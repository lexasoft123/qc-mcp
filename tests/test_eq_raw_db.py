"""Parametric EQ band gains are stored as raw dB (measured on CorOS 4.1.1)."""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from qc_mcp import catalog

PARAMETRIC = [(4001, i) for i in (0, 5, 10)] + [(4000, i) for i in range(0, 40, 5)] + [(4004, i) for i in range(0, 25, 5)]


def test_gain_is_identity_within_range():
    for h, i in PARAMETRIC:
        assert catalog.display_range(h, i) == (-12.0, 12.0)
        for db in (-12, -7.5, 0, 0.5, 1.0, 3, 6, 11.9, 12):
            assert math.isclose(catalog.to_norm(h, i, db), db, abs_tol=1e-9)
            assert math.isclose(catalog.to_display(h, i, db), db, abs_tol=1e-9)


def test_six_db_is_not_capped_at_one():
    assert catalog.to_norm(4001, 5, 6.0) == 6.0


def test_gain_clamps_to_twelve():
    for db, want in [(13, 12.0), (40, 12.0), (-20, -12.0)]:
        assert catalog.to_norm(4001, 0, db) == want


def test_freq_and_q_still_normalized():
    assert 0.0 <= catalog.to_norm(4001, 1, 1000) <= 1.0      # 1 FREQ
    assert catalog.raw_db_range(4001, 1) is None


def test_graphic9_bands_stay_normalized():
    # literal -12..12 bounds, stored 0-1 (readbacks like 4.0 dB = 0.6667)
    assert catalog.raw_db_range(4005, 1) is None
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
