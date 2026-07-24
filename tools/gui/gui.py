#!/usr/bin/env python3
"""GUI-automation harness for Cortex Control.

Lets Claude drive the official app for scenario testing:
  * locate the window          (CGWindowList  — no permission needed)
  * screenshot the window       (screencapture — needs Screen Recording)
  * click / type / press keys   (cliclick      — needs Accessibility)
  * mark + decode the protocol log emitted by the DYLD interposer, so every
    GUI action can be correlated with the on-the-wire messages it produced.

Coordinate model
----------------
CGWindowList reports the window bounds in *points* (e.g. 1346x987). On a Retina
display screencapture writes the region at the backing scale (2x -> 2692x1974
pixels). So when Claude reads a screenshot and picks a target at PIXEL (px,py),
the screen POINT to click is:

    screen_x = win.x + px / scale
    screen_y = win.y + py / scale         scale = png_px_width / win_point_width

`click` takes screenshot-pixel coords and does this conversion automatically, so
Claude always reasons in the pixel space of the image it just looked at.

Usage
-----
    python3 tools/gui/gui.py bounds
    python3 tools/gui/gui.py shot [out.png]
    python3 tools/gui/gui.py click <px> <py>
    python3 tools/gui/gui.py type "Man of Mystery"
    python3 tools/gui/gui.py key return
    python3 tools/gui/gui.py logmark            # prints current OUT/IN msg counts
    python3 tools/gui/gui.py act <px> <py> [label]   # mark -> click -> shot -> decode
"""
import json
import os
import subprocess
import sys
import time

# pyobjc (Quartz) lives in the project venv; re-exec there if we're not in it.
try:
    import Quartz  # noqa: F401
except ImportError:
    _venv = os.path.join(os.path.dirname(__file__), "..", "..", ".venv", "bin", "python")
    if os.path.exists(_venv) and not os.environ.get("_QC_GUI_REEXEC"):
        os.environ["_QC_GUI_REEXEC"] = "1"
        os.execv(_venv, [_venv, *sys.argv])

APP = os.environ.get("QC_GUI_APP", "Cortex Control")
LOG = os.environ.get("QC_HID_LOG", os.path.join(os.path.dirname(__file__), "..", "..", "interceptor", "hid_log.txt"))
SHOT = os.environ.get("QC_GUI_SHOT", "/tmp/cc.png")

# make qc_mcp importable for log decoding
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


# --------------------------------------------------------------------------- window
def window_bounds(app=APP):
    """Window bounds in screen points. Works with no special permission."""
    import Quartz
    opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    best = None
    for w in Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID):
        owner = w.get("kCGWindowOwnerName", "") or ""
        if app.lower() in owner.lower():
            b = w["kCGWindowBounds"]
            cand = dict(x=int(b["X"]), y=int(b["Y"]), w=int(b["Width"]), h=int(b["Height"]),
                        id=int(w["kCGWindowNumber"]), layer=int(w.get("kCGWindowLayer", 0)))
            # prefer the largest layer-0 (main) window
            if best is None or (cand["layer"] == 0 and cand["w"] * cand["h"] > best["w"] * best["h"]):
                best = cand
    return best


def _main_display_bounds():
    import Quartz
    b = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
    return dict(x=int(b.origin.x), y=int(b.origin.y), w=int(b.size.width), h=int(b.size.height))


def move_to_main(x=180, y=80, app=APP):
    """Move the app window onto the main (built-in Retina) display via the
    Accessibility API. Clicks only map reliably on the main display: screencapture
    samples at the main display's 2x scale, so a window parked on a 1x external
    monitor makes capture-pixels and click-points disagree. Re-home before driving."""
    import Quartz
    from ApplicationServices import (
        AXUIElementCreateApplication, AXUIElementCopyAttributeValue,
        AXUIElementSetAttributeValue, AXValueCreate, kAXValueCGPointType,
    )
    pid = None
    for w in Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID):
        if app.lower() in (w.get("kCGWindowOwnerName", "") or "").lower():
            pid = w["kCGWindowOwnerPID"]
            break
    if pid is None:
        raise RuntimeError(f"{app!r} not found")
    axapp = AXUIElementCreateApplication(pid)
    err, wins = AXUIElementCopyAttributeValue(axapp, "AXWindows", None)
    if not wins:
        raise RuntimeError("no AX windows (Accessibility permission?)")
    AXUIElementSetAttributeValue(wins[0], "AXPosition",
                                 AXValueCreate(kAXValueCGPointType, Quartz.CGPoint(x, y)))
    return window_bounds(app)


def _on_main(b):
    m = _main_display_bounds()
    return m["x"] <= b["x"] < m["x"] + m["w"] and m["y"] <= b["y"] < m["y"] + m["h"]


def _png_size(path):
    out = subprocess.check_output(["sips", "-g", "pixelWidth", "-g", "pixelHeight", path], text=True)
    w = h = None
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("pixelWidth:"):
            w = int(line.split(":")[1])
        elif line.startswith("pixelHeight:"):
            h = int(line.split(":")[1])
    return w, h


# --------------------------------------------------------------------------- capture
def screenshot(path=SHOT, app=APP):
    b = window_bounds(app)
    if not b:
        raise RuntimeError(f"{app!r} window not found")
    if not _on_main(b):
        print(f"WARNING: {app!r} window is off the main display (origin {b['x']},{b['y']}); "
              f"clicks will mismap. Run: python3 tools/gui/gui.py home", file=sys.stderr)
    subprocess.run(["screencapture", "-x", "-o", "-R",
                    f"{b['x']},{b['y']},{b['w']},{b['h']}", path], check=True)
    pw, ph = _png_size(path)
    scale = pw / b["w"]
    return {"path": path, "bounds": b, "scale": scale, "px": [pw, ph]}


# --------------------------------------------------------------------------- input
def _px_to_point(px, py, info):
    b, s = info["bounds"], info["scale"]
    return b["x"] + px / s, b["y"] + py / s


def click(px, py, info=None, double=False, app=APP):
    """Click at screenshot-pixel coords (auto-converts to screen points)."""
    info = info or screenshot(app=app)
    x, y = _px_to_point(px, py, info)
    verb = "dc" if double else "c"
    subprocess.run(["cliclick", f"{verb}:{int(round(x))},{int(round(y))}"], check=True)
    return {"clicked_point": [round(x, 1), round(y, 1)], "from_px": [px, py]}


def type_text(t):
    subprocess.run(["cliclick", "-w", "20", f"t:{t}"], check=True)


def key(name):
    subprocess.run(["cliclick", f"kp:{name}"], check=True)


# --------------------------------------------------------------------------- log correlation
def _log_counts(path=LOG):
    """Cheap mark: byte offset + line count of the interposer log."""
    try:
        st = os.stat(path)
        with open(path, "rb") as f:
            lines = sum(1 for _ in f)
        return {"bytes": st.st_size, "lines": lines}
    except FileNotFoundError:
        return {"bytes": 0, "lines": 0}


def log_since(mark, path=LOG):
    """Return raw log text appended since a mark (from _log_counts)."""
    try:
        with open(path, "rb") as f:
            f.seek(mark.get("bytes", 0))
            return f.read().decode("utf-8", "replace")
    except FileNotFoundError:
        return ""


def decode_delta(text):
    """Reassemble chunked reports in a log delta and name each message's command.
    Returns a list of {dir, cmd, cmd_name, len, gzip, head} dicts — the protocol
    footprint of whatever GUI action produced this delta."""
    import struct as _s
    from qc_mcp.protocol import COMMANDS
    buf = {"OUT": b"", "IN": b""}
    out = []
    for line in text.splitlines():
        if " id=" not in line or " len=" not in line:
            continue
        try:
            _ts, d, *_rest, hexs = line.split()
            b = bytes.fromhex(hexs)
        except ValueError:
            continue
        d = d.strip()
        if d not in buf or len(b) < 3:
            continue
        chunk_len, flags = b[1], b[2]
        if flags & 0x40:
            buf[d] = b""
        buf[d] += b[3:3 + chunk_len]
        if flags & 0x80:
            m = buf[d]
            buf[d] = b""
            if len(m) < 8:
                continue
            cmd = _s.unpack("<H", m[-8:-6])[0]
            gz = m[:3] == b"\x1f\x8b\x08"
            out.append({"dir": d, "cmd": cmd, "cmd_name": COMMANDS.get(cmd, f"?{cmd}"),
                        "len": len(m), "gzip": gz, "head": m[:24].hex()})
    return out


# --------------------------------------------------------------------------- high level
def act(px, py, label="", app=APP):
    """mark log -> click -> settle -> screenshot -> return log delta."""
    mark = _log_counts()
    before = screenshot(app=app)
    res = click(px, py, info=before)
    time.sleep(0.6)  # let the app emit its messages
    after = screenshot(app=app)
    delta = log_since(mark)
    msgs = decode_delta(delta)
    return {"label": label, "click": res, "shot": after["path"],
            "log_new_bytes": len(delta), "messages": msgs}


# --------------------------------------------------------------------------- cli
def main(argv):
    if not argv:
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "bounds":
        print(json.dumps(window_bounds(), indent=2))
    elif cmd == "home":
        x = int(rest[0]) if len(rest) > 0 else 180
        y = int(rest[1]) if len(rest) > 1 else 80
        print(json.dumps(move_to_main(x, y), indent=2))
    elif cmd == "shot":
        out = rest[0] if rest else SHOT
        print(json.dumps(screenshot(out), indent=2))
    elif cmd == "click":
        print(json.dumps(click(int(rest[0]), int(rest[1]), double="--double" in rest), indent=2))
    elif cmd == "type":
        type_text(rest[0])
    elif cmd == "key":
        key(rest[0])
    elif cmd == "logmark":
        print(json.dumps(_log_counts(), indent=2))
    elif cmd == "decode":
        # decode last N bytes of the log (default 20000) into named commands
        n = int(rest[0]) if rest else 20000
        st = os.stat(LOG)
        print(json.dumps(decode_delta(log_since({"bytes": max(0, st.st_size - n)})), indent=2))
    elif cmd == "act":
        px, py = int(rest[0]), int(rest[1])
        label = rest[2] if len(rest) > 2 else ""
        print(json.dumps(act(px, py, label), indent=2))
    else:
        print(f"unknown command {cmd!r}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
