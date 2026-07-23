#!/usr/bin/env python3
"""Load every preset in a folder (via the bridge, sharing Cortex Control's session)
and check that our scripts fully understand each one — flagging any block model,
routing, or config our MCP decoder doesn't handle.

Read-only w.r.t. data (never saves); it does change the *active* preset as it steps
through. Usage: python3 tools/gui/sweep_presets.py [folder_key] [max]
Default folder = cloud-0-1 (Downloads).
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))
from mine_log import messages  # noqa: E402
import qc_mcp.protocol as P  # noqa: E402
from qc_mcp import catalog  # noqa: E402
from qc_mcp.transport import QuadCortex  # noqa: E402
from qc_mcp import server as S  # noqa: E402

CAPTURE_HASHES = {14000, 14001}


def folder_files(folder_key):
    """Ordered list of (name, cloud_id) in a folder, from the most complete captured
    File message. Downloads/cloud presets load by cloud_id; factory/user by position."""
    best = []
    for ts, d, cid, name, proto, gz in messages():
        if name != "File" or d != "IN":
            continue
        try:
            m = P.message_class("File")(); m.ParseFromString(proto)
        except Exception:
            continue
        if not m.HasField("folder") or m.folder.key != folder_key:
            continue
        entries = [(f.name, getattr(f, "cloud_id", "")) for f in m.folder.files]
        if len(entries) > len(best):
            best = entries
    return best


def analyze(bp):
    """Return (features, gaps) for one BinaryPreset."""
    feats = {"blocks": 0, "unknown_hashes": set(), "categories": set(),
             "captures": 0, "rows": set(), "splits": 0, "mixers": 0}
    gaps = []
    for ci, ch in enumerate(bp.chains):
        feats["rows"].add(getattr(ch, "row", ci))
        scp = getattr(ch, "split_control_points", None)
        try:
            for cp in (scp or []):
                vals = [getattr(cp, f.name) for f in cp.DESCRIPTOR.fields]
                if any(v not in (-1, 0) for v in vals):
                    feats["splits"] += 1
                    break
        except TypeError:
            pass
        for m in ch.models:
            if not m.hash:
                continue
            feats["blocks"] += 1
            if m.hash in CAPTURE_HASHES:
                feats["captures"] += 1
            info = catalog.lookup(m.hash)
            if not info:
                feats["unknown_hashes"].add(m.hash)
            else:
                feats["categories"].add(info.get("category", "?"))
                if "mixer" in info.get("category", "").lower() or "splitter" in info.get("name", "").lower():
                    feats["mixers"] += 1
    # top-level configs that may not be wired into the MCP
    for fld, label in [("midi_messages", "midi"), ("midi_messages_general", "midi"),
                       ("stomp_mode_assignments", "stomp-assign"),
                       ("bypass", "bypass-map"), ("side_chain_follow_exists", "side-chain")]:
        try:
            v = getattr(bp, fld)
            present = (len(v) if hasattr(v, "__len__") else bool(v)) if v is not None else False
            if present:
                feats.setdefault("extra_configs", set()).add(label)
        except Exception:
            pass
    # does the MCP summary decode without error?
    try:
        S._preset_summary(bp)
    except Exception as e:
        gaps.append(f"_preset_summary raised: {type(e).__name__}: {e}")
    if feats["unknown_hashes"]:
        gaps.append(f"unknown model hashes: {sorted(feats['unknown_hashes'])}")
    return feats, gaps


def recall_and_capture(qc, name, folder, pos, cloud_id, secs=3.5):
    """Load a preset and return the full BinaryPreset the device pushes back."""
    want = P.NAME_TO_CMD["RecallPreset"]
    qc._pending.clear()
    if cloud_id:
        qc.recall(downloads_key=cloud_id)
    else:
        qc.recall(folder, pos, is_factory=folder.startswith("/opt/"))
    best = None
    deadline = time.time() + secs
    while time.time() < deadline:
        qc._collect(0.15)
        for cmd, obj, raw, pb in list(qc._pending):
            if cmd == want and obj is not None and obj.HasField("preset") \
                    and sum(1 for ch in obj.preset.chains for m in ch.models if m.hash):
                best = obj.preset
            qc._pending.remove((cmd, obj, raw, pb))
        if best:
            break
    return best


def main(argv):
    folder = argv[0] if argv else "cloud-0-1"
    files = folder_files(folder)
    limit = int(argv[1]) if len(argv) > 1 else len(files)
    idxs = list(range(len(files)))[:limit]
    print(f"sweeping {len(idxs)} presets in {folder!r} ({len(files)} total)\n")
    qc = QuadCortex(bridge=True).open()
    agg_unknown = set(); agg_cats = set(); agg_extra = set()
    all_gaps = []; loaded = 0; capture_presets = []
    try:
        skipped_empty = 0
        for idx in idxs:
            nm, cloud_id = files[idx]
            if not nm.strip():          # empty/clear slot — nothing to sweep
                skipped_empty += 1
                continue
            # only cloud/download folders load by cloud_id; factory/user by position
            key = cloud_id if folder.startswith("cloud-") else ""
            bp = recall_and_capture(qc, nm, folder, idx, key)
            if not bp:
                all_gaps.append((nm, ["no grid pushed (load slow / cloud capture pending?)"]))
                print(f"  [{idx:2}] {nm:34} -- NO GRID")
                continue
            loaded += 1
            feats, gaps = analyze(bp)
            agg_unknown |= feats["unknown_hashes"]; agg_cats |= feats["categories"]
            agg_extra |= feats.get("extra_configs", set())
            if feats["captures"]:
                capture_presets.append(nm)
            tag = "  ".join(filter(None, [
                f"{feats['blocks']}blk", f"{len(feats['rows'])}row",
                f"{feats['splits']}split" if feats["splits"] else "",
                f"{feats['captures']}cap" if feats["captures"] else "",
                "GAP" if gaps else ""]))
            print(f"  [{idx:2}] {nm:34} {tag}")
            if gaps:
                all_gaps.append((nm, gaps))
                for g in gaps:
                    print(f"        !! {g}")
    finally:
        qc.close()
    print(f"\n=== SUMMARY: {loaded} loaded, {skipped_empty} empty slots skipped ===")
    print("categories seen:", sorted(agg_cats))
    print("unknown model hashes (NOT in catalog):", sorted(agg_unknown) or "NONE")
    print("top-level configs present (verify MCP handles):", sorted(agg_extra) or "none")
    print("presets using captures:", capture_presets or "none")
    print(f"presets with gaps: {len(all_gaps)}")
    for nm, gaps in all_gaps:
        print(f"  - {nm}: {'; '.join(gaps)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
