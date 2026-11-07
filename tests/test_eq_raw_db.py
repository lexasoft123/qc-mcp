"""Parametric EQ band gains are stored as raw dB (measured on CorOS 4.1.1)."""
import pytest
from qc_mcp import catalog

PARAMETRIC = [(4001, i) for i in (0, 5, 10)] + [(4000, i) for i in range(0, 40, 5)] + [(4004, i) for i in range(0, 25, 5)]


@pytest.mark.parametrize("h,i", PARAMETRIC)
def test_gain_is_identity_within_range(h, i):
    assert catalog.display_range(h, i) == (-12.0, 12.0)
    for db in (-12, -7.5, 0, 0.5, 1.0, 3, 6, 11.9, 12):
        assert catalog.to_norm(h, i, db) == pytest.approx(db)
        assert catalog.to_display(h, i, db) == pytest.approx(db)


def test_six_db_is_not_capped_at_one():
    assert catalog.to_norm(4001, 5, 6.0) == 6.0


@pytest.mark.parametrize("db,want", [(13, 12.0), (40, 12.0), (-20, -12.0)])
def test_gain_clamps_to_twelve(db, want):
    assert catalog.to_norm(4001, 0, db) == want


def test_freq_and_q_still_normalized():
    assert 0.0 <= catalog.to_norm(4001, 1, 1000) <= 1.0      # 1 FREQ
    assert catalog.raw_db_range(4001, 1) is None


def test_graphic9_bands_stay_normalized():
    # literal -12..12 bounds, stored 0-1 (readbacks like 4.0 dB = 0.6667)
    assert catalog.raw_db_range(4005, 1) is None
    assert catalog.to_norm(4005, 1, 4.0) == pytest.approx(16 / 24)
    assert catalog.to_display(4005, 1, 16 / 24) == pytest.approx(4.0)
