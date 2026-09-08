#!/usr/bin/env python3
"""RE oracle: dump the per-param `expression` fields of a preset that already has
expression-pedal assignments, to learn the encoding (which integer = Exp 1 vs Exp 2,
and the expr_min/max convention). Read-only.

Usage: .venv/bin/python tools/dump_expression.py [position]
Default position 5 = "Vocal 58 Pitch" in My Presets (X1->MODE, X2->ROOT).
Run with the MCP server disconnected (direct mode needs exclusive USB).
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from qc_mcp.transport import QuadCortex          # noqa: E402
from qc_mcp.preset import describe               # noqa: E402

FOLDER = "/media/p4/Presets/My Presets"
pos = int(sys.argv[1]) if len(sys.argv) > 1 else 5

qc = QuadCortex(bridge=False).open(handshake=True)
try:
    print(f"device_type={qc.device_type} firmware={qc.firmware}")
    if pos >= 0:
        qc.recall(folder_key=FOLDER, position=pos)
        time.sleep(2.0)
    bp = qc.get_current_preset()
    if bp is None:
        print("no preset read"); sys.exit(1)
    d = describe(bp)
    print(f"preset name={d.get('name')!r}  position={pos}")
    print("blocks loaded:")
    for ch in d.get("chains", []):
        for m in ch.get("models", []):
            print(f"  row{ch.get('row')} col{m.get('column')} hash={m.get('hash')}")
    print("\n-- full param detail for Pitch Correction (18013) and any expr-bearing param --")
    for ch in d.get("chains", []):
        for m in ch.get("models", []):
            for p in m.get("params", []):
                expr = p.get("expression", 0)
                emin = p.get("expr_min", 0.0)
                emax = p.get("expr_max", 0.0)
                if m.get("hash") == 18013 or expr or emin not in (0.0,) or emax not in (0.0, 1.0):
                    print(f"  row{ch.get('row')} col{m.get('column')} hash={m.get('hash')} "
                          f"idx={p.get('index')}: expression={expr} "
                          f"expr_min={emin} expr_max={emax} scene_mode={p.get('scene_mode')} "
                          f"values={p.get('values')}")
    # Also dump the raw protobuf field names present on a Param, to catch any
    # expression-related field describe() might not surface.
    print("\n-- raw Param field names (schema) --")
    from qc_mcp import protocol as P
    Param = P.message_class("Grid")().preset.chains.add().models.add().params.add()
    print("  ", [f.name for f in Param.DESCRIPTOR.fields])
finally:
    qc.close()
