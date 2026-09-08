"""Offline tests for the looper-style reference sampler (no audio hardware).

Fakes `sounddevice` so the state machine can be driven block by block: arm ->
auto-start on the first note -> auto-stop after silence -> trim -> save.
Run: .venv/bin/python tests/test_sampler.py
"""
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

try:
    import numpy as np
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

RATE = 48000
BLOCK = 1024


class Skip(Exception):
    pass


class _CallbackStop(Exception):
    """Stand-in for sounddevice.CallbackStop."""


class _FakeStream:
    """Captures the callback so the test can pump blocks through it by hand."""

    def __init__(self, **kw):
        self.kw = kw
        self.callback = kw.get("callback")
        self.finished_callback = kw.get("finished_callback")
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True

    def pump(self, block):
        """Feed one buffer; returns False once the stream has stopped itself."""
        try:
            self.callback(block, block.shape[0], None, None)
            return True
        except _CallbackStop:
            if self.finished_callback:
                self.finished_callback()
            return False


def _install_fakes(monkey):
    """Put a fake sounddevice + device lookup in place; returns the fake module."""
    from qc_mcp import audio_io
    fake_sd = types.SimpleNamespace(
        CallbackStop=_CallbackStop,
        InputStream=lambda **kw: monkey.setdefault("stream", _FakeStream(**kw)),
    )
    audio_io._sd = lambda: fake_sd
    audio_io.find_device = lambda name_hint=None: {
        "index": 0, "name": "Quad Cortex", "inputs": 8, "outputs": 8,
        "default_rate": RATE}
    return fake_sd


def _tone(frames=BLOCK, amp=0.5, channels=2):
    return (np.ones((frames, channels), dtype="float32") * amp)


def _quiet(frames=BLOCK, channels=2):
    return np.full((frames, channels), 1e-5, dtype="float32")


def _fresh_sampler(tmpdir):
    from qc_mcp import audio_io
    monkey = {}
    _install_fakes(monkey)
    audio_io._sampler = None
    s = audio_io.sampler()
    return s, monkey


def _tmp():
    import tempfile
    return os.path.join(tempfile.mkdtemp(prefix="qcsample"), "ref.wav")


def _need_numpy():
    if not HAVE_NUMPY:
        raise Skip("numpy not installed")


# --------------------------------------------------------------------- tests ----
def test_arm_waits_and_does_not_record_quiet_blocks():
    _need_numpy()
    s, mk = _fresh_sampler(None)
    s.arm(threshold_dbfs=-40.0)
    assert s.state == s.ARMED, s.state
    for _ in range(5):
        mk["stream"].pump(_quiet())
    assert s.state == s.ARMED, "quiet must not start the take"
    assert s.status()["seconds_recorded"] == 0.0, s.status()


def test_first_note_above_threshold_starts_recording():
    _need_numpy()
    s, mk = _fresh_sampler(None)
    s.arm(threshold_dbfs=-40.0)
    mk["stream"].pump(_quiet())
    mk["stream"].pump(_tone())
    assert s.state == s.RECORDING, s.state
    assert s.status()["seconds_recorded"] > 0, s.status()


def test_silence_auto_stops_the_take():
    _need_numpy()
    s, mk = _fresh_sampler(None)
    s.arm(threshold_dbfs=-40.0, silence_seconds=0.1)   # 0.1 s ~= 5 blocks
    mk["stream"].pump(_tone())
    alive = True
    for _ in range(40):
        if not alive:
            break
        alive = mk["stream"].pump(_quiet())
    assert alive is False, "stream should have stopped itself"
    assert s.state == s.DONE, s.state


def test_max_seconds_caps_the_take():
    _need_numpy()
    s, mk = _fresh_sampler(None)
    max_s = 10 * BLOCK / float(RATE)
    s.arm(threshold_dbfs=-40.0, max_seconds=max_s, silence_seconds=99.0)
    alive = True
    pumped = 0
    while alive and pumped < 200:
        alive = mk["stream"].pump(_tone())
        pumped += 1
    assert alive is False, "max_seconds should have stopped it"
    assert pumped <= 12, "stopped after %d blocks, expected ~10" % pumped


def test_stop_trims_saves_and_reports():
    _need_numpy()
    s, mk = _fresh_sampler(None)
    s.arm(threshold_dbfs=-40.0)
    for _ in range(3):
        mk["stream"].pump(_quiet())
    for _ in range(20):
        mk["stream"].pump(_tone(amp=0.25))
    for _ in range(3):
        mk["stream"].pump(_quiet())
    path = _tmp()
    try:
        res = s.stop(path=path)
    except Exception as e:
        if "soundfile" in str(e):
            raise Skip("soundfile not installed")
        raise
    assert res["state"] == "done", res
    assert os.path.exists(res["path"]), res
    assert res["duration_s"] > 0, res
    assert res["peak_dbfs"] is not None, res
    # the three quiet blocks that arrived after the tone must be trimmed off
    assert res["trimmed_tail_s"] >= 0.0, res
    expected = 20 * BLOCK / float(RATE)
    assert res["duration_s"] <= expected + 0.01, (res["duration_s"], expected)


def test_stop_with_nothing_recorded_reports_an_error():
    _need_numpy()
    s, mk = _fresh_sampler(None)
    s.arm(threshold_dbfs=-40.0)
    for _ in range(3):
        mk["stream"].pump(_quiet())
    res = s.stop(path=_tmp())
    assert "error" in res, res
    assert "threshold" in res["error"], res
    assert s.state == s.IDLE, s.state


def test_discard_resets_to_idle():
    _need_numpy()
    s, mk = _fresh_sampler(None)
    s.arm(threshold_dbfs=-40.0)
    mk["stream"].pump(_tone())
    assert s.state == s.RECORDING
    out = s.discard()
    assert out["state"] == "idle", out
    assert s.status()["seconds_recorded"] == 0.0, s.status()


def test_arming_twice_is_a_no_op_not_a_second_stream():
    _need_numpy()
    s, mk = _fresh_sampler(None)
    s.arm(threshold_dbfs=-40.0)
    first = mk["stream"]
    s.arm(threshold_dbfs=-40.0)
    assert mk["stream"] is first, "must not open a second input stream"


def test_status_reports_a_live_input_level():
    _need_numpy()
    s, mk = _fresh_sampler(None)
    s.arm(threshold_dbfs=-40.0)
    mk["stream"].pump(_tone(amp=0.5))
    st = s.status()
    assert st["input_dbfs"] is not None, st
    assert abs(st["input_dbfs"] - (-6.02)) < 0.1, st["input_dbfs"]


def test_sampler_records_the_dry_di_pair_by_default():
    """The riff must be the raw instrument, not something a preset already coloured."""
    _need_numpy()
    s, mk = _fresh_sampler(None)
    s.arm()
    import inspect
    from qc_mcp import audio_io
    sig = inspect.signature(audio_io.Sampler.arm)
    assert sig.parameters["channels"].default == (1, 2), sig.parameters["channels"]


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    ok = skipped = 0
    for name, fn in fns:
        try:
            fn()
            print("  ok   %s" % name)
            ok += 1
        except Skip as e:
            print("  skip %s (%s)" % (name, e))
            skipped += 1
        except AssertionError as e:
            print("  FAIL %s: %s" % (name, e))
        except Exception as e:
            print("  ERR  %s: %s: %s" % (name, type(e).__name__, e))
    print("%d passed, %d skipped, %d failed" % (ok, skipped, len(fns) - ok - skipped))
    sys.exit(0 if ok + skipped == len(fns) else 1)
