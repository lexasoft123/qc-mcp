"""Offline checks for setlist bank/slot labels (no device needed).

The Quad Cortex Mini (device_type 'ATMA') banks presets in 4s to match its four
footswitches, so position 9 shows as 3B on the unit; the full Quad Cortex banks
in 8s, making the same position 2B. This once shipped hardcoded to 8, so a Mini
preset saved at position 9 was reported as "2B" while the device showed "3B".
Recall positions are the 0-based index either way — only the label differs.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from qc_mcp.server import _bank_label    # noqa: E402


def test_mini_banks_in_fours():
    # ATMA = Quad Cortex Mini: 4 slots (A-D) per bank.
    assert _bank_label(0, "ATMA") == "1A"
    assert _bank_label(3, "ATMA") == "1D"
    assert _bank_label(4, "ATMA") == "2A"
    assert _bank_label(9, "ATMA") == "3B"   # the bug: was reported "2B"
    assert _bank_label(11, "ATMA") == "3D"


def test_full_qc_banks_in_eights():
    assert _bank_label(0, "QC") == "1A"
    assert _bank_label(7, "QC") == "1H"
    assert _bank_label(8, "QC") == "2A"
    assert _bank_label(9, "QC") == "2B"


def test_unknown_device_defaults_to_eights():
    # No/blank device type must not regress the full-QC labelling.
    assert _bank_label(9, None) == "2B"
    assert _bank_label(9, "") == "2B"


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
            passed += 1
    print(f"\n{passed}/{passed} passed")
