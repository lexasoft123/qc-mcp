"""Offline tests for the loudness core and the audio-channel safety rules.

Synthetic signals only — no device, no audio hardware. Skips cleanly when the optional
audio extra is not installed, so the base venv can still run the suite.
Run: .venv/bin/python tests/test_loudness.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from qc_mcp import loudness  # noqa: E402

try:
    import numpy as np
    HAVE_NUMPY = True
except ImportError:
    HAVE_NUMPY = False

try:
    import pyloudnorm  # noqa: F401
    HAVE_LOUDNESS = True
except ImportError:
    HAVE_LOUDNESS = False

RATE = 48000


class Skip(Exception):
    pass


def _need(flag, what):
    if not flag:
        raise Skip("%s not installed" % what)


def _sine(seconds=5.0, freq=1000.0, amp_dbfs=-20.0, rate=RATE, channels=1):
    amp = 10.0 ** (amp_dbfs / 20.0)
    t = np.arange(int(seconds * rate)) / float(rate)
    x = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float64)
    return np.tile(x.reshape(-1, 1), (1, channels))


# ------------------------------------------------------------------ metrics ----
def test_1khz_at_minus20_dbfs_is_minus23_lufs():
    """The anchor value: K-weighting is ~-3 dB at 1 kHz, so -20 dBFS -> -23 LUFS.

    Measured on this machine at 3.14/arm64 as -23.05, so pin +/-0.2.
    """
    _need(HAVE_LOUDNESS, "pyloudnorm")
    out = loudness.analyze(_sine(), RATE)
    assert out["lufs_integrated"] is not None, out
    assert abs(out["lufs_integrated"] - (-23.0)) < 0.2, out["lufs_integrated"]


def test_doubling_amplitude_raises_loudness_by_6_lu():
    _need(HAVE_LOUDNESS, "pyloudnorm")
    quiet = loudness.analyze(_sine(amp_dbfs=-26.0), RATE)["lufs_integrated"]
    loud = loudness.analyze(_sine(amp_dbfs=-20.0), RATE)["lufs_integrated"]
    assert abs((loud - quiet) - 6.0) < 0.1, (quiet, loud)


def test_true_peak_is_at_least_the_sample_peak():
    _need(HAVE_NUMPY, "numpy")
    x = _sine(seconds=1.0, freq=997.0, amp_dbfs=-1.0)
    tp = loudness.true_peak_dbtp(x, RATE)
    sample_peak = 20 * np.log10(float(np.max(np.abs(x))))
    assert tp is not None and tp >= sample_peak - 0.01, (tp, sample_peak)


def test_rms_of_a_sine_is_3db_below_its_peak():
    _need(HAVE_NUMPY, "numpy")
    r = loudness.rms_dbfs(_sine(amp_dbfs=-20.0))
    assert abs(r - (-23.01)) < 0.05, r


# -------------------------------------------------------------------- gates ----
def test_digital_silence_is_flagged_not_measured():
    """The macOS denied-microphone signature must never look like a quiet preset."""
    _need(HAVE_NUMPY, "numpy")
    out = loudness.analyze(np.zeros((RATE * 2, 2)), RATE)
    assert out["silent"] is True, out
    assert "error" in out and "microphone" in out["error"], out
    assert "lufs_integrated" not in out, "must not report a level for silence"


def test_below_the_gate_errors_rather_than_reporting_nothing():
    """A noise floor gates out entirely; integrated loudness is -inf, not "very quiet".

    Found by a live capture: the leveling loop must refuse this, not trim against it.
    """
    _need(HAVE_LOUDNESS, "pyloudnorm")
    out = loudness.analyze(_sine(seconds=3.0, amp_dbfs=-95.0), RATE, trim=False)
    assert out["silent"] is False, out
    assert out["lufs_integrated"] is None, out
    assert "error" in out and "gate" in out["error"], out


def test_too_short_a_capture_errors_rather_than_lying():
    _need(HAVE_LOUDNESS, "pyloudnorm")
    out = loudness.analyze(_sine(seconds=0.2), RATE)
    assert "error" in out and "BS.1770" in out["error"], out
    assert "lufs_integrated" not in out, out


def test_trim_removes_leading_and_trailing_silence():
    _need(HAVE_NUMPY, "numpy")
    tone = _sine(seconds=2.0)
    pad = np.zeros((RATE, 1))
    padded = np.vstack([pad, tone, pad, pad])
    out, lead, tail = loudness.trim_silence(padded, RATE)
    assert abs(lead - 1.0) < 0.05, lead
    assert abs(tail - 2.0) < 0.05, tail
    assert abs(out.shape[0] / RATE - 2.0) < 0.05, out.shape


def test_trim_leaves_all_silent_input_alone():
    _need(HAVE_NUMPY, "numpy")
    z = np.zeros((RATE, 2))
    out, lead, tail = loudness.trim_silence(z, RATE)
    assert out.shape == z.shape and lead == 0.0 and tail == 0.0


def test_short_term_windows_track_the_level():
    _need(HAVE_LOUDNESS, "pyloudnorm")
    vals = loudness.short_term_lufs(_sine(seconds=8.0), RATE)
    assert len(vals) > 3, vals
    assert all(abs(v - (-23.0)) < 0.3 for v in vals), vals


def test_db_delta_and_clamp():
    assert loudness.db_delta(-24.8, -18.0) == 6.8
    assert loudness.db_delta(None, -18.0) is None
    assert loudness.clamp_db(99.0) == 12.0
    assert loudness.clamp_db(-99.0) == -60.0


# ------------------------------------------------------------- spectral ----
def _noise(seconds=3.0, amp_dbfs=-20.0, rate=RATE, seed=7):
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(int(seconds * rate))
    x = x / np.max(np.abs(x)) * (10.0 ** (amp_dbfs / 20.0))
    return x.reshape(-1, 1)


def test_spectral_balance_puts_a_tone_in_its_own_band():
    _need(HAVE_NUMPY, "numpy")
    bands = loudness.spectral_balance(_sine(seconds=2.0, freq=1500.0), RATE)
    assert bands, bands
    hottest = max((v, k) for k, v in bands.items() if v is not None)[1]
    assert hottest == "presence", (hottest, bands)   # 1500 Hz -> the 1-2 kHz band


def test_spectral_balance_is_level_independent():
    """It answers 'where is the energy', not 'how loud' — so gain must not change it."""
    _need(HAVE_NUMPY, "numpy")
    quiet = loudness.spectral_balance(_noise(amp_dbfs=-40.0), RATE)
    loud = loudness.spectral_balance(_noise(amp_dbfs=-10.0), RATE)
    for k in quiet:
        if quiet[k] is not None and loud[k] is not None:
            assert abs(quiet[k] - loud[k]) < 0.01, (k, quiet[k], loud[k])


def test_identical_captures_compare_as_a_match():
    _need(HAVE_NUMPY, "numpy")
    b = loudness.spectral_balance(_noise(), RATE)
    cmp = loudness.compare_spectra(b, b)
    assert cmp["moved"] == [], cmp
    v = loudness.verdict(0.0, cmp)
    assert v["kind"] == "match", v


def test_a_pure_gain_change_reads_as_a_level_difference():
    _need(HAVE_NUMPY, "numpy")
    a = loudness.spectral_balance(_noise(amp_dbfs=-30.0), RATE)
    b = loudness.spectral_balance(_noise(amp_dbfs=-18.0), RATE)
    v = loudness.verdict(12.0, loudness.compare_spectra(a, b))
    assert v["kind"] == "level", v
    assert "trimming will fix" in v["summary"].lower(), v


def test_an_eq_change_reads_as_a_tone_difference():
    _need(HAVE_NUMPY, "numpy")
    low = loudness.spectral_balance(_sine(seconds=2.0, freq=100.0), RATE)
    high = loudness.spectral_balance(_sine(seconds=2.0, freq=6000.0), RATE)
    cmp = loudness.compare_spectra(low, high)
    assert len(cmp["moved"]) >= 2, cmp
    v = loudness.verdict(0.2, cmp)
    assert v["kind"] == "tone", v
    assert "not a level one" in v["summary"], v


def test_crest_factor_separates_dynamic_from_compressed():
    _need(HAVE_NUMPY, "numpy")
    sine = loudness.crest_db(_sine(seconds=1.0))
    square = np.ones((RATE, 1)) * 0.5
    assert abs(sine - 3.01) < 0.05, sine          # a sine is 3 dB peak-to-RMS
    assert abs(loudness.crest_db(square)) < 0.05  # a square is 0


def test_analyze_only_computes_the_spectrum_when_asked():
    _need(HAVE_LOUDNESS, "pyloudnorm")
    plain = loudness.analyze(_sine(), RATE)
    assert "spectral_balance" not in plain, plain.keys()
    full = loudness.analyze(_sine(), RATE, spectrum=True)
    assert full["spectral_balance"], full
    assert full["crest_db"] is not None, full


# ------------------------------------------------------ channel safety rules ----
def test_playing_on_host_outputs_1_to_4_is_refused():
    """Outputs 1-4 bypass The Grid and hit the analog jacks — full level to monitors."""
    from qc_mcp import audio_io
    dev = {"index": 0, "name": "fake", "inputs": 8, "outputs": 8}
    for bad in ((1, 2), (3, 4), (4, 5)):
        try:
            audio_io._check_channels(dev, out_channels=bad)
        except ValueError as e:
            assert "bypass" in str(e).lower() or "analog" in str(e).lower(), e
        else:
            raise AssertionError("channels %s should have been refused" % (bad,))


def test_grid_output_channels_are_allowed():
    from qc_mcp import audio_io
    dev = {"index": 0, "name": "fake", "inputs": 8, "outputs": 8}
    audio_io._check_channels(dev, out_channels=(5, 6))
    audio_io._check_channels(dev, out_channels=(7, 8))


def test_channels_beyond_the_device_are_refused():
    from qc_mcp import audio_io
    dev = {"index": 0, "name": "fake", "inputs": 2, "outputs": 2}
    for kwargs in ({"out_channels": (5, 6)}, {"in_channels": (5, 6)}):
        try:
            audio_io._check_channels(dev, **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError("out-of-range channels should be refused: %s" % kwargs)


def test_defaults_measure_on_the_grid_pair_not_the_analog_pair():
    """3/4 follows the analog outs and would fold master volume into the reading."""
    from qc_mcp import audio_io
    assert audio_io.DEFAULT_MEASURE_CHANNELS == (5, 6), audio_io.DEFAULT_MEASURE_CHANNELS
    assert audio_io.DEFAULT_REAMP_CHANNELS == (5, 6), audio_io.DEFAULT_REAMP_CHANNELS
    assert audio_io.QC_RATE == 48000


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
