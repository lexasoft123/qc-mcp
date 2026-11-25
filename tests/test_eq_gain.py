"""Parametric EQ band gains (MIN_EQ_DB..MAX_EQ_DB) are stored 0-1 like every other
parameter: -12..+12 dB linear, 0 dB at 0.5 (measured on CorOS 4.1.1)."""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from qc_mcp import catalog

PARAMETRIC = [(4001, i) for i in (0, 5, 10)] + [(4000, i) for i in range(0, 40, 5)] + [(4004, i) for i in range(0, 25, 5)]


def test_gain_is_linear_over_twelve_db():
    for h, i in PARAMETRIC:
        assert catalog.display_range(h, i) == (-12.0, 12.0)
        for db, nv in [(-12, 0.0), (-6, 0.25), (0, 0.5), (6, 0.75), (12, 1.0)]:
            assert math.isclose(catalog.to_norm(h, i, db), nv, abs_tol=1e-9)
            assert math.isclose(catalog.to_display(h, i, nv), db, abs_tol=1e-9)


def test_cortex_control_readings():
    # stored values and the dB Cortex Control shows for them
    for nv, shown in [(0.4417, -1.4), (0.2901, -5.0)]:
        assert round(catalog.to_display(4001, 0, nv), 1) == shown


def test_zero_db_is_the_middle():
    # sent raw, 0 dB would be 0.0: a -12 dB cut on the device
    assert catalog.to_norm(4001, 5, 0.0) == 0.5


def test_gain_clamps_to_the_range():
    for db, want in [(13, 1.0), (40, 1.0), (-20, 0.0)]:
        assert catalog.to_norm(4001, 0, db) == want


def test_freq_and_q_still_normalized():
    assert 0.0 <= catalog.to_norm(4001, 1, 1000) <= 1.0      # 1 FREQ


def test_graphic9_bands_stay_normalized():
    # literal -12..12 bounds, stored 0-1 (readbacks like 4.0 dB = 0.6667)
    assert math.isclose(catalog.to_norm(4005, 1, 4.0), 16 / 24)
    assert math.isclose(catalog.to_display(4005, 1, 16 / 24), 4.0)


if __name__ == "__main__":
    import traceback
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    ok = 0
    for name, fn in fns:
        try:
            fn()
            print("PASS", name)
            ok += 1
        except Exception:
            print("FAIL", name)
            traceback.print_exc()
    print(f"{ok}/{len(fns)} passed")
    sys.exit(0 if ok == len(fns) else 1)
