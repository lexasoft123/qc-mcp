"""Loudness analysis for preset leveling — pure numpy, no device, no audio device I/O.

Kept deliberately free of `sounddevice` so it can be tested offline and so a machine
without the audio extra can still import the rest of the package.

Metrics:
  * integrated LUFS      — ITU-R BS.1770 K-weighting with the -70/-10 gates (pyloudnorm)
  * short-term LUFS      — 3 s windows, EBU R128 Tech 3341 (approximated, see below)
  * true peak (dBTP)     — BS.1770-4 Annex 2, 4x oversampled
  * RMS / sample peak    — plain, for sanity checks
  * Zwicker N5 / N50     — ISO 532-1 time-varying loudness (mosqito), OPTIONAL and slow

Why both LUFS and Zwicker: two presets at equal LUFS do not sound equally loud when one
is dense distortion and the other is a clean. LUFS is the cheap, standard, reproducible
number; Zwicker is closer to the ear but costs ~0.9x realtime, so it is opt-in.
"""
from __future__ import annotations

# Analysis constants
GATE_DBFS = -70.0          # silence trim threshold, matches BS.1770's absolute gate
FADE_MS = 20.0             # de-click fade applied after trimming
SHORT_TERM_S = 3.0         # EBU R128 short-term window
SHORT_TERM_HOP_S = 0.5
MIN_ANALYSIS_S = 0.4       # pyloudnorm's own floor: one 400 ms block
TRUE_PEAK_OVERSAMPLE = 4
# mosqito wants Pascals. We work in dBFS with no acoustic calibration, so this constant
# is arbitrary and the sone values are RELATIVE — comparable between our own captures,
# not against any absolute loudness scale. Do not present them as calibrated sones.
FS_TO_PA = 20.0


class AudioDepsMissing(RuntimeError):
    """Raised when the optional audio extra is not installed."""


def _numpy():
    try:
        import numpy as np
        return np
    except ImportError as e:                                  # pragma: no cover
        raise AudioDepsMissing(
            "numpy is required for loudness analysis — install the audio extra: "
            "pip install -e '.[audio]'") from e


def _as_2d(data):
    """Return float64 (samples, channels), accepting mono 1-D input."""
    np = _numpy()
    a = np.asarray(data, dtype=np.float64)
    if a.ndim == 1:
        a = a.reshape(-1, 1)
    if a.ndim != 2:
        raise ValueError("expected (samples,) or (samples, channels), got %r" % (a.shape,))
    return a


def _db(x):
    np = _numpy()
    x = float(x)
    return None if x <= 0 else float(20.0 * np.log10(x))


def _finite(x):
    """JSON-safe: -inf/NaN become None rather than poisoning a report."""
    np = _numpy()
    if x is None:
        return None
    x = float(x)
    return None if (np.isnan(x) or np.isinf(x)) else round(x, 2)


def trim_silence(data, rate, threshold_dbfs=GATE_DBFS, fade_ms=FADE_MS):
    """Drop leading/trailing silence below `threshold_dbfs`, then fade the edges.

    Returns (trimmed, lead_s, tail_s). Untouched when the whole buffer is silent —
    the caller decides what a silent capture means (usually: permission denied).
    """
    np = _numpy()
    a = _as_2d(data)
    if a.shape[0] == 0:
        return a, 0.0, 0.0
    env = np.max(np.abs(a), axis=1)
    thresh = 10.0 ** (threshold_dbfs / 20.0)
    loud = np.flatnonzero(env > thresh)
    if loud.size == 0:
        return a, 0.0, 0.0
    first, last = int(loud[0]), int(loud[-1]) + 1
    out = a[first:last].copy()
    n_fade = min(int(rate * fade_ms / 1000.0), out.shape[0] // 2)
    if n_fade > 1:
        ramp = np.linspace(0.0, 1.0, n_fade).reshape(-1, 1)
        out[:n_fade] *= ramp
        out[-n_fade:] *= ramp[::-1]
    return out, first / float(rate), (a.shape[0] - last) / float(rate)


def true_peak_dbtp(data, rate, oversample=TRUE_PEAK_OVERSAMPLE):
    """BS.1770-4 Annex 2 true peak: oversample, then take the max absolute sample."""
    np = _numpy()
    a = _as_2d(data)
    if a.shape[0] == 0:
        return None
    try:
        from scipy.signal import resample_poly
        up = resample_poly(a, oversample, 1, axis=0)
    except ImportError:                                        # pragma: no cover
        up = a                                                 # sample peak fallback
    return _db(np.max(np.abs(up)))


def rms_dbfs(data):
    np = _numpy()
    a = _as_2d(data)
    if a.shape[0] == 0:
        return None
    return _db(float(np.sqrt(np.mean(np.square(a)))))


def short_term_lufs(data, rate, window_s=SHORT_TERM_S, hop_s=SHORT_TERM_HOP_S):
    """Short-term loudness per EBU R128 (3 s window), as a list of LUFS values.

    APPROXIMATION: pyloudnorm exposes only gated integrated loudness, so each window is
    measured with its own meter. A true short-term meter is ungated; on a window that is
    mostly signal the difference is small, but on one straddling silence it is not.
    Windows that gate to -inf are dropped rather than reported as silence.
    """
    np = _numpy()
    try:
        import pyloudnorm as pyln
    except ImportError as e:
        raise AudioDepsMissing("pyloudnorm is required — install '.[audio]'") from e
    a = _as_2d(data)
    win = int(window_s * rate)
    hop = max(1, int(hop_s * rate))
    if a.shape[0] < win:
        return []
    meter = pyln.Meter(rate, block_size=window_s)
    vals = []
    for start in range(0, a.shape[0] - win + 1, hop):
        chunk = a[start:start + win]
        try:
            lv = float(meter.integrated_loudness(chunk))
        except Exception:
            continue
        if not (np.isnan(lv) or np.isinf(lv)):
            vals.append(lv)
    return vals


def zwicker(data, rate):
    """ISO 532-1 time-varying loudness -> (N5, N50) in RELATIVE sones, or (None, None).

    Costs roughly 0.9x realtime, so callers make this opt-in. Returns None when mosqito
    is absent (it imports matplotlib, which the extra must therefore also install).
    """
    np = _numpy()
    try:
        from mosqito.sq_metrics import loudness_zwtv
    except ImportError:
        return None, None
    a = _as_2d(data)
    mono = np.ascontiguousarray(a.mean(axis=1) * FS_TO_PA)
    if mono.size < int(0.2 * rate):
        return None, None
    try:
        N, _spec, _bark, _t = loudness_zwtv(mono, int(rate))
    except Exception:
        return None, None
    N = np.asarray(N, dtype=np.float64)
    N = N[np.isfinite(N)]
    if N.size == 0:
        return None, None
    # Nx = the loudness exceeded x% of the time, i.e. the (100-x)th percentile.
    return float(np.percentile(N, 95)), float(np.percentile(N, 50))


def analyze(data, rate, perceived=False, trim=True, spectrum=False):
    """Full measurement of one capture. `perceived=True` adds the slow Zwicker pass.

    Always reports `silent`: an all-zero capture is the signature of a denied macOS
    microphone permission, not of a quiet preset, and the caller must not level against
    it. See docs/LEVELING.md.
    """
    np = _numpy()
    try:
        import pyloudnorm as pyln
    except ImportError as e:
        raise AudioDepsMissing("pyloudnorm is required — install '.[audio]'") from e

    a = _as_2d(data)
    raw_s = a.shape[0] / float(rate)
    peak = float(np.max(np.abs(a))) if a.shape[0] else 0.0
    out = {
        "duration_s": round(raw_s, 3),
        "channels": int(a.shape[1]),
        "rate": int(rate),
        "sample_peak_dbfs": _finite(_db(peak)),
        "silent": bool(peak == 0.0),
    }
    if peak == 0.0:
        out["error"] = (
            "capture is digital silence on every channel. On macOS a DENIED microphone "
            "permission returns silence instead of raising — check that the host app "
            "has microphone access before assuming the preset is quiet.")
        return out

    if trim:
        a, lead, tail = trim_silence(a, rate)
        out["trimmed_lead_s"] = round(lead, 3)
        out["trimmed_tail_s"] = round(tail, 3)
        out["duration_s"] = round(a.shape[0] / float(rate), 3)

    if a.shape[0] < int(MIN_ANALYSIS_S * rate):
        out["error"] = ("only %.2f s of signal after trimming; BS.1770 needs at least "
                        "%.1f s" % (a.shape[0] / float(rate), MIN_ANALYSIS_S))
        return out

    meter = pyln.Meter(rate)
    out["lufs_integrated"] = _finite(meter.integrated_loudness(a))
    out["true_peak_dbtp"] = _finite(true_peak_dbtp(a, rate))
    out["rms_dbfs"] = _finite(rms_dbfs(a))

    if out["lufs_integrated"] is None:
        # Not silence, but everything fell under BS.1770's -70 LUFS absolute gate, so
        # integrated loudness is -inf. Typical of a noise floor with nothing playing.
        # A leveling loop must refuse this rather than treat it as "very quiet".
        out["error"] = (
            "signal is below the BS.1770 -70 LUFS gate, so integrated loudness is "
            "undefined (peak was %s dBFS). Nothing is playing through this output, or "
            "the wrong channels were captured." % out["sample_peak_dbfs"])
        return out

    st = short_term_lufs(a, rate)
    if st:
        out["lufs_short_term_max"] = _finite(max(st))
        out["lufs_short_term_p95"] = _finite(float(np.percentile(st, 95)))

    if spectrum:
        out["spectral_balance"] = spectral_balance(a, rate)
        out["crest_db"] = crest_db(a)

    if perceived:
        n5, n50 = zwicker(a, rate)
        out["zwicker_n5_rel"] = None if n5 is None else round(n5, 2)
        out["zwicker_n50_rel"] = None if n50 is None else round(n50, 2)
        if n5 is None:
            out["zwicker_note"] = ("mosqito unavailable (it imports matplotlib); "
                                   "install the audio extra to enable perceived loudness")
        else:
            out["zwicker_note"] = "relative sones — no acoustic calibration, compare only to other captures"
    return out


# Octave-ish bands for the spectral balance readout. Chosen to line up with how a
# guitarist talks about a tone: body, mids, presence, air.
BANDS = [
    ("sub", 20.0, 60.0), ("low", 60.0, 120.0), ("low_mid", 120.0, 250.0),
    ("mid", 250.0, 500.0), ("upper_mid", 500.0, 1000.0), ("presence", 1000.0, 2000.0),
    ("edge", 2000.0, 4000.0), ("brilliance", 4000.0, 8000.0), ("air", 8000.0, 16000.0),
]
# A band has to move by more than this before it counts as a tonal difference rather
# than measurement noise.
BAND_SIGNIFICANT_DB = 3.0


def spectral_balance(data, rate):
    """Energy per band in dB relative to the whole signal — a tone fingerprint.

    Relative by construction, so two captures at different levels stay comparable: it
    answers "where does this preset put its energy", not "how loud is it".
    """
    np = _numpy()
    a = _as_2d(data)
    if a.shape[0] < 1024:
        return {}
    mono = a.mean(axis=1)
    win = np.hanning(mono.size)
    spec = np.abs(np.fft.rfft(mono * win)) ** 2
    freqs = np.fft.rfftfreq(mono.size, 1.0 / rate)
    total = float(spec.sum())
    if total <= 0:
        return {}
    out = {}
    for name, lo, hi in BANDS:
        sel = (freqs >= lo) & (freqs < hi)
        frac = float(spec[sel].sum()) / total
        out[name] = None if frac <= 0 else round(10.0 * np.log10(frac), 2)
    return out


def crest_db(data):
    """Peak-to-RMS. High = dynamic and peaky; low = compressed and dense."""
    np = _numpy()
    a = _as_2d(data)
    if a.shape[0] == 0:
        return None
    peak = float(np.max(np.abs(a)))
    rms = float(np.sqrt(np.mean(np.square(a))))
    if peak <= 0 or rms <= 0:
        return None
    return round(20.0 * np.log10(peak / rms), 2)


def compare_spectra(reference, other, significant_db=BAND_SIGNIFICANT_DB):
    """Band-by-band difference between two `spectral_balance` results.

    Positive = `other` has more energy there than `reference`.
    """
    diff, moved = {}, []
    for name, _lo, _hi in BANDS:
        a, b = reference.get(name), other.get(name)
        if a is None or b is None:
            diff[name] = None
            continue
        d = round(b - a, 2)
        diff[name] = d
        if abs(d) >= significant_db:
            moved.append(name)
    return {"bands": diff, "moved": moved}


def verdict(level_delta_db, spectral, significant_db=BAND_SIGNIFICANT_DB,
            level_tolerance=1.0):
    """Say whether two captures differ by LEVEL or by TONE — the question worth asking.

    Levelling fixes a level difference. It cannot fix a tone difference, and trying is
    how a preset ends up both wrong and quiet.
    """
    moved = spectral.get("moved") or []
    level_off = level_delta_db is not None and abs(level_delta_db) > level_tolerance
    if len(moved) >= 2:
        kind = "tone" if not level_off else "both"
    elif level_off:
        kind = "level"
    else:
        kind = "match"
    msgs = {
        "match": "no meaningful difference in level or tone.",
        "level": "a level difference — trimming will fix this.",
        "tone": "a tone difference, not a level one. Trimming will not fix it; the EQ, "
                "cab or drive is what differs.",
        "both": "different in level AND tone. Level it first, then compare again — what "
                "is left is genuinely tonal.",
    }
    return {"kind": kind, "moved_bands": moved, "summary": msgs[kind]}


def db_delta(measured, target):
    """Correction in dB to move `measured` onto `target`. None if unmeasurable."""
    if measured is None or target is None:
        return None
    return round(float(target) - float(measured), 2)


def clamp_db(value, lo=-60.0, hi=12.0):
    """Clamp a trim to the Gain block's LEVEL range."""
    return max(lo, min(hi, float(value)))
