"""CoreAudio capture/playback for the Quad Cortex, plus the looper-style sampler.

Everything here is device-facing; the maths lives in `loudness.py`. Imports of
`sounddevice` are lazy so the MCP server still runs without the audio extra.

QUAD CORTEX USB CHANNEL MAP (manual 4.1.0, "USB Channels" + the USB_IO schematic).
Host-side numbering, which is what sounddevice's 1-based `mapping` uses:

  host records (in)          host plays (out)
  1,2  dry DI, analog In 1/2       1,2  -> analog OUT 1/L, 2/R   *speakers*
  3,4  analog OUT 1/L, 2/R         3,4  -> analog OUT 3/L, 4/R   *speakers*
  5-8  Grid USB Output 5-8         5-8  -> Grid USB Input 5-8    *the grid*

Two consequences the rest of the codebase depends on:

  * MEASURE ON 5/6, NOT 3/4. Host inputs 3/4 are fed from the analog outputs, so they
    ride the output stage and fold master volume into the reading — levelling against
    them would level the master, not the preset. Host 5-8 come from dedicated grid
    output blocks and bypass it.
  * NEVER PLAY THE STIMULUS ON HOST OUTPUTS 1-4. Those go straight to the analog jacks,
    bypassing the grid: full-level audio into whatever the player is monitoring on.
    Only 5-8 reach The Grid. `play_and_record` refuses anything below 5.
"""
from __future__ import annotations

import os

from .loudness import AudioDepsMissing

QC_NAME_HINT = "Quad Cortex"
QC_RATE = 48000                    # fixed; the QC runs at 48 kHz only
DEFAULT_MEASURE_CHANNELS = (5, 6)  # grid USB output blocks, free of the output stage
DEFAULT_REAMP_CHANNELS = (5, 6)    # -> grid USB Input 5/6, lane in_portid 12
MIN_GRID_OUT_CHANNEL = 5           # below this the host plays into the analog jacks
DEFAULT_SAMPLE_PATH = os.path.expanduser("~/.qc-mcp/reference_di.wav")


def _sd():
    try:
        import sounddevice as sd
        return sd
    except ImportError as e:
        raise AudioDepsMissing(
            "sounddevice is required for audio capture — install the audio extra: "
            "pip install -e '.[audio]'") from e


def _np():
    try:
        import numpy as np
        return np
    except ImportError as e:                                   # pragma: no cover
        raise AudioDepsMissing("numpy is required — install '.[audio]'") from e


def list_devices():
    """Every CoreAudio device, flagging the Quad Cortex."""
    sd = _sd()
    out = []
    for i, d in enumerate(sd.query_devices()):
        out.append({
            "index": i,
            "name": d["name"],
            "inputs": int(d["max_input_channels"]),
            "outputs": int(d["max_output_channels"]),
            "default_rate": int(d["default_samplerate"]),
            "is_quad_cortex": QC_NAME_HINT.lower() in d["name"].lower(),
        })
    return out


def find_device(name_hint=QC_NAME_HINT):
    """Resolve a device by NAME at call time.

    Never cache the index: it shifts as other audio devices come and go, and writing
    audio to the wrong index is exactly the failure that makes noise in someone's room.
    """
    sd = _sd()
    hint = (name_hint or QC_NAME_HINT).lower()
    matches = [(i, d) for i, d in enumerate(sd.query_devices())
               if hint in d["name"].lower()]
    if not matches:
        raise RuntimeError(
            "no audio device matching %r. Connect the Quad Cortex over USB and check "
            "it appears in Audio MIDI Setup." % name_hint)
    if len(matches) > 1:
        raise RuntimeError("%r matches %d devices: %s — be more specific"
                           % (name_hint, len(matches), [d["name"] for _i, d in matches]))
    idx, dev = matches[0]
    return {"index": idx, "name": dev["name"],
            "inputs": int(dev["max_input_channels"]),
            "outputs": int(dev["max_output_channels"]),
            "default_rate": int(dev["default_samplerate"])}


def _check_channels(device, in_channels=None, out_channels=None):
    if out_channels:
        bad = [c for c in out_channels if c < MIN_GRID_OUT_CHANNEL]
        if bad:
            raise ValueError(
                "refusing to play on host output channel(s) %s: outputs 1-4 are wired "
                "straight to the Quad Cortex's analog jacks and bypass The Grid, which "
                "would send full-level audio to the monitors. Only 5-8 reach The Grid."
                % bad)
        if any(c > device["outputs"] for c in out_channels):
            raise ValueError("device has %d outputs; asked for %s"
                             % (device["outputs"], list(out_channels)))
    if in_channels and any(c > device["inputs"] for c in in_channels):
        raise ValueError("device has %d inputs; asked for %s"
                         % (device["inputs"], list(in_channels)))


def record(seconds, channels=DEFAULT_MEASURE_CHANNELS, name_hint=QC_NAME_HINT):
    """Capture `seconds` from the given 1-based host input channels."""
    sd = _sd()
    dev = find_device(name_hint)
    _check_channels(dev, in_channels=channels)
    frames = int(seconds * QC_RATE)
    data = sd.rec(frames, samplerate=QC_RATE, mapping=list(channels),
                  dtype="float32", device=dev["index"])
    sd.wait()
    return data, QC_RATE


def play_and_record(stimulus, rate=QC_RATE, out_channels=DEFAULT_REAMP_CHANNELS,
                    in_channels=DEFAULT_MEASURE_CHANNELS, tail_s=1.0,
                    name_hint=QC_NAME_HINT):
    """Play `stimulus` into The Grid while capturing the processed result.

    `tail_s` of extra recording catches reverb/delay tails that outlast the stimulus.
    """
    np = _np()
    sd = _sd()
    if rate != QC_RATE:
        raise ValueError("the Quad Cortex is fixed at %d Hz; resample first" % QC_RATE)
    dev = find_device(name_hint)
    _check_channels(dev, in_channels=in_channels, out_channels=out_channels)

    a = np.asarray(stimulus, dtype="float32")
    if a.ndim == 1:
        a = a.reshape(-1, 1)
    if a.shape[1] != len(out_channels):        # mono -> both, or trim extra channels
        a = (np.repeat(a[:, :1], len(out_channels), axis=1) if a.shape[1] == 1
             else a[:, :len(out_channels)])
    pad = np.zeros((int(tail_s * rate), a.shape[1]), dtype="float32")
    a = np.vstack([a, pad])

    rec = sd.playrec(a, samplerate=rate, device=(dev["index"], dev["index"]),
                     input_mapping=list(in_channels),
                     output_mapping=list(out_channels), dtype="float32")
    sd.wait()
    return rec, rate


def play(stimulus, rate=QC_RATE, out_channels=DEFAULT_REAMP_CHANNELS,
         name_hint=QC_NAME_HINT, blocking=True):
    """Play into The Grid without recording — auditioning, not measuring.

    Same channel rule as `play_and_record`: only 5-8 reach The Grid, and 1-4 are
    refused because they bypass it straight to the analog jacks.
    """
    np = _np()
    sd = _sd()
    if rate != QC_RATE:
        raise ValueError("the Quad Cortex is fixed at %d Hz; resample first" % QC_RATE)
    dev = find_device(name_hint)
    _check_channels(dev, out_channels=out_channels)
    a = np.asarray(stimulus, dtype="float32")
    if a.ndim == 1:
        a = a.reshape(-1, 1)
    if a.shape[1] != len(out_channels):
        a = (np.repeat(a[:, :1], len(out_channels), axis=1) if a.shape[1] == 1
             else a[:, :len(out_channels)])
    sd.play(a, samplerate=rate, device=dev["index"],
            mapping=list(out_channels), blocking=blocking)
    return round(a.shape[0] / float(rate), 2)


def stop_play():
    """Cut short whatever `play` started."""
    _sd().stop()


def load_wav(path, target_rate=QC_RATE):
    """Read a WAV/AIFF/FLAC into float32 at 48 kHz, resampling only if needed."""
    np = _np()
    try:
        import soundfile as sf
    except ImportError as e:
        raise AudioDepsMissing("soundfile is required — install '.[audio]'") from e
    data, rate = sf.read(path, dtype="float32", always_2d=True)
    if rate != target_rate:
        from fractions import Fraction
        from scipy.signal import resample_poly
        f = Fraction(target_rate, rate).limit_denominator(1000)
        data = resample_poly(data, f.numerator, f.denominator, axis=0)
        data = np.asarray(data, dtype="float32")
    return data, target_rate


def save_wav(path, data, rate=QC_RATE):
    try:
        import soundfile as sf
    except ImportError as e:
        raise AudioDepsMissing("soundfile is required — install '.[audio]'") from e
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)
    sf.write(path, data, rate, subtype="PCM_24")
    return path


# --------------------------------------------------------------- the sampler ----
class Sampler:
    """A looper pedal for the reference riff: arm, auto-start on the first note,
    auto-stop after silence, trim, save.

    The callback only appends buffers — no numpy maths, no I/O — so it cannot overrun
    the realtime deadline. Everything else happens on the calling thread.
    """

    IDLE, ARMED, RECORDING, DONE = "idle", "armed", "recording", "done"
    #: buckets in the envelope handed to the UI — enough to draw, small enough to
    #: send on every status poll
    ENVELOPE = 240

    def __init__(self):
        self.state = self.IDLE
        self._stream = None
        self._blocks = []
        self._input_peak = 0.0
        self._armed_peak = 0.0
        self.threshold_dbfs = -40.0
        self.max_seconds = 30.0
        self.silence_seconds = 1.5
        self._silent_run = 0
        self._frames = 0
        #: one peak per callback block, so the view can draw the take as it grows
        self._env = []
        self.result = None
        self.error = None

    # -- lifecycle ------------------------------------------------------------
    def arm(self, threshold_dbfs=-40.0, max_seconds=30.0, silence_seconds=1.5,
            channels=(1, 2), name_hint=QC_NAME_HINT):
        """Open the input and wait for the first note above the threshold.

        Records the DRY DI (host inputs 1/2) — the riff must be the player's raw
        signal, not something already coloured by a preset.
        """
        np = _np()
        sd = _sd()
        if self.state in (self.ARMED, self.RECORDING):
            return self.status()
        dev = find_device(name_hint)
        _check_channels(dev, in_channels=channels)
        self.threshold_dbfs = float(threshold_dbfs)
        self.max_seconds = float(max_seconds)
        self.silence_seconds = float(silence_seconds)
        self._blocks, self._frames, self._silent_run = [], 0, 0
        self._env = []
        self._input_peak = self._armed_peak = 0.0
        self.result = self.error = None
        thresh = 10.0 ** (self.threshold_dbfs / 20.0)
        max_frames = int(self.max_seconds * QC_RATE)
        silence_frames = int(self.silence_seconds * QC_RATE)

        def cb(indata, frames, time_info, status):             # realtime thread
            peak = float(np.max(np.abs(indata))) if frames else 0.0
            self._input_peak = peak
            if self.state == self.ARMED:
                if peak > thresh:
                    self.state = self.RECORDING
                    self._blocks.append(indata.copy())
                    self._env.append(peak)
                    self._frames += frames
                return
            if self.state == self.RECORDING:
                self._blocks.append(indata.copy())
                self._env.append(peak)
                self._frames += frames
                self._armed_peak = max(self._armed_peak, peak)
                self._silent_run = 0 if peak > thresh else self._silent_run + frames
                if self._silent_run >= silence_frames or self._frames >= max_frames:
                    raise sd.CallbackStop()

        self._stream = sd.InputStream(
            samplerate=QC_RATE, device=dev["index"], channels=max(channels),
            dtype="float32", blocksize=1024, callback=cb,
            finished_callback=self._on_finished)
        self.state = self.ARMED
        self._stream.start()
        return self.status()

    def _on_finished(self):
        if self.state == self.RECORDING:
            self.state = self.DONE

    def stop(self, path=None, trim=True):
        """End the take (the pedal's second press), trim it, and save."""
        from . import loudness
        np = _np()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
        if not self._blocks:
            self.state = self.IDLE
            self.error = ("nothing was recorded — no signal crossed %.0f dBFS. Play "
                          "louder, or lower the threshold." % self.threshold_dbfs)
            return {"state": self.state, "error": self.error}
        data = np.concatenate(self._blocks, axis=0)
        if trim:
            data, lead, tail = loudness.trim_silence(data, QC_RATE)
        else:
            lead = tail = 0.0
        self.state = self.DONE
        path = path or DEFAULT_SAMPLE_PATH
        save_wav(path, data, QC_RATE)
        info = loudness.analyze(data, QC_RATE)
        self.result = {
            "state": self.state, "path": path, "peaks": self.envelope(),
            "duration_s": round(data.shape[0] / float(QC_RATE), 3),
            "trimmed_lead_s": round(lead, 3), "trimmed_tail_s": round(tail, 3),
            "peak_dbfs": info.get("sample_peak_dbfs"),
            "lufs": info.get("lufs_integrated"),
            "true_peak_dbtp": info.get("true_peak_dbtp"),
        }
        return self.result

    def discard(self):
        """Throw the take away and go back to idle (the undo)."""
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None
        self._blocks, self._frames, self._silent_run = [], 0, 0
        self.state = self.IDLE
        self.result = self.error = None
        return {"state": self.state}

    def envelope(self, buckets=None):
        """The take as `buckets` peaks in 0..1 — what the view draws.

        Computed from the per-block peaks the callback already keeps, not from the
        audio: the audio runs to tens of megabytes and this crosses a JSON socket on
        every status poll.
        """
        n = buckets or self.ENVELOPE
        src = self._env
        if not src:
            return []
        if len(src) <= n:
            return [round(float(v), 4) for v in src]
        step = len(src) / float(n)
        out = []
        for i in range(n):
            lo = int(i * step)
            hi = max(lo + 1, int((i + 1) * step))
            out.append(round(float(max(src[lo:hi])), 4))
        return out

    def status(self):
        np = _np()
        peak = self._input_peak
        return {
            "state": self.state,
            "seconds_recorded": round(self._frames / float(QC_RATE), 2),
            "input_dbfs": (None if peak <= 0 else round(float(20 * np.log10(peak)), 1)),
            "threshold_dbfs": self.threshold_dbfs,
            "max_seconds": self.max_seconds,
            "silence_seconds": self.silence_seconds,
            "error": self.error,
            "peaks": self.envelope(),
        }


def sample_info(path=None, buckets=None):
    """Facts and a drawable envelope for a riff already on disk.

    The sampler only knows about a take it recorded this session. Reopening the app
    with a riff already saved would otherwise show "Ready" over a blank waveform and
    zeroed facts, so the stored file has to be readable back.
    """
    from . import loudness
    np = _np()
    path = path or DEFAULT_SAMPLE_PATH
    if not os.path.exists(path):
        return None
    data, rate = load_wav(path)
    info = loudness.analyze(data, rate, trim=False)
    n = buckets or Sampler.ENVELOPE
    mono = np.abs(np.asarray(data, dtype="float64")).max(axis=1)
    if mono.size >= n:
        # trim to a whole number of buckets so reshape can do the work
        usable = (mono.size // n) * n
        env = mono[:usable].reshape(n, -1).max(axis=1)
    else:
        env = mono
    return {
        "state": "done", "path": path,
        "seconds_recorded": round(data.shape[0] / float(rate), 2),
        "duration_s": round(data.shape[0] / float(rate), 3),
        "peak_dbfs": info.get("sample_peak_dbfs"),
        "lufs": info.get("lufs_integrated"),
        "true_peak_dbtp": info.get("true_peak_dbtp"),
        "peaks": [round(float(v), 4) for v in env],
        "input_dbfs": None, "threshold_dbfs": -40.0,
        "max_seconds": 30.0, "silence_seconds": 1.5, "error": None,
    }


_sampler = None


def sampler():
    global _sampler
    if _sampler is None:
        _sampler = Sampler()
    return _sampler
