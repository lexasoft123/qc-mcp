#!/usr/bin/env python3
"""Validate transport.assign_expression round-trips on hardware. Non-destructive:
assigns a currently-unassigned param to Exp 1, reads it back, then clears it. Never
saves. Run with the MCP disconnected (direct mode). Operates on whatever preset is in
the working grid.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from qc_mcp.transport import QuadCortex          # noqa: E402
from qc_mcp.preset import describe               # noqa: E402


def expr_at(qc, row, col, pos):
    d = describe(qc.get_current_preset())
    for ch in d.get("chains", []):
        if ch.get("row") != row:
            continue
        for m in ch.get("models", []):
            if m.get("column") == col:
                params = m.get("params", [])
                if pos < len(params):
                    return params[pos].get("expression", 0)
    return None


qc = QuadCortex(bridge=False).open(handshake=True)
try:
    d = describe(qc.get_current_preset())
    # find the first populated block and a param position currently unassigned
    target = None
    for ch in d.get("chains", []):
        for m in ch.get("models", []):
            if m.get("hash"):
                for pos, p in enumerate(m.get("params", [])):
                    if not p.get("expression"):
                        target = (ch["row"], m["column"], pos, m["hash"])
                        break
            if target:
                break
        if target:
            break
    if not target:
        print("no unassigned param found"); sys.exit(1)
    row, col, pos, h = target
    print(f"target: row{row} col{col} hash={h} param_pos={pos}")
    print(f"before: expression={expr_at(qc, row, col, pos)}")

    qc.assign_expression(row, col, pos, expression=1)
    time.sleep(0.6)
    after = expr_at(qc, row, col, pos)
    print(f"after assign exp1: expression={after}  -> {'PASS' if after == 1 else 'FAIL'}")

    qc.assign_expression(row, col, pos, expression=0)
    time.sleep(0.6)
    cleared = expr_at(qc, row, col, pos)
    print(f"after clear: expression={cleared}  -> {'PASS' if cleared == 0 else 'FAIL'}")
finally:
    qc.close()
