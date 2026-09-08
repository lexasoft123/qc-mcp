"""Offline tests for the skew-aware parameter taper (no device needed).

ModelRepo declares a JUCE-style `skew` on 773 params. Before this, display<->normalized
conversion ignored it, so writing "+6.0 dB" to a Gain block landed somewhere else.
Run: .venv/bin/python tests/test_catalog_skew.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from qc_mcp import catalog  # noqa: E402

GAIN = 16005          # "Gain" utility block; param 0 = LEVEL, -60..+12 dB, skew 3.8018
LEVEL = 0


def test_gain_level_round_trips_at_every_landmark():
    for db in (-60.0, -24.0, -12.0, -6.0, 0.0, 6.0, 12.0):
        nv = catalog.to_norm(GAIN, LEVEL, db)
        back = catalog.to_display(GAIN, LEVEL, nv)
        assert abs(back - db) < 1e-6, "%.1f dB round-tripped to %.6f" % (db, back)


def test_unity_gain_sits_at_the_centre_detent():
    """The whole reason to trust the JUCE reading: skew 3.8018 puts 0 dB at norm 0.5."""
    nv = catalog.to_norm(GAIN, LEVEL, 0.0)
    assert abs(nv - 0.5) < 1e-5, "0 dB -> %.6f, expected 0.5" % nv


def test_endpoints_are_exact():
    assert abs(catalog.to_norm(GAIN, LEVEL, -60.0) - 0.0) < 1e-9
    assert abs(catalog.to_norm(GAIN, LEVEL, 12.0) - 1.0) < 1e-9


def test_skew_is_not_the_old_linear_mapping():
    """Guards against a silent regression back to the pre-skew behaviour."""
    nv = catalog.to_norm(GAIN, LEVEL, 0.0)
    linear = (0.0 - -60.0) / (12.0 - -60.0)          # 0.8333, what it used to return
    assert abs(nv - linear) > 0.3, "taper looks linear again (%.4f)" % nv


def test_curve_is_monotonic():
    prev = -1.0
    db = -60.0
    while db <= 12.0:
        nv = catalog.to_norm(GAIN, LEVEL, db)
        assert nv >= prev, "not monotonic at %.1f dB" % db
        prev = nv
        db += 0.5


def test_out_of_range_values_clamp_rather_than_explode():
    assert catalog.to_norm(GAIN, LEVEL, -200.0) == 0.0
    assert catalog.to_norm(GAIN, LEVEL, 200.0) == 1.0
    for nv in (-0.5, 1.5):
        v = catalog.to_display(GAIN, LEVEL, nv)
        assert -60.0 <= v <= 12.0, "display %.3f escaped the range" % v


def test_symbolic_skew_is_ignored():
    """LIN_SKEW / LOG_SKEW are not numbers; _skew must fall through, not raise."""
    found = False
    for mid, m in catalog._catalog().items():
        for i, p in enumerate(m["params"]):
            sk = p.get("skew")
            if sk in ("LIN_SKEW", "LOG_SKEW"):
                assert catalog._skew(mid, i) is None, (mid, i, sk)
                catalog.to_norm(mid, i, 0.0)          # must not raise
                found = True
                break
        if found:
            break
    assert found, "no symbolic skew found in ModelRepo — test needs updating"


def test_params_without_skew_keep_the_old_heuristic():
    """A param with no skew attribute must behave exactly as before."""
    for mid, m in catalog._catalog().items():
        for i, p in enumerate(m["params"]):
            if p.get("skew") is not None:
                continue
            rng = catalog._prange(mid, i)
            if not rng:
                continue
            lo, hi = rng
            if hi <= lo:
                continue
            assert catalog._skew(mid, i) is None
            mid_val = lo + (hi - lo) * 0.5
            nv = catalog.to_norm(mid, i, mid_val)
            expect = 0.5 ** (1.0 / catalog.LOG_TAPER) if catalog._is_log(lo, hi) else 0.5
            assert abs(nv - expect) < 1e-9, (mid, i, nv, expect)
            return
    raise AssertionError("no skew-less numeric param found")


def test_every_numeric_skew_in_the_catalog_round_trips():
    """Sweep the whole catalog: no skew value may produce a broken conversion."""
    checked = 0
    for mid, m in catalog._catalog().items():
        for i, p in enumerate(m["params"]):
            if catalog._skew(mid, i) is None:
                continue
            rng = catalog._prange(mid, i)
            if not rng:
                continue
            lo, hi = rng
            if hi <= lo:
                continue
            for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
                v = lo + (hi - lo) * frac
                back = catalog.to_display(mid, i, catalog.to_norm(mid, i, v))
                tol = max(1e-6, abs(hi - lo) * 1e-9)
                assert abs(back - v) < tol, (mid, m["name"], i, v, back)
            checked += 1
    assert checked > 500, "expected hundreds of skewed params, saw %d" % checked


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    ok = 0
    for name, fn in fns:
        try:
            fn()
            print("  ok   %s" % name)
            ok += 1
        except AssertionError as e:
            print("  FAIL %s: %s" % (name, e))
        except Exception as e:
            print("  ERR  %s: %s: %s" % (name, type(e).__name__, e))
    print("%d/%d passed" % (ok, len(fns)))
    sys.exit(0 if ok == len(fns) else 1)
