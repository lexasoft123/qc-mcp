#!/usr/bin/env python3
"""Mine the interposer HID log for messages of a given command, decode the
protobuf, and print fields with timestamps. Non-destructive: reverses operations
from already-captured history instead of triggering them live.

    python3 tools/gui/mine_log.py list                 # non-telemetry event timeline
    python3 tools/gui/mine_log.py <Command> [n] [--in|--out]  # decode n examples
    python3 tools/gui/mine_log.py <Command> --raw      # hex instead of decoded

Examples:
    python3 tools/gui/mine_log.py RecallPreset 3 --out
    python3 tools/gui/mine_log.py File 2 --in
"""
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
try:
    import qc_mcp.protocol as P
except ImportError:
    _venv = os.path.join(os.path.dirname(__file__), "..", "..", ".venv", "bin", "python")
    if os.path.exists(_venv) and not os.environ.get("_QC_MINE_REEXEC"):
        os.environ["_QC_MINE_REEXEC"] = "1"
        os.execv(_venv, [_venv, *sys.argv])
    raise

LOG = os.environ.get("QC_HID_LOG", os.path.join(os.path.dirname(__file__), "..", "..", "interceptor", "hid_log.txt"))
NOISE = {"KeepAlive", "CPULoad", "GlobalTempo", "IOMeter", "Tuner", "GridModelMeter"}


def messages(log=LOG):
    """Yield (ts, dir, cmd_id, cmd_name, proto_bytes, gzip_flag) per reassembled msg."""
    buf = {"OUT": b"", "IN": b""}
    for line in open(log, encoding="utf-8", errors="replace"):
        if " id=" not in line or " len=" not in line:
            continue
        p = line.split()
        try:
            ts = float(p[0]); d = p[1]; b = bytes.fromhex(p[-1])
        except ValueError:
            continue
        if d not in buf or len(b) < 3:
            continue
        cl, fl = b[1], b[2]
        if fl & 0x40:
            buf[d] = b""
        buf[d] += b[3:3 + cl]
        if fl & 0x80:
            m = buf[d]; buf[d] = b""
            if len(m) < 8:
                continue
            cmd = struct.unpack("<H", m[-8:-6])[0]
            proto = m[:-8]
            gz = proto[:3] == b"\x1f\x8b\x08"
            if gz:
                un = P.gunzip(proto)
                if un is not None:
                    proto = un
            yield ts, d, cmd, P.COMMANDS.get(cmd, f"?{cmd}"), proto, gz


def decode(name, proto):
    try:
        mc = P.message_class(name)
        msg = mc()
        msg.ParseFromString(proto)
        return str(msg)
    except Exception as e:
        return f"<decode failed: {e}>\n  hex[:80]={proto[:80].hex()}"


def main(argv):
    if not argv:
        print(__doc__); return 0
    if argv[0] == "list":
        t0 = None
        for ts, d, cid, name, proto, gz in messages():
            if name in NOISE:
                continue
            if t0 is None:
                t0 = ts
            print(f"  +{ts - t0:8.2f}s  {d:3} {name:22} {len(proto):6}B{' gz' if gz else ''}")
        return 0
    name = argv[0]
    n = next((int(a) for a in argv[1:] if a.isdigit()), 3)
    want = "OUT" if "--out" in argv else ("IN" if "--in" in argv else None)
    raw = "--raw" in argv
    t0 = None
    shown = 0
    for ts, d, cid, mname, proto, gz in messages():
        if t0 is None:
            t0 = ts
        if mname != name or (want and d != want):
            continue
        print(f"\n--- {mname} {d} +{ts - t0:.2f}s  {len(proto)}B{' gz' if gz else ''} (cmd={cid}) ---")
        print(proto[:200].hex() if raw else decode(mname, proto))
        shown += 1
        if shown >= n:
            break
    if not shown:
        print(f"no {name} {want or ''} messages found")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
