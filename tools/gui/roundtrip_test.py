#!/usr/bin/env python3
"""Deep round-trip fidelity tests over REAL complex presets.

For each curated corner-case preset it: loads the golden preset from the device, runs it
through the MCP's model (`describe` -> `build`), and DEEP-DIFFS every field of golden vs
rebuilt — not just which blocks exist, but their parameter values (per scene), routing
(in/out portids per lane), splitters/mixers, cab/mic settings, reverb settings, volume,
pan, scenes and the bypass map. Any dropped or altered field is reported, proving whether
the MCP can faithfully represent complicated presets (the basis for building from prompts).

    python3 tools/gui/roundtrip_test.py [name-substring]
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))
from mine_log import messages  # noqa: E402
import qc_mcp.protocol as P  # noqa: E402
from qc_mcp import preset, catalog  # noqa: E402
from qc_mcp.transport import QuadCortex  # noqa: E402

# Curated corner cases: (name, folder_key, what it exercises). folder '' = Downloads.
CORNER_CASES = [
    ("Reamp Example",   "/opt/neuraldsp/Factory Library", "4 parallel USB lanes, in/out blocks"),
    ("9 Cable Method",  "/opt/neuraldsp/Factory Library", "splits + 3 captures + FX routing"),
    ("Eric Johnson",    "/opt/neuraldsp/Factory Library", "23 blocks, split, 2 captures"),
    ("Multi Amp Pleasure", "/opt/neuraldsp/Factory Library", "multi-amp mixer topology"),
    ("Wet Dry Wet",     "/opt/neuraldsp/Factory Library", "parallel wet/dry + mixer + reverbs"),
    ("Double Cabs",     "/opt/neuraldsp/Factory Library", "stereo/dual cabs (mic settings)"),
    ("Snakehair Bassmix 1", "/opt/neuraldsp/Factory Library", "bass amps/cabs, different in/out"),
    ("Patch Send1 to Ret1", "/opt/neuraldsp/Factory Library", "FX loop send/return routing"),
    ("Sultans of Swing Dire Straits", "", "Downloads preset w/ factory capture"),
]


def _listing(folder_key):
    best = []
    for ts, d, cid, name, proto, gz in messages():
        if name != "File" or d != "IN":
            continue
        try:
            m = P.message_class("File")(); m.ParseFromString(proto)
        except Exception:
            continue
        if m.HasField("folder") and m.folder.key == folder_key:
            ent = [(f.name, getattr(f, "cloud_id", "")) for f in m.folder.files]
            if len(ent) > len(best):
                best = ent
    return best


def resolve(name, folder_key):
    """Return (folder_key, position, cloud_id) for a preset by name substring."""
    fk = folder_key or "cloud-0-1"
    for pos, (nm, cid) in enumerate(_listing(fk)):
        if name.lower() in nm.lower():
            return fk, pos, cid
    return None


def load_golden(qc, name, folder_key):
    r = resolve(name, folder_key)
    if not r:
        return None
    fk, pos, cid = r
    qc._pending.clear()
    if folder_key == "":
        qc.recall(downloads_key=cid)
    else:
        qc.recall(fk, pos, is_factory=fk.startswith("/opt/"))
    want = P.NAME_TO_CMD["RecallPreset"]
    deadline = time.time() + 4.0
    G = None
    while time.time() < deadline and G is None:
        qc._collect(0.15)
        for cmd, obj, raw, pb in list(qc._pending):
            if cmd == want and obj and obj.HasField("preset") \
                    and sum(1 for ch in obj.preset.chains for mm in ch.models if mm.hash):
                G = obj.preset
            qc._pending.remove((cmd, obj, raw, pb))
    return G


def _pval(pv):
    if pv.HasField("string_value"):
        return pv.string_value
    if pv.HasField("int_value"):
        return pv.int_value
    return round(pv.float_value, 5)


def _model(m):
    return {"hash": m.hash,
            "params": [{"i": p.index, "sm": p.scene_mode,
                        "expr": p.expression,
                        "emin": round(p.expression_min, 5),
                        "emax": round(p.expression_max, 5),
                        "v": [_pval(x) for x in p.param_values]} for p in m.params]}


def full_structure(bp):
    """Deep structural dump covering every field the tests must check."""
    lanes = []
    for ch in bp.chains:
        lanes.append({
            "row": ch.row, "in": ch.in_portid, "out": ch.out_portid,
            "splits": [[s.split, s.mix] for s in ch.split_control_points],
            "splitter": [_model(m) for m in ch.splitter],
            "mixer": [_model(m) for m in ch.mixer],
            "combined_splitter": [_model(m) for m in ch.combined_splitter],
            "models": [_model(m) for m in ch.models if m.hash],
        })
    return {
        "volume": round(bp.volume, 5), "pan": round(bp.pan, 5),
        "default_scene": bp.default_scene,
        "scene_labels": list(bp.scene_labels),
        "scene_colors": list(bp.scene_colors),
        "lanes": lanes,
        "bypass": [{"row": b.row,
                    "cols": [{"c": cb.column, "sm": cb.sceneMode,
                              "sb": [x.bypass for x in cb.sceneBypass]}
                             for cb in b.colBypass]} for b in bp.bypass],
    }


def deep_diff(a, b, path=""):
    diffs = []
    if type(a) != type(b):
        return [f"{path}: type {type(a).__name__} != {type(b).__name__}"]
    if isinstance(a, dict):
        for k in set(a) | set(b):
            if k not in a:
                diffs.append(f"{path}.{k}: missing in golden")
            elif k not in b:
                diffs.append(f"{path}.{k}: missing in rebuilt")
            else:
                diffs += deep_diff(a[k], b[k], f"{path}.{k}")
    elif isinstance(a, list):
        if len(a) != len(b):
            diffs.append(f"{path}: len {len(a)} != {len(b)}")
        else:
            for i, (x, y) in enumerate(zip(a, b)):
                diffs += deep_diff(x, y, f"{path}[{i}]")
    else:
        if a != b:
            # NaN != NaN by IEEE rules, but a preserved NaN is a faithful round-trip
            if isinstance(a, float) and isinstance(b, float) and a != a and b != b:
                pass
            else:
                diffs.append(f"{path}: {a!r} != {b!r}")
    return diffs


def device_rebuild(qc, G):
    """Rebuild the golden onto the DEVICE via apply_spec, then read the grid back —
    exercising the full wire path (Grid edits incl. string params)."""
    qc.clear_grid()
    time.sleep(0.6)
    qc.apply_spec(preset.describe(G), per_scene=True)
    time.sleep(1.2)
    return qc.get_current_preset(timeout_ms=6000)


def main(argv):
    device = "--device" in argv
    pos = [a for a in argv if not a.startswith("--")]
    only = pos[0].lower() if pos else None
    cases = [c for c in CORNER_CASES if not only or only in c[0].lower()]
    qc = QuadCortex(bridge=True).open()
    print(f"mode: {'DEVICE rebuild (apply_spec -> read back)' if device else 'OFFLINE model (describe -> build)'}\n")
    results = []
    try:
        for name, folder, note in cases:
            G = load_golden(qc, name, folder)
            if not G:
                print(f"[{name}] -- could not load (not in captured listing)\n")
                results.append((name, None)); continue
            n_blocks = sum(1 for ch in G.chains for m in ch.models if m.hash)
            R = device_rebuild(qc, G) if device else preset.build(preset.describe(G))
            if R is None:
                print(f"[{name}] -- device read-back empty\n")
                results.append((name, None)); continue
            # device rebuild only reproduces the grid (lanes), not preset metadata
            sg, sr = full_structure(G), full_structure(R)
            if device:
                sg, sr = {"lanes": sg["lanes"]}, {"lanes": sr["lanes"]}
            diffs = deep_diff(sg, sr, name)
            ok = not diffs
            results.append((name, ok))
            print(f"[{name}] {note}")
            print(f"   {len(G.chains)} lanes, {n_blocks} blocks -> {'PASS (identical)' if ok else f'{len(diffs)} DIFFS'}")
            for d in diffs[:12]:
                print(f"      ! {d}")
            print()
    finally:
        qc.close()
    npass = sum(1 for _, ok in results if ok)
    total = sum(1 for _, ok in results if ok is not None)
    print(f"=== {npass}/{total} round-trips identical ===")
    return 0 if npass == total else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
