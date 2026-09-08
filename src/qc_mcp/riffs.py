"""Reference riffs, synthesised.

Leveling needs a signal to push through every preset, and the obvious source —
the player records one — turns out to be the weakest part of the whole chain.
The first riff recorded against this feature peaked at -22.8 dBFS with 74% of
its samples near silence, and a preset fed 12 dB of input moved its output by
3.5: there was nothing there to measure. Nobody notices that while playing, and
the numbers that come out the far end look like preset differences.

So the bench ships its own. These are Karplus-Strong plucked strings — a burst
of noise round a delay line the length of one period, damped a little on each
pass — which is a real physical model of a string and sounds enough like a DI
to drive an amp model honestly. Cheap, exact, and identical on every machine,
which also makes them the right input for a test.

Each is normalised to a known peak and carries broadband content with real
dynamics, because both matter:

  * peak at -6 dBFS, so there is 6 dB of headroom and 60 dB of signal
  * no long silences — the gate in loudness.analyze would eat them, and a riff
    that is mostly gaps measures the gaps

`catalogue()` names them; `render(name)` returns float32 mono at 48 kHz, the
rate the Quad Cortex runs at and the only one it will accept.
"""
from __future__ import annotations

import math
import os

RATE = 48_000
PEAK_DBFS = -6.0

#: name -> (what it is for, notes as (semitone, beat, duration_beats, velocity))
#: Semitones are relative to low E (82.41 Hz), the way a guitarist counts frets.
_E2 = 82.4069


def _note(semitone, beat, dur=1.0, vel=1.0):
    return (semitone, beat, dur, vel)


def _chord(semitones, beat, dur=2.0, vel=1.0, spread=0.018):
    """A strum: the same beat, offset by a plectrum's worth of time per string."""
    return [(s, beat + i * spread, dur, vel * (1.0 - 0.04 * i))
            for i, s in enumerate(semitones)]


# Open E, A, D, G, B, e as semitones above low E.
_OPEN = (0, 5, 10, 15, 19, 24)
_POWER_E = (0, 7, 12)
_POWER_G = (3, 10, 15)
_POWER_A = (5, 12, 17)


def _riffs():
    """Built lazily so importing this module costs nothing."""
    return {
        "chords": (
            "Open strummed chords — broadband and sustained. The general case, "
            "and the one that shows a preset's tone as well as its level.",
            120.0,
            [n for beat, ch in ((0.0, _OPEN), (2.0, _OPEN), (4.0, _OPEN), (6.0, _OPEN))
             for n in _chord(ch, beat, dur=2.0, vel=0.95 if beat % 4 else 1.0)],
        ),
        "chug": (
            "Palm-muted eighths on the low string — short, dense, and the hardest "
            "case for a true-peak guard.",
            140.0,
            [_note(0, i * 0.5, 0.34, 1.0 if i % 4 == 0 else 0.8) for i in range(16)]
            + [_note(3, 8.0 + i * 0.5, 0.34, 0.9) for i in range(4)],
        ),
        "lead": (
            "A single-note line up the neck — sparse and sustained, where a gate "
            "set too high starts measuring the gaps instead of the notes.",
            110.0,
            [_note(s, i * 0.75, 0.7, v) for i, (s, v) in enumerate(
                [(12, 1.0), (15, .9), (17, .95), (19, 1.0), (17, .85),
                 (15, .9), (12, .95), (10, .9), (12, 1.0)])],
        ),
        "dynamics": (
            "The same phrase played quiet, then loud — a preset that compresses "
            "hard measures very differently from one that does not.",
            100.0,
            [n for i, v in enumerate((0.35, 0.55, 0.8, 1.0))
             for n in _chord(_POWER_E if i % 2 == 0 else _POWER_A,
                             i * 2.0, dur=1.8, vel=v)],
        ),
        "sweep": (
            "Power chords across the low register — for checking that a trim is "
            "level and not tilt, since each one lands in a different band.",
            120.0,
            _chord(_POWER_E, 0.0, 1.8) + _chord(_POWER_G, 2.0, 1.8)
            + _chord(_POWER_A, 4.0, 1.8) + _chord(_POWER_E, 6.0, 1.8),
        ),
    }


def catalogue():
    """`[(name, description, seconds)]` — what the bench can offer."""
    out = []
    for name, (why, bpm, notes) in sorted(_riffs().items()):
        end = max(b + d for _s, b, d, _v in notes)
        out.append((name, why, round(end * 60.0 / bpm + 0.4, 2)))
    return out


def _pluck(freq, seconds, rate, velocity, damping, seed):
    """One Karplus-Strong string.

    The delay line is one period long and is filled with noise; each pass
    averages neighbouring samples, which is a one-pole lowpass, so the harmonics
    die faster than the fundamental exactly as they do on a real string.
    """
    import numpy as np

    n = max(1, int(round(rate / float(freq))))
    rng = np.random.default_rng(seed)
    buf = rng.uniform(-1.0, 1.0, n).astype(np.float64)
    # Take the edge off the pick attack; an unfiltered noise burst is a click.
    buf = np.convolve(buf, np.ones(3) / 3.0, mode="same")

    total = int(seconds * rate)
    out = np.empty(total, dtype=np.float64)
    idx = 0
    for i in range(total):
        out[i] = buf[idx]
        buf[idx] = damping * 0.5 * (buf[idx] + buf[(idx + 1) % n])
        idx = (idx + 1) % n
    return out * velocity


def render(name, rate=RATE, peak_dbfs=PEAK_DBFS):
    """One riff as float32 mono. Deterministic: same bytes on every machine."""
    import numpy as np

    table = _riffs()
    if name not in table:
        raise KeyError("no such riff %r (have: %s)" % (name, ", ".join(sorted(table))))
    _why, bpm, notes = table[name]
    spb = 60.0 / bpm

    end = max(b + d for _s, b, d, _v in notes) * spb + 0.35
    buf = np.zeros(int(end * rate) + 1, dtype=np.float64)

    for i, (semi, beat, dur, vel) in enumerate(notes):
        freq = _E2 * (2.0 ** (semi / 12.0))
        # Higher strings ring shorter, and a quiet note decays sooner: both are
        # what the damping term does on a real instrument.
        damping = 0.996 - 0.0009 * (semi / 12.0)
        seg = _pluck(freq, dur * spb, rate, vel, damping, seed=1000 + i)
        at = int(beat * spb * rate)
        room = min(len(seg), len(buf) - at)
        if room > 0:
            # a short release so a stopped note is not a step discontinuity
            tail = min(room, int(0.012 * rate))
            seg = seg[:room].copy()
            seg[-tail:] *= np.linspace(1.0, 0.0, tail)
            buf[at:at + room] += seg

    peak = float(np.max(np.abs(buf)))
    if peak > 0:
        buf *= (10.0 ** (peak_dbfs / 20.0)) / peak
    return buf.astype(np.float32)


def default_dir():
    """Where rendered riffs are cached, beside the recorded one."""
    home = os.path.expanduser("~")
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or home
        return os.path.join(base, "qc-mcp", "riffs")
    return os.path.join(home, ".qc-mcp", "riffs")


def path_for(name, directory=None):
    return os.path.join(directory or default_dir(), "%s.wav" % name)


def ensure(name, directory=None, rate=RATE):
    """Render `name` to a wav if it is not already there, and return the path.

    Cached rather than committed: they are a few hundred kilobytes each, they
    are perfectly reproducible from this file, and a repo is not a good place
    for generated audio.
    """
    from . import audio_io

    p = path_for(name, directory)
    if os.path.exists(p):
        return p
    os.makedirs(os.path.dirname(p), exist_ok=True)
    audio_io.save_wav(p, render(name, rate=rate), rate)
    return p


def ensure_all(directory=None, rate=RATE):
    return {name: ensure(name, directory, rate) for name, _w, _s in catalogue()}


def describe(name):
    return _riffs()[name][0]


__all__ = ["RATE", "catalogue", "describe", "render", "ensure", "ensure_all",
           "path_for", "default_dir"]
