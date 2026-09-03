"""Offline tests for the IOMeter output_meter tool (no device needed).

Builds real IOMeterMessage protobufs from the bundled descriptor pool and feeds them
through a fake transport that mimics QuadCortex.latest_broadcast's contract.
Run: .venv/bin/python tests/test_meters.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from qc_mcp import protocol as P  # noqa: E402
from qc_mcp import server  # noqa: E402


def _meter(**fields):
    msg = P.message_class(P.NAME_TO_CMD["IOMeter"])()
    for k, v in fields.items():
        setattr(msg, k, v)
    return msg


class _FakeQC:
    """Stands in for QuadCortex: latest_broadcast returns a scripted list."""

    def __init__(self, msgs):
        self.msgs = msgs
        self.calls = []

    def latest_broadcast(self, command, hold_s=1.0, settle_s=0.25):
        self.calls.append((command, hold_s))
        return self.msgs


def _with_qc(msgs):
    fake = _FakeQC(msgs)
    server._conn = lambda: fake          # noqa: E731  (test double)
    return fake


def test_peak_hold_takes_max_and_last():
    fake = _with_qc([
        _meter(xlr_1=0.20, xlr_2=0.10),
        _meter(xlr_1=0.90, xlr_2=0.30),   # the transient
        _meter(xlr_1=0.40, xlr_2=0.20),
    ])
    out = server.output_meter(hold_s=0.5)
    assert out["samples"] == 3, out
    assert out["ports"]["xlr_1"]["peak"] == 0.9, out["ports"]["xlr_1"]
    assert out["ports"]["xlr_1"]["last"] == 0.4, out["ports"]["xlr_1"]
    assert fake.calls == [("IOMeter", 0.5)], fake.calls


def test_limiter_flag_latches_across_the_window():
    _with_qc([
        _meter(xlr_1=0.5, xlr_1_limiter=0.0),
        _meter(xlr_1=0.99, xlr_1_limiter=1.0),   # engaged for one frame only
        _meter(xlr_1=0.5, xlr_1_limiter=0.0),
    ])
    out = server.output_meter()
    assert out["limiters"]["xlr_1"] is True, out["limiters"]
    assert out["limiters"]["xlr_2"] is False, out["limiters"]
    assert out["any_limiting"] is True, out


def test_headphones_have_one_shared_limiter_flag():
    """The proto carries hp_limiter_active (bool) for both channels, not one each."""
    _with_qc([_meter(hp_l=0.3, hp_r=0.3, hp_limiter_active=True)])
    out = server.output_meter()
    assert out["limiters"]["hp"] is True, out["limiters"]
    assert "hp_l" not in out["limiters"], "headphones must not get per-channel flags"
    assert "hp_r" not in out["limiters"], "headphones must not get per-channel flags"
    assert "hp_l" in out["ports"] and "hp_r" in out["ports"], out["ports"]


def test_levels_convert_from_linear_amplitude_to_db():
    """IOMeter sends linear amplitude 0..1; the QC's own readout is -40..+12 dB."""
    _with_qc([_meter(xlr_1=1.0), _meter(xlr_1=0.5), _meter(xlr_1=0.1)])
    out = server.output_meter()
    assert out["ports"]["xlr_1"]["peak"] == 1.0, out["ports"]["xlr_1"]
    assert out["ports"]["xlr_1"]["peak_db"] == 0.0, out["ports"]["xlr_1"]
    assert out["ports"]["xlr_1"]["last_db"] == -20.0, out["ports"]["xlr_1"]
    assert out["scale"]["db_floor"] == -40.0, out["scale"]


def test_half_amplitude_is_minus_six_db():
    _with_qc([_meter(xlr_1=0.5)])
    assert abs(server.output_meter()["ports"]["xlr_1"]["peak_db"] - (-6.0)) < 0.05


def test_silence_clamps_to_the_meter_floor_not_minus_infinity():
    _with_qc([_meter(xlr_1=0.0)])
    out = server.output_meter()
    assert out["ports"]["xlr_1"]["peak_db"] is None, out["ports"]["xlr_1"]
    _with_qc([_meter(xlr_1=1e-9)])
    assert server.output_meter()["ports"]["xlr_1"]["peak_db"] == -40.0


def test_quiet_rig_reports_no_limiting():
    _with_qc([_meter(xlr_1=0.0, xlr_2=0.0)])
    out = server.output_meter()
    assert out["any_limiting"] is False, out
    assert out["ports"]["xlr_1"]["peak"] == 0.0, out["ports"]["xlr_1"]


def test_detail_adds_grid_and_usb_channels():
    _with_qc([_meter(grid_xlr_1=0.7, usb_output_3l=0.25, usb_input_1l=0.11)])
    plain = server.output_meter()
    assert "grid" not in plain and "usb_out" not in plain, plain.keys()
    out = server.output_meter(detail=True)
    assert out["grid"]["grid_xlr_1"]["peak"] == 0.7, out["grid"]
    assert out["usb_out"]["usb_output_3l"]["peak"] == 0.25, out["usb_out"]
    assert out["usb_in"]["usb_input_1l"]["peak"] == 0.11, out["usb_in"]
    # the USB tables must cover all 8 channels each, L and R
    assert len(out["usb_out"]) == 8 and len(out["usb_in"]) == 8, out["usb_out"].keys()


def test_empty_stream_returns_a_hint_not_a_crash():
    _with_qc([])
    out = server.output_meter()
    assert "note" in out and "hint" in out, out
    assert "ports" not in out, "must not report zeros as if they were measurements"


def test_every_declared_field_exists_on_the_real_proto():
    """Guards against typos in _METER_* against the bundled descriptor."""
    msg = P.message_class(P.NAME_TO_CMD["IOMeter"])()
    declared = [f.name for f in msg.DESCRIPTOR.fields]
    for label, lvl, lim in server._METER_PORTS:
        assert lvl in declared, "unknown level field %r" % lvl
        if lim:
            assert lim in declared, "unknown limiter field %r" % lim
    for f in server._METER_GRID + server._METER_USB_OUT + server._METER_USB_IN:
        assert f in declared, "unknown field %r" % f
    assert "hp_limiter_active" in declared


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
