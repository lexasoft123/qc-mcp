"""Offline checks for expression-pedal assignment (no device needed).

The QC has no per-parameter MIDI CC mapping; a parameter is driven over MIDI by
assigning it to Expression Pedal 1 (CC#1) or 2 (CC#2). The encoding was decoded
from a preset that already carried assignments: Param.expression is 0/1/2, with
expression_min/max bounding the swept range in normalized units. This test pins
the wire message assign_expression emits.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from qc_mcp.transport import QuadCortex    # noqa: E402


def _capture(**kw):
    qc = QuadCortex(bridge=False)
    sent = {}
    qc.send = lambda cmd, msg: sent.update(cmd=cmd, msg=msg)
    qc.assign_expression(**kw)
    return sent


def test_assign_builds_grid_update_with_expression():
    sent = _capture(row=0, column=3, param_index=0,
                    expression=2, expr_min=0.1, expr_max=0.9)
    assert sent["cmd"] == "Grid"
    g = sent["msg"]
    ch = g.preset.chains[0]
    assert ch.row == 0
    m = ch.models[0]
    assert m.column == 3
    p = m.params[0]
    assert p.index == 0
    assert p.expression == 2
    assert abs(p.expression_min - 0.1) < 1e-6
    assert abs(p.expression_max - 0.9) < 1e-6
    # only the expression fields are sent — no param_values, so the device merge
    # preserves the param's existing value/scenes.
    assert len(p.param_values) == 0


def test_clear_sends_expression_zero():
    sent = _capture(row=3, column=2, param_index=3, expression=0)
    p = sent["msg"].preset.chains[0].models[0].params[0]
    assert p.expression == 0


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
            passed += 1
    print(f"\n{passed}/{passed} passed")
