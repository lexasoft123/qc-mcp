#!/usr/bin/env python3
"""End-to-end prompt -> signal-chain tests for the QC MCP.

Each case pairs a natural-language PROMPT with the chain the MCP is expected to build
for it (this file encodes the intended prompt->chain interpretation). The harness builds
each chain on the device via the transport, reads the grid back, and asserts the
realized blocks match the expected categories in order — so a regression in catalog
resolution, block placement, capture loading, or read-back is caught.

Read-back uses get_current_preset, which is reliable in DIRECT mode (Cortex Control
quit). In bridge mode read-back returns empty, so run this with CC quit:
    QC_BRIDGE=0 python3 tools/gui/e2e_test.py [case_name]

Non-persistent: it clears + rebuilds the current grid per case and never saves. Note the
active preset is left on the last case's chain; recall a preset afterward to restore.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
from qc_mcp import catalog  # noqa: E402
from qc_mcp.transport import QuadCortex  # noqa: E402


def F(query, category):
    """Resolve a block by catalog search -> (hash, name, category). First hit."""
    res = catalog.find(query or None, category or None)
    if not res:
        raise LookupError(f"no catalog model for query={query!r} category={category!r}")
    m = res[0]
    return m["id"], m["name"], m["category"]


# Each block: ("amp"/"cap", query, category[, capture_key, capture_name])
CASES = [
    {
        "name": "clean_fender_ts_hall",
        "prompt": "Clean Fender tone, tube-screamer boost, touch of hall reverb",
        "chain": [("blk", "808", "Guitar Overdrive"),
                  ("blk", "US DLX Normal", "Guitar Amplifier"),
                  ("blk", "", "Cabsim Guitar (M)"),
                  ("blk", "Hall", "Reverb")],
        "expect": ["Guitar Overdrive", "Guitar Amplifier", "Cabsim Guitar (M)", "Reverb"],
    },
    {
        "name": "metal_gate_mesa_cab",
        "prompt": "High-gain metal rhythm: noise gate, Mesa amp, tight 4x12",
        "chain": [("blk", "Gate", "Utility"),
                  ("blk", "CA", "Guitar Amplifier"),
                  ("blk", "412", "Cabsim Guitar (M)")],
        "expect": ["Utility", "Guitar Amplifier", "Cabsim Guitar (M)"],
    },
    {
        "name": "srv_blues_spring",
        "prompt": "SRV blues: Tube Screamer into a blackface amp with spring reverb",
        "chain": [("blk", "808", "Guitar Overdrive"),
                  ("blk", "US TWN", "Guitar Amplifier"),
                  ("blk", "", "Cabsim Guitar (M)"),
                  ("blk", "Spring", "Reverb")],
        "expect": ["Guitar Overdrive", "Guitar Amplifier", "Cabsim Guitar (M)", "Reverb"],
    },
    {
        "name": "ambient_comp_delay_verb",
        "prompt": "Ambient: compressor, clean amp, delay, big reverb",
        "chain": [("blk", "", "Compressor"),
                  ("blk", "US DLX Normal", "Guitar Amplifier"),
                  ("blk", "", "Cabsim Guitar (M)"),
                  ("blk", "Digital", "Delay"),
                  ("blk", "Hall", "Reverb")],
        "expect": ["Compressor", "Guitar Amplifier", "Cabsim Guitar (M)", "Delay", "Reverb"],
    },
    {
        "name": "capture_as_amp",
        # 'CA John's 2' is a factory capture (present on every QC); key is universal.
        "prompt": "Use the 'CA John's 2' factory capture as the amp, TS in front, then a cab",
        "chain": [("blk", "808", "Guitar Overdrive"),
                  ("cap", "d7591effff1ba7d85bb458c5615a1c305a587957a4e28fff68a0df0d45a9809f",
                   "CA John's 2"),
                  ("blk", "", "Cabsim Guitar (M)")],
        "expect": ["Guitar Overdrive", "Neural Capture", "Cabsim Guitar (M)"],
    },
]


def categories_in_order(bp):
    """Realized block categories by grid column (row 0)."""
    cats = []
    for ch in bp.chains:
        if getattr(ch, "row", 0) != 0:
            continue
        for m in sorted(ch.models, key=lambda x: x.column):
            if not m.hash:
                continue
            info = catalog.lookup(m.hash) or {}
            cats.append(info.get("category", f"?{m.hash}"))
    return cats


def run_case(qc, case):
    qc.clear_grid()
    time.sleep(0.3)
    built = []
    for col, block in enumerate(case["chain"]):
        kind = block[0]
        if kind == "cap":
            _, key, nm = block
            qc.set_capture(0, col, key, nm, version="v1")
            built.append(("Neural Capture", nm))
        else:
            _, q, cat = block
            h, nm, c = F(q, cat)
            qc.add_block(h, row=0, column=col)
            built.append((c, nm))
        time.sleep(0.15)
    time.sleep(0.6)
    bp = qc.get_current_preset(timeout_ms=5000)
    if not bp:
        return {"name": case["name"], "ok": None, "note": "read-back empty (run in DIRECT mode: QC_BRIDGE=0)",
                "built": built}
    got = categories_in_order(bp)
    exp = case["expect"]
    ok = got == exp
    return {"name": case["name"], "ok": ok, "expected": exp, "got": got, "built": built}


def main(argv):
    only = argv[0] if argv else None
    cases = [c for c in CASES if not only or c["name"] == only]
    bridge = os.environ.get("QC_BRIDGE") != "0" and os.path.exists("/tmp/qc_inject")
    qc = QuadCortex(bridge=bridge).open(handshake=not bridge)
    print(f"mode: {'BRIDGE' if bridge else 'DIRECT'}\n")
    results = []
    try:
        for c in cases:
            print(f"[{c['name']}] {c['prompt']!r}")
            r = run_case(qc, c)
            results.append(r)
            mark = {True: "PASS", False: "FAIL", None: "SKIP"}[r["ok"]]
            print(f"   built: {[b[1] for b in r['built']]}")
            if r["ok"] is None:
                print(f"   {mark}: {r['note']}")
            else:
                print(f"   expected: {r['expected']}")
                print(f"   got:      {r['got']}")
                print(f"   {mark}")
            print()
    finally:
        qc.close()
    npass = sum(1 for r in results if r["ok"] is True)
    print(f"=== {npass}/{len(results)} passed ===")
    return 0 if npass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
