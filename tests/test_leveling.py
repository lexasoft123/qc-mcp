"""Offline tests for the closed leveling loop (no device, no audio).

A fake QuadCortex records every routing/param write; `leveling.measure` is replaced by a
scripted sequence, so convergence, clamping, the true-peak guard and — most importantly —
routing restoration can all be proven without hardware.
Run: .venv/bin/python tests/test_leveling.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from qc_mcp import leveling  # noqa: E402


class _Model:
    def __init__(self, hash_=0):
        self.hash = hash_
        self.params = []


class _Chain:
    def __init__(self, in_port=1, out_port=19, models=None):
        self.in_portid = in_port
        self.out_portid = out_port
        self.models = models or [_Model(12345), _Model(0), _Model(0)]


class _Preset:
    def __init__(self, chains):
        self.chains = chains


class FakeQC:
    """Records what the loop does to the device."""

    def __init__(self, in_port=1, out_port=19, gain_col=None):
        models = [_Model(12345), _Model(0), _Model(0)]
        if gain_col is not None:
            models[gain_col] = _Model(leveling.GAIN_HASH)
        self.chain = _Chain(in_port, out_port, models)
        self.routing_calls = []
        self.param_writes = []
        self.scene_writes = []
        self.scenes_set = []
        self.added = []
        self.fail_scene = set()

    def get_current_preset(self, timeout_ms=6000):
        return _Preset([self.chain])

    def set_routing(self, row, in_portid=None, out_portid=None):
        self.routing_calls.append((row, in_portid, out_portid))
        if in_portid is not None:
            self.chain.in_portid = in_portid
        if out_portid is not None:
            self.chain.out_portid = out_portid
        return True

    def add_block(self, model_hash, row=0, column=0, wait_echo_ms=800):
        self.added.append((model_hash, row, column))
        while len(self.chain.models) <= column:
            self.chain.models.append(_Model(0))
        self.chain.models[column] = _Model(model_hash)
        return True

    def set_param(self, row, column, param_index, value):
        self.param_writes.append((row, column, param_index, value))
        return True

    def set_param_scenes(self, row, column, param_index, values):
        self.scene_writes.append((row, column, param_index, list(values)))
        return True

    def set_scene(self, scene):
        self.scenes_set.append(scene)
        return scene not in self.fail_scene

    def _await_scene(self, scene, timeout_s=2.0):
        return scene not in self.fail_scene


def script(*results):
    """Replace leveling.measure with a fixed sequence of measurement dicts."""
    seq = list(results)
    calls = {"n": 0}

    def fake(di_path=None, perceived=False, in_channels=None, out_channels=None):
        calls["n"] += 1
        return seq.pop(0) if seq else seq_last[0]

    seq_last = [results[-1]]
    leveling.measure = fake
    return calls


def m(lufs, tp=-6.0):
    return {"lufs_integrated": lufs, "true_peak_dbtp": tp, "sample_peak_dbfs": -8.0,
            "silent": False}


# --------------------------------------------------------------- the loop ----
def test_converges_and_reports_each_iteration():
    qc = FakeQC()
    script(m(-24.8), m(-18.6), m(-18.1))
    out = leveling.level_current(qc, target=-18.0, tolerance=0.5)
    assert out["converged"] is True, out
    assert len(out["iterations"]) == 3, out["iterations"]
    assert out["iterations"][0]["delta_db"] == 6.8, out["iterations"][0]
    assert abs(out["final_delta_db"]) <= 0.5, out


def test_already_on_target_writes_nothing():
    qc = FakeQC()
    script(m(-18.2))
    out = leveling.level_current(qc, target=-18.0, tolerance=0.5)
    assert out["converged"] is True, out
    assert out["written"] is False, out
    assert qc.param_writes == [], qc.param_writes
    assert qc.added == [], "must not add a Gain block when no trim is needed"


def test_dry_run_measures_but_never_writes():
    qc = FakeQC()
    script(m(-24.8))
    out = leveling.level_current(qc, target=-18.0, dry_run=True)
    assert out["suggested_db"] == 6.8, out
    assert out["written"] is False, out
    assert qc.param_writes == [] and qc.added == [], (qc.param_writes, qc.added)


def test_routing_is_swapped_in_and_restored():
    qc = FakeQC(in_port=1, out_port=19)
    script(m(-18.1))
    leveling.level_current(qc, target=-18.0)
    assert qc.routing_calls[0] == (0, leveling.IN_PORT_USB_5_6,
                                   leveling.OUT_PORT_USB_5_6), qc.routing_calls
    assert qc.routing_calls[-1] == (0, 1, 19), qc.routing_calls
    assert qc.chain.in_portid == 1 and qc.chain.out_portid == 19


def test_routing_is_restored_even_when_measurement_raises():
    qc = FakeQC(in_port=1, out_port=19)

    def boom(**kw):
        raise RuntimeError("device fell over")
    leveling.measure = boom
    try:
        leveling.level_current(qc, target=-18.0)
    except RuntimeError:
        pass
    assert qc.chain.in_portid == 1 and qc.chain.out_portid == 19, \
        "routing must be restored on failure"
    assert qc.routing_calls[-1] == (0, 1, 19), qc.routing_calls


def test_silent_capture_aborts_before_writing():
    qc = FakeQC()
    script({"silent": True, "error": "capture is digital silence ... microphone"})
    out = leveling.level_current(qc, target=-18.0)
    assert "error" in out and "silence" in out["error"], out
    assert out["written"] is False, out
    assert qc.param_writes == [], "must never trim against a silent capture"


def test_true_peak_guard_backs_the_trim_off():
    qc = FakeQC()
    # wants +9 dB, but after the first trim the true peak is over the ceiling
    script(m(-27.0, tp=-8.0), m(-19.0, tp=-0.2), m(-19.5, tp=-1.5))
    out = leveling.level_current(qc, target=-18.0, tolerance=0.5)
    steps = out["iterations"]
    assert any(s.get("limited_by") == "true_peak" for s in steps), steps
    assert out.get("limited") is True, out
    backed = [s for s in steps if "backed_off_to_db" in s][0]
    assert backed["backed_off_to_db"] < backed["wrote_db"], backed


def test_trim_is_clamped_to_the_gain_block_range():
    qc = FakeQC()
    script(m(-70.0), m(-60.0), m(-58.0), m(-57.0), m(-57.0), m(-57.0))
    out = leveling.level_current(qc, target=-18.0, max_iterations=3)
    from qc_mcp import catalog
    for _row, _col, _idx, value in qc.param_writes:
        db = catalog.to_display(leveling.GAIN_HASH, leveling.GAIN_LEVEL, value)
        assert leveling.GAIN_MIN_DB - 1e-6 <= db <= leveling.GAIN_MAX_DB + 1e-6, db
    assert out["written"] is True


def test_gain_block_is_added_once_and_reused():
    qc = FakeQC()
    script(m(-24.8), m(-18.1))
    out = leveling.level_current(qc, target=-18.0)
    assert out["trim_block"]["added"] is True, out["trim_block"]
    assert len(qc.added) == 1, qc.added
    assert qc.added[0][0] == leveling.GAIN_HASH

    script(m(-24.8), m(-18.1))
    out2 = leveling.level_current(qc, target=-18.0)
    assert out2["trim_block"]["added"] is False, out2["trim_block"]
    assert len(qc.added) == 1, "must reuse the existing Gain block"


def test_writes_go_to_the_gain_block_not_the_amp():
    qc = FakeQC()
    script(m(-24.8), m(-18.1))
    leveling.level_current(qc, target=-18.0)
    gain_col = leveling.find_gain_block(qc, 0)
    for row, col, idx, _v in qc.param_writes:
        assert col == gain_col, "wrote to column %d, not the Gain block" % col
        assert idx == leveling.GAIN_LEVEL, idx


def test_perceived_metric_uses_a_ratio_correction():
    qc = FakeQC()
    r = {"zwicker_n5_rel": 10.0, "true_peak_dbtp": -6.0, "silent": False,
         "lufs_integrated": -20.0}
    script(r)
    out = leveling.level_current(qc, target=20.0, metric="perceived", dry_run=True)
    # doubling loudness in sones is +3.01 dB on a 10*log10 ratio scale
    assert abs(out["suggested_db"] - 3.01) < 0.02, out


# ------------------------------------------------------------------ scenes ----
def test_scene_leveling_writes_all_eight_values_once():
    qc = FakeQC()
    script(m(-24.9), m(-23.2), m(-21.5), m(-25.4),
           m(-24.0), m(-24.0), m(-24.0), m(-24.0))
    out = leveling.level_scenes(qc, scenes=[0, 1, 2, 3], target=-18.0)
    assert out["written"] is True, out
    assert len(qc.scene_writes) == 1, "per-scene values must be written in one pass"
    _row, _col, idx, values = qc.scene_writes[0]
    assert idx == leveling.GAIN_LEVEL and len(values) == 8, (idx, len(values))


def test_unconfirmed_scene_switch_is_skipped_not_written():
    qc = FakeQC()
    qc.fail_scene = {2}
    script(m(-24.9), m(-23.2), m(-25.4))
    out = leveling.level_scenes(qc, scenes=[0, 1, 2], target=-18.0)
    skipped = [s for s in out["scenes"] if s.get("skipped")]
    assert any(s["scene"] == 2 for s in skipped), out["scenes"]
    assert "not confirmed" in skipped[0]["skipped"], skipped


def test_scene_run_returns_to_scene_zero():
    qc = FakeQC()
    script(m(-24.9), m(-23.2))
    leveling.level_scenes(qc, scenes=[0, 1], target=-18.0)
    assert qc.scenes_set[-1] == 0, qc.scenes_set


def test_scene_dry_run_writes_nothing():
    qc = FakeQC()
    script(m(-24.9), m(-23.2))
    out = leveling.level_scenes(qc, scenes=[0, 1], target=-18.0, dry_run=True)
    assert out["written"] is False, out
    assert qc.scene_writes == [] and qc.added == [], (qc.scene_writes, qc.added)


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    ok = 0
    real_measure = leveling.measure
    for name, fn in fns:
        leveling.measure = real_measure
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
