"""MCP server exposing Quad Cortex control over its reverse-engineered USB-HID
protocol. See PROTOCOL.md for the full protocol.

Capabilities: read the live preset (fx blocks, grid positions, per-lane input/
output blocks, params) the way Cortex Control does on boot; read hardware I/O
settings and CPU load; switch presets, scenes (A-H), and performance modes
(Preset=0 / Hybrid=6); recall presets; set master volume; search the 533-device
catalog (name / emulated gear / parameters); and browse the on-device DIRECTORY —
presets, neural captures, and IRs — with search, favorites/recents, and load-by-
position (see qc_mcp/directory.py and docs/DIRECTORY.md).

The connection maintains the session + KeepAlive heartbeat the QC requires before
it will stream state and answer READs (see transport.QuadCortex).

Connection modes (auto-detected in _conn): **bridge** — the instrumented Cortex
Control is running (interceptor/run-bridge.sh) and we share its live session over
FIFOs, so the app and this server work at once (preferred); **direct** — no bridge
FIFOs, we seize the HID device ourselves, which requires Cortex Control to be quit
(exclusive access). QC_BRIDGE=0 forces direct mode.
"""
from __future__ import annotations
import os
import threading

from mcp.server.fastmcp import FastMCP

from . import protocol as P
from . import catalog
from .transport import QuadCortex, QCError

mcp = FastMCP("quad-cortex", instructions="""Controls a Neural DSP Quad Cortex
(guitar amp modeler) over its internal USB protocol. Core workflow for building:

1. connect() — asks bridge vs direct when ambiguous; bridge self-launches the app.
2. SLOT SAFETY: never build over a named preset. If the currently open slot is
   empty ("Unsaved") use IT (the user chose it); else list_empty_slots + recall.
3. Build: find_devices for hashes -> build_preset (topology+params in one spec)
   -> ALWAYS verify with get_current_preset (routing/split_points) + cpu_load
   (<~85%; delete, don't bypass, to save CPU).
4. Scenes A-H = per-block param/bypass snapshots. set_block_bypass(scenes=[...])
   for drives/amps (NO-OP on delays — scene the delay MIX instead). Vary AMP
   params per scene too (gain/master/EQ) via set_parameter_scenes. Label scenes
   with set_preset_meta.
5. Stereo multi-amp: pan branch lanes with set_lane_output (~0.3/0.7), drop lane
   volumes ~0.5 so the parallel sum doesn't clip.
6. Save: save_preset / save_preset_as (File CREATE; guarded against clobbering).
   A success string is not proof — verify via current_preset_position + read.

Deeper knowledge (routing recipes for shared-front multi-amp rigs, CPU model,
protocol docs, GUI verification harness) lives in the qc-mcp repo — sessions
opened in that repo load it automatically as skills/CLAUDE.md.""")

_qc = None
_lock = threading.Lock()


def _conn():
    global _qc
    with _lock:
        if _qc is None:
            # Auto-detect: if the instrumented Cortex Control is running (bridge
            # FIFOs present), share its session so both run at once; otherwise
            # seize the device directly. QC_BRIDGE=0 forces direct mode.
            fifos = os.path.exists("/tmp/qc_inject") and os.path.exists("/tmp/qc_in")
            bridge = fifos and os.environ.get("QC_BRIDGE") != "0"
            try:
                _qc = QuadCortex(bridge=bridge).open(handshake=True)
            except Exception:
                if bridge:   # bridge failed (app not really there) — fall back
                    _qc = QuadCortex(bridge=False).open(handshake=True)
                else:
                    raise
        return _qc


def _disconnect():
    global _qc
    with _lock:
        if _qc is not None:
            _qc.close()
            _qc = None


def _repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _app_running(pattern):
    import subprocess
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0


def _bridge_running():
    """Bridge is usable = FIFOs exist AND the instrumented app is alive (the FIFOs
    are plain filesystem objects and outlive the app — existence alone lies)."""
    return (os.path.exists("/tmp/qc_inject") and os.path.exists("/tmp/qc_in")
            and _app_running("CortexControl-instrumented"))


def _launch_bridge(timeout_s=45):
    """Start interceptor/run-bridge.sh (instrumented Cortex Control with the FIFO
    bridge) detached, and wait until the bridge is up."""
    import subprocess, time
    script = os.path.join(_repo_root(), "interceptor", "run-bridge.sh")
    if not os.path.exists(script):
        return f"run-bridge.sh not found at {script}"
    subprocess.Popen([script], cwd=_repo_root(), start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _bridge_running():
            time.sleep(12)  # boot storm: the app pulls the whole catalog on start —
            return None     # join after it settles or our first reads time out
        time.sleep(1)
    return f"bridge did not come up within {timeout_s}s (device plugged in?)"


def _fields(msg):
    return {f.name: getattr(msg, f.name) for f, _ in msg.ListFields()}


def _preset_summary(bp):
    chains = []
    for ch in bp.chains:
        blocks = []
        for i, m in enumerate(ch.models):
            if m.hash == 0:
                continue
            info = catalog.lookup(m.hash) or {}
            cparams = info.get("params", [])
            params = {}
            for pos, p in enumerate(m.params):   # param index == array position
                if not p.param_values or pos >= len(cparams):
                    continue
                nv = p.param_values[0].float_value
                params[cparams[pos]["name"]] = round(catalog.to_display(m.hash, pos, nv), 3)
            blocks.append({"slot": i, "model_hash": m.hash,
                           "name": info.get("name", f"unknown#{m.hash}"),
                           "category": info.get("category"),
                           "based_on": info.get("tm", ""),
                           "params": params})
        def lane_ctrl(models):
            for m in models:
                if not m.hash:
                    continue
                info = catalog.lookup(m.hash) or {}
                cps = info.get("params", [])
                vals = {}
                for pos, p in enumerate(m.params):
                    if p.param_values and pos < len(cps):
                        nv = p.param_values[0].float_value
                        vals[cps[pos]["name"]] = round(catalog.to_display(m.hash, pos, nv), 3)
                return {"name": info.get("name"), "params": vals}
            return None
        split_points = [[s.split, s.mix] for s in ch.split_control_points
                        if (s.split, s.mix) != (-1, -1)]
        chains.append({"row": ch.row, "in_port": ch.in_portid,
                       "out_port": ch.out_portid,
                       "split_points": split_points, "blocks": blocks,
                       "input_block": lane_ctrl(ch.input_control),
                       "output_block": lane_ctrl(ch.output_control)})
    return {"name": bp.name, "tempo": bp.tempo, "default_scene": bp.default_scene,
            "scene_labels": [s for s in bp.scene_labels],
            "num_chains": len(bp.chains), "chains": chains}


@mcp.tool()
def connect(mode: str = "auto", quit_app: bool = False) -> dict:
    """Connect to the Quad Cortex. mode:
      * 'auto' (default) — if the bridge (instrumented Cortex Control) is running,
        join it. Otherwise DON'T guess: returns the available modes as a question —
        relay the choice to the user, then call connect(mode=...) with their answer.
      * 'bridge' — launches interceptor/run-bridge.sh ITSELF if needed (starts the
        instrumented Cortex Control; app + MCP share the device) and connects.
        Takes ~20s on a cold start.
      * 'direct' — exclusive USB-HID. If any Cortex Control is running it holds the
        device: refuses unless quit_app=True (then quits the app first).
    """
    global _qc
    if _qc is not None:
        return {"status": f"already connected ({'bridge' if _qc.bridge else 'direct'} mode)"}
    if mode == "auto":
        if _bridge_running():
            mode = "bridge"
        else:
            return {"question": "How should I connect to the Quad Cortex?",
                    "options": {
                        "bridge": "I launch the instrumented Cortex Control myself; "
                                  "the app and MCP then share the device (recommended "
                                  "— GUI verification works too). ~20s cold start.",
                        "direct": "Exclusive USB-HID, no app running. Faster, but no "
                                  "Cortex Control GUI alongside."},
                    "next": "Ask the user, then call connect(mode='bridge') or "
                            "connect(mode='direct')."}
    if mode == "bridge":
        os.environ.pop("QC_BRIDGE", None)   # undo a prior direct-mode override
        if not _bridge_running():
            err = _launch_bridge()
            if err:
                return {"error": err}
    elif mode == "direct":
        if _bridge_running() or _app_running("Cortex Control.app"):
            if not quit_app:
                return {"error": "Cortex Control is running and holds the device. "
                                 "Pass quit_app=True to quit it and connect direct, "
                                 "or use mode='bridge' to share its session."}
            import subprocess, time
            subprocess.run(["osascript", "-e", 'quit app "Cortex Control"'],
                           capture_output=True)
            time.sleep(3)
        os.environ["QC_BRIDGE"] = "0"     # _conn() honors this: force direct
    else:
        return {"error": f"unknown mode {mode!r} — use 'auto', 'bridge' or 'direct'."}
    try:
        qc = _conn()
        v = qc.read_state("Version")
        return {"status": f"Connected ({mode}). Firmware "
                          f"{getattr(v, 'zenos_git_hash', '?')}."}
    except QCError as e:
        return {"error": f"Connect failed: {e}"}


@mcp.tool()
def disconnect() -> str:
    """Disconnect from the Quad Cortex (releases the HID device so Cortex
    Control can use it again)."""
    _disconnect()
    return "Disconnected."


@mcp.tool()
def device_info() -> dict:
    """Read the Quad Cortex firmware / device info. Read-only."""
    v = _conn().read_state("Version")
    return _fields(v)


@mcp.tool()
def get_current_preset() -> dict:
    """Read the currently loaded preset and its full signal chain (blocks, grid
    positions, and tweaked parameters) directly from the device — the same full
    state Cortex Control loads on boot. Read-only."""
    qc = _conn()
    bp = qc.get_current_preset()
    if bp is None:
        return {"note": "no preset returned; device may be mid-load — retry."}
    return _preset_summary(bp)


@mcp.tool()
def recall_preset(position: int, setlist_key: str = "", is_factory: bool = False,
                  capture_seconds: float = 3.0) -> dict:
    """WRITE: load a preset by setlist position (0-based). Changes the active
    preset on the device. setlist_key defaults to the CURRENT folder — the device
    REQUIRES folder_key on SetlistPosition UPDATE (a folderless recall is silently
    refused: it answers with the unchanged position). Verifies the recall landed."""
    qc = _conn()
    if not setlist_key and not is_factory:
        cur = qc.get_setlist_position() or {}
        setlist_key = cur.get("folder_key") or "/media/p4/Presets/My Presets"
    cls = P.message_class("SetlistPosition")
    m = cls(action=P.ACTION["UPDATE"], request_id=qc.next_request_id(),
            position=position, is_factory=is_factory)
    if setlist_key:
        m.folder_key = setlist_key
    qc.send("SetlistPosition", m)
    import time
    out = {"recalled_position": position}
    deadline = time.time() + capture_seconds
    while time.time() < deadline:
        qc._collect(0.2)
        for cmd, obj, raw, pb in list(qc._pending):
            if cmd == P.NAME_TO_CMD["RecallPreset"] and obj is not None \
                    and obj.HasField("preset"):
                out["preset"] = _preset_summary(obj.preset)
                deadline = 0
    # the device echoes SetlistPosition with the ACTUAL position — verify it moved
    now = qc.get_setlist_position() or {}
    if now.get("position") is not None and now["position"] != position:
        return {"error": f"device refused the recall (still at position "
                         f"{now['position']}) — check setlist_key/position.",
                "requested": position, "actual": now}
    return out


@mcp.tool()
def set_master_volume(volume: float, engaged: bool = True) -> str:
    """WRITE: set master volume (0.0-1.0)."""
    qc = _conn()
    m = P.message_class("MasterVolume")(action=P.ACTION["UPDATE"],
                                        request_id=qc.next_request_id(),
                                        volume=float(volume), engaged=engaged)
    qc.send("MasterVolume", m)
    return f"Master volume set to {volume}."


@mcp.tool()
def cpu_load(detail: bool = True) -> dict:
    """Read the current DSP load. Returns {total_percent} plus, when detail=True, a
    per-block and per-core breakdown: the QC has two DSP cores and assigns each block
    to one (reporting a per-block cost + is_on_core2 flag), balancing to keep the
    busier core under ~90%. Heaviest blocks are amps/captures (~0.5-0.6) and stereo
    reverbs; cabs ~0.18; drives/comp ~0.08-0.16. To LOWER CPU you must DELETE blocks —
    bypassing/disabling does NOT free CPU. Duplicating a block across parallel lanes
    multiplies its cost. Global EQ + Input Gate live on the shared core and auto-
    disable on overload. Read-only. See docs/CPU.md and the optimize-preset-cpu skill."""
    qc = _conn()
    import time
    want = P.NAME_TO_CMD["CPULoad"]
    # CPULoad is streamed continuously, so the buffer holds stale ones — drop any
    # already queued, then take the LATEST fresh message (reflects the current grid).
    qc._pending = [t for t in qc._pending if t[0] != want]
    msg = None
    deadline = time.time() + 2.0
    while time.time() < deadline:
        qc._collect(0.25)
        fresh = [obj for cmd, obj, raw, pb in qc._pending if cmd == want and obj is not None]
        qc._pending = [t for t in qc._pending if t[0] != want]
        if fresh:
            msg = fresh[-1]
            break
    if msg is None:
        return {"note": "no CPULoad update captured"}
    out = {"total_percent": round(msg.cpu_total_load, 1)}
    if not detail:
        return out
    names = {}
    try:                                        # label blocks by name (best-effort)
        bp = qc.get_current_preset(timeout_ms=3000)
        if bp:
            for ridx, ch in enumerate(bp.chains):
                for cidx, m in enumerate(ch.models):   # array index == physical column
                    if m.hash:
                        names[(ridx, cidx)] = catalog.name_of(m.hash).split(" [")[0]
    except Exception:
        pass
    blocks = []
    by_core = {"core1": 0.0, "core2": 0.0}
    for ridx, ch in enumerate(msg.chains):
        for cidx, col in enumerate(ch.columns):
            load = round(getattr(col, "cpu_load", 0.0), 4)
            if load <= 0:
                continue
            on2 = bool(getattr(col, "is_on_core2", False))
            by_core["core2" if on2 else "core1"] += load
            blocks.append({"row": ridx, "col": cidx, "cpu": load,
                           "core": 2 if on2 else 1, "name": names.get((ridx, cidx), "")})
    blocks.sort(key=lambda b: -b["cpu"])
    out["by_core_weight"] = {k: round(v, 3) for k, v in by_core.items()}
    out["blocks"] = blocks
    out["hint"] = ("Delete (not bypass) blocks to cut CPU; dedupe blocks repeated "
                   "across parallel lanes; prefer mono (M) over stereo (ST).")
    return out


# IOMeter port fields, grouped as the hardware presents them. Each entry is
# (label, level field, limiter field or None). The headphones carry a SINGLE
# `hp_limiter_active` flag for both channels, not one per channel.
_METER_PORTS = [
    ("input_1", "input_1", None), ("input_2", "input_2", None),
    ("return_1", "return_1", None), ("return_2", "return_2", None),
    ("xlr_1", "xlr_1", "xlr_1_limiter"), ("xlr_2", "xlr_2", "xlr_2_limiter"),
    ("out_3", "out_3", "out_3_limiter"), ("out_4", "out_4", "out_4_limiter"),
    ("send_1", "send_1", None), ("send_2", "send_2", None),
    ("hp_l", "hp_l", None), ("hp_r", "hp_r", None),
]
# The grid-side taps, i.e. what the preset feeds each output before the output stage.
_METER_GRID = ["grid_xlr_1", "grid_xlr_2", "grid_out_3", "grid_out_4",
               "grid_send_1", "grid_send_2"]
_METER_USB_OUT = ["usb_output_%d%s" % (n, s) for n in (1, 2, 3, 4) for s in ("l", "r")]
_METER_USB_IN = ["usb_input_%d%s" % (n, s) for n in (1, 2, 3, 4) for s in ("l", "r")]

# IOMeter reports LINEAR AMPLITUDE 0..1, not dB — established by the Patchbay Leveling
# bench, which converts with 20*log10 and displays on the -40..+12 dB scale the QC's own
# OUT LEVEL readout uses. Straight to a bar the linear value buries everything below
# -6 dBFS in the bottom tenth, which is where guitar playing actually lives.
METER_FLOOR_DB = -40.0
METER_CEIL_DB = 12.0


def _meter_db(level):
    """Linear amplitude -> dB on the device's own -40..+12 scale."""
    import math
    if level is None or level <= 0:
        return None
    return round(max(METER_FLOOR_DB, 20.0 * math.log10(float(level))), 1)


@mcp.tool()
def output_meter(hold_s: float = 1.0, detail: bool = False) -> dict:
    """READ the device's live output meters (IOMeter telemetry) over a hold window.

    Returns per-port {peak, last} plus the per-output LIMITER flags — the reliable way
    to answer "is signal reaching the outputs?" and "is this preset clipping?" without
    guessing from the grid. Ports: input_1/2, return_1/2, xlr_1/2, out_3/4, send_1/2,
    hp_l/hp_r; detail=True adds the grid-side taps and all USB channels.

    `hold_s` is a peak-hold window: the device streams these continuously, so a longer
    hold catches transients a single sample would miss. Play while it runs.

    Levels come back both as the raw linear amplitude the device sends (0..1) and as dB
    on the -40..+12 scale the QC's own OUT LEVEL readout uses. See docs/METERS.md."""
    qc = _conn()
    msgs = qc.latest_broadcast("IOMeter", hold_s=hold_s)
    if not msgs:
        return {"note": "no IOMeter update captured",
                "hint": ("The device streams IOMeter continuously in bridge mode. If "
                         "this stays empty, the session may be stale — disconnect() "
                         "then connect() — or in direct mode the stream may need the "
                         "app running to be subscribed.")}

    def fold(field):
        vals = [float(getattr(m, field, 0.0) or 0.0) for m in msgs]
        pk = max(vals)
        return {"peak": round(pk, 5), "last": round(vals[-1], 5),
                "peak_db": _meter_db(pk), "last_db": _meter_db(vals[-1])}

    ports = {label: fold(lvl) for label, lvl, _lim in _METER_PORTS}
    limiters = {}
    for label, _lvl, lim in _METER_PORTS:
        if lim:
            limiters[label] = any(bool(getattr(m, lim, 0.0)) for m in msgs)
    limiters["hp"] = any(bool(getattr(m, "hp_limiter_active", False)) for m in msgs)

    out = {"samples": len(msgs), "hold_s": hold_s, "ports": ports,
           "limiters": limiters, "any_limiting": any(limiters.values()),
           "scale": {"raw": "linear amplitude 0..1", "db_floor": METER_FLOOR_DB,
                     "db_ceiling": METER_CEIL_DB}}
    if detail:
        out["grid"] = {f: fold(f) for f in _METER_GRID}
        out["usb_out"] = {f: fold(f) for f in _METER_USB_OUT}
        out["usb_in"] = {f: fold(f) for f in _METER_USB_IN}
    return out


# ---------------------------------------------------------------- audio tools ----
# All of these need the optional audio extra: pip install -e '.[audio]'
def _audio_err(e):
    return {"error": str(e),
            "hint": "install the audio extra:  pip install -e '.[audio]'"}


@mcp.tool()
def audio_devices() -> dict:
    """LIST the Mac's CoreAudio devices and flag the Quad Cortex.

    The QC enumerates as 8 in / 8 out at a fixed 48 kHz. Device indices move as other
    interfaces come and go, so everything else here resolves the device BY NAME."""
    try:
        from . import audio_io
        devs = audio_io.list_devices()
    except Exception as e:
        return _audio_err(e)
    qc = [d for d in devs if d["is_quad_cortex"]]
    out = {"devices": devs, "quad_cortex": qc[0] if qc else None}
    if not qc:
        out["hint"] = "No Quad Cortex found — connect it over USB."
    return out


@mcp.tool()
def sample_arm(threshold_dbfs: float = -40.0, max_seconds: float = 30.0,
               silence_seconds: float = 1.5) -> dict:
    """ARM the reference-riff recorder — a looper pedal for the measurement stimulus.

    Records the DRY DI (host USB inputs 1/2), so the riff is the raw instrument, not
    something already coloured by a preset. Recording AUTO-STARTS on the first note
    above `threshold_dbfs` and AUTO-STOPS after `silence_seconds` of quiet (or at
    `max_seconds`, or on sample_stop). Returns immediately; poll sample_status.

    Record once per guitar/pickup setting — the sample is reused for every preset, and
    that is what makes the loudness comparisons between presets valid."""
    try:
        from . import audio_io
        return audio_io.sampler().arm(threshold_dbfs=threshold_dbfs,
                                      max_seconds=max_seconds,
                                      silence_seconds=silence_seconds)
    except Exception as e:
        return _audio_err(e)


@mcp.tool()
def sample_status() -> dict:
    """POLL the reference recorder: state (idle/armed/recording/done), seconds captured
    and a live input level in dBFS. Use it to tell the player when to start."""
    try:
        from . import audio_io
        return audio_io.sampler().status()
    except Exception as e:
        return _audio_err(e)


@mcp.tool()
def sample_stop(path: str = "") -> dict:
    """STOP the reference recording (the pedal's second press), trim the silence off
    both ends with short fades, save 24-bit/48 kHz WAV, and report duration, peak and
    LUFS. Defaults to ~/.qc-mcp/reference_di.wav."""
    try:
        from . import audio_io
        return audio_io.sampler().stop(path=path or None)
    except Exception as e:
        return _audio_err(e)


@mcp.tool()
def sample_discard() -> dict:
    """DISCARD the current take and return to idle (the recorder's undo)."""
    try:
        from . import audio_io
        return audio_io.sampler().discard()
    except Exception as e:
        return _audio_err(e)


@mcp.tool()
def measure_loudness(seconds: float = 6.0, channels: list = None,
                     perceived: bool = False) -> dict:
    """MEASURE what the device is putting out: integrated LUFS (BS.1770), short-term
    LUFS, true peak (dBTP), RMS and sample peak. `perceived=True` adds ISO 532-1
    Zwicker N5/N50 in relative sones — closer to the ear for dense high-gain tones, but
    it costs roughly 0.9x realtime, so leave it off for routine measurements.

    Captures host USB inputs 5/6 by default: those come from dedicated Grid USB output
    blocks, so the reading is free of the analog output stage and master volume. Host
    inputs 3/4 follow the analog outputs and would fold master volume into the number.
    Route the lane you want to measure to USB 5/6 (out_portid=14) first.

    A capture that is digital silence is reported as such with an error — on macOS a
    DENIED microphone permission returns silence rather than failing, and that must
    never be mistaken for a quiet preset."""
    try:
        from . import audio_io, loudness
        chans = tuple(channels) if channels else audio_io.DEFAULT_MEASURE_CHANNELS
        data, rate = audio_io.record(seconds, channels=chans)
        out = loudness.analyze(data, rate, perceived=perceived)
        out["channels_recorded"] = list(chans)
        return out
    except Exception as e:
        return _audio_err(e)


@mcp.tool()
def measure_preset(perceived: bool = False, di_path: str = "",
                   in_channels: list = None, out_channels: list = None) -> dict:
    """PLAY the reference riff into the current preset and measure what comes back.

    Sends the sample to host USB outputs 5/6, which land on The Grid's USB Input 5/6 —
    so the lane you want to measure must take USB 5/6 as its input (in_portid=12) and
    send its output to USB 5/6 (out_portid=14). Use with_reamp_routing to arrange that
    and put the routing back afterwards.

    Host outputs 1-4 are REFUSED: they bypass The Grid and go straight to the analog
    jacks, i.e. full-level audio into whatever the player is monitoring on."""
    try:
        from . import audio_io, loudness
        path = di_path or audio_io.DEFAULT_SAMPLE_PATH
        import os as _os
        if not _os.path.exists(path):
            return {"error": "no reference sample at %s" % path,
                    "hint": "record one first: sample_arm(), play a riff, sample_stop()"}
        stim, rate = audio_io.load_wav(path)
        rec, rate = audio_io.play_and_record(
            stim, rate=rate,
            out_channels=tuple(out_channels) if out_channels
            else audio_io.DEFAULT_REAMP_CHANNELS,
            in_channels=tuple(in_channels) if in_channels
            else audio_io.DEFAULT_MEASURE_CHANNELS)
        out = loudness.analyze(rec, rate, perceived=perceived)
        out["stimulus"] = path
        out["stimulus_s"] = round(stim.shape[0] / float(rate), 2)
        return out
    except Exception as e:
        return _audio_err(e)


@mcp.tool()
def compare_presets(reference_position: int = -1, positions: list = None,
                    setlist_key: str = "", di_path: str = "", row: int = 0,
                    perceived: bool = False) -> dict:
    """COMPARE presets against a reference — is the difference LEVEL or TONE?

    Plays the same riff through each preset and reports, per preset: the loudness gap in
    dB, a 9-band spectral balance difference, crest factor, and a verdict saying whether
    trimming would fix it. A tone difference is not fixable by leveling, and trying is
    how a preset ends up both wrong and quiet.

    `reference_position=-1` uses the current preset as the reference. Writes nothing."""
    try:
        from . import leveling, loudness as L, audio_io  # noqa: F401
    except Exception as e:
        return _audio_err(e)
    qc = _conn()

    def _measure_here():
        with leveling.reamp_routing(qc, row=row):
            from . import audio_io as A
            import os as _os
            path = di_path or A.DEFAULT_SAMPLE_PATH
            if not _os.path.exists(path):
                raise RuntimeError("no reference sample at %s — record one with "
                                   "sample_arm()" % path)
            stim, rate = A.load_wav(path)
            rec, rate = A.play_and_record(stim, rate=rate)
            return L.analyze(rec, rate, perceived=perceived, spectrum=True)

    try:
        if reference_position >= 0:
            qc.recall(folder_key=setlist_key, position=int(reference_position))
        ref = _measure_here()
        if ref.get("error"):
            return {"error": ref["error"]}
        rows = []
        for pos in (positions or []):
            qc.recall(folder_key=setlist_key, position=int(pos))
            got = _measure_here()
            if got.get("error"):
                rows.append({"position": int(pos), "error": got["error"]})
                continue
            delta = None
            if ref.get("lufs_integrated") is not None and got.get("lufs_integrated") is not None:
                delta = round(got["lufs_integrated"] - ref["lufs_integrated"], 2)
            spec = L.compare_spectra(ref.get("spectral_balance") or {},
                                     got.get("spectral_balance") or {})
            rows.append({
                "position": int(pos),
                "lufs": got.get("lufs_integrated"),
                "level_delta_db": delta,
                "crest_db": got.get("crest_db"),
                "spectral_diff": spec["bands"],
                "verdict": L.verdict(delta, spec),
            })
    except Exception as e:
        return {"error": str(e)}
    return {"reference": {"position": (reference_position if reference_position >= 0
                                       else "current"),
                          "lufs": ref.get("lufs_integrated"),
                          "crest_db": ref.get("crest_db"),
                          "spectral_balance": ref.get("spectral_balance")},
            "presets": rows,
            "note": "report only — nothing was written to the device"}


@mcp.tool()
def suggest_levels(positions: list = None, target_lufs: float = -18.0,
                   metric: str = "lufs", setlist_key: str = "",
                   di_path: str = "", row: int = 0) -> dict:
    """REPORT-ONLY: measure presets and report the correction each needs, in dB.

    Writes NOTHING to the device — it recalls each preset, plays the reference riff
    through it, measures, and puts the routing back. Use level_preset / level_setlist to
    apply. `metric="perceived"` levels by Zwicker loudness instead of LUFS, which suits
    a setlist mixing dense high-gain presets with cleans (slower: ~0.9x realtime).

    `positions` defaults to the current preset only. Record a reference riff first."""
    try:
        from . import leveling
    except Exception as e:
        return _audio_err(e)
    qc = _conn()
    rows = []
    try:
        if not positions:
            res = leveling.level_current(qc, target=target_lufs, metric=metric,
                                         row=row, di_path=di_path or None,
                                         dry_run=True)
            rows.append({"position": "current", **_level_row(res)})
        else:
            for pos in positions:
                qc.recall(folder_key=setlist_key, position=int(pos))
                res = leveling.level_current(qc, target=target_lufs, metric=metric,
                                             row=row, di_path=di_path or None,
                                             dry_run=True)
                rows.append({"position": int(pos), **_level_row(res)})
    except Exception as e:
        return {"error": str(e), "measured": rows}
    vals = [r["measured"] for r in rows if r.get("measured") is not None]
    return {"target": target_lufs, "metric": metric, "presets": rows,
            "spread": (round(max(vals) - min(vals), 2) if len(vals) > 1 else None),
            "note": "report only — nothing was written to the device"}


def _level_row(res):
    return {"measured": (res.get("iterations") or [{}])[0].get("measured"),
            "correction_db": res.get("suggested_db"),
            "true_peak_dbtp": (res.get("iterations") or [{}])[0].get("true_peak_dbtp"),
            "error": res.get("error")}


@mcp.tool()
def level_preset(target_lufs: float = -18.0, metric: str = "lufs",
                 tolerance: float = 0.5, max_iterations: int = 4, row: int = 0,
                 di_path: str = "", save: bool = False) -> dict:
    """LEVEL the current preset: measure, trim, re-measure until it lands on target.

    The correction goes to a Gain block at the end of the measured lane (added if
    absent) — amp master, cab and drive are never touched. A true-peak guard backs the
    trim off rather than pushing the preset into the output limiter.

    Leaves the preset DIRTY unless save=True. Returns every iteration so you can see the
    convergence. The reamp routing is restored even if the run fails."""
    try:
        from . import leveling
    except Exception as e:
        return _audio_err(e)
    qc = _conn()
    try:
        out = leveling.level_current(qc, target=target_lufs, metric=metric,
                                     tolerance=tolerance, max_iterations=max_iterations,
                                     row=row, di_path=di_path or None)
    except Exception as e:
        return {"error": str(e)}
    if save and out.get("written") and not out.get("error"):
        try:
            out["saved"] = save_preset()
        except Exception as e:
            out["save_error"] = str(e)
    return out


@mcp.tool()
def level_scenes(target_lufs: float = -18.0, scenes: list = None,
                 metric: str = "lufs", tolerance: float = 0.5, row: int = 0,
                 di_path: str = "", save: bool = False, dry_run: bool = False) -> dict:
    """LEVEL each SCENE of the current preset independently.

    Measures every requested scene (default: all 8), then writes the per-scene values of
    the Gain block's LEVEL in one pass. Scenes whose switch the device does not confirm,
    or that cannot be measured, are reported as skipped and left untouched.

    Per-scene values require the parameter to be assigned to scenes first; that is done
    for you. Returns to scene 0 when finished."""
    try:
        from . import leveling
    except Exception as e:
        return _audio_err(e)
    qc = _conn()
    try:
        out = leveling.level_scenes(qc, scenes=scenes, target=target_lufs,
                                    metric=metric, tolerance=tolerance, row=row,
                                    di_path=di_path or None, dry_run=dry_run)
    except Exception as e:
        return {"error": str(e)}
    if save and out.get("written"):
        try:
            out["saved"] = save_preset()
        except Exception as e:
            out["save_error"] = str(e)
    return out


@mcp.tool()
def level_setlist(positions: list, setlist_key: str = "", target_lufs: float = -18.0,
                  metric: str = "lufs", tolerance: float = 0.5, row: int = 0,
                  di_path: str = "", per_scene: bool = False,
                  save: bool = False) -> dict:
    """LEVEL several presets in sequence — the unattended pass over a setlist.

    Recalls each position, levels it (per preset, or per scene when per_scene=True),
    optionally saves, and moves on. Stops at the first device error and reports what was
    done up to that point rather than carrying on blindly.

    Measure first with suggest_levels to see the corrections before anything is written."""
    try:
        from . import leveling
    except Exception as e:
        return _audio_err(e)
    qc = _conn()
    done, failed = [], None
    for pos in positions:
        try:
            qc.recall(folder_key=setlist_key, position=int(pos))
            if per_scene:
                res = leveling.level_scenes(qc, target=target_lufs, metric=metric,
                                            tolerance=tolerance, row=row,
                                            di_path=di_path or None)
            else:
                res = leveling.level_current(qc, target=target_lufs, metric=metric,
                                             tolerance=tolerance, row=row,
                                             di_path=di_path or None)
            entry = {"position": int(pos), "converged": res.get("converged"),
                     "final_delta_db": res.get("final_delta_db"),
                     "final_trim_db": res.get("final_trim_db"),
                     "limited": res.get("limited", False), "error": res.get("error")}
            if save and res.get("written") and not res.get("error"):
                entry["saved"] = save_preset()
            done.append(entry)
            if res.get("error"):
                failed = "preset %s: %s" % (pos, res["error"])
                break
        except Exception as e:
            failed = "preset %s: %s" % (pos, e)
            break
    return {"target": target_lufs, "metric": metric, "per_scene": per_scene,
            "presets": done, "aborted_at": failed,
            "note": ("routing is restored after every preset; presets not listed were "
                     "not touched")}


@mcp.tool()
def switch_scene(scene: int) -> str:
    """WRITE: switch the active scene (0–7 = A–H) within the current preset.
    Scenes store per-block parameter values and bypass states."""
    _conn().set_scene(scene)
    return f"Switched to scene {'ABCDEFGH'[scene] if 0 <= scene < 8 else scene}."


@mcp.tool()
def switch_preset(position: int = 0, setlist_key: str = "/media/p4/Presets/My Presets",
                  is_factory: bool = False, downloads_cloud_id: str = "",
                  plugin_key: str = "") -> dict:
    """WRITE: switch the active preset. Addressing depends on the source:
      * My Presets / Factory: setlist_key + position (0-based) [+ is_factory].
      * Downloads (cloud):     downloads_cloud_id = the preset's cloud_id (from
                               search_directory's 'key'/list). folder+position do NOT
                               work for Downloads.
      * Plugin banks:          plugin_key.
    Returns the loaded preset's signal chain (captured from the push the device sends
    on switch — works whether connected directly or bridged to Cortex Control)."""
    import time
    qc = _conn()
    qc._pending.clear()
    if downloads_cloud_id:
        qc.recall(downloads_key=downloads_cloud_id)
    elif plugin_key:
        qc.recall(plugin_key=plugin_key)
    else:
        qc.recall(setlist_key, position, is_factory=is_factory)
    want = P.NAME_TO_CMD["RecallPreset"]
    bp = None
    deadline = time.time() + 3.5
    while time.time() < deadline and bp is None:
        qc._collect(0.15)
        for cmd, obj, raw, pb in list(qc._pending):
            if cmd == want and obj is not None and obj.HasField("preset") \
                    and sum(1 for ch in obj.preset.chains for m in ch.models if m.hash):
                bp = obj.preset
            qc._pending.remove((cmd, obj, raw, pb))
    return {"loaded": bool(bp), "preset": _preset_summary(bp) if bp else None}


# Performance-mode id map (Mode(14)) — FULLY captured live, ids 0-8.
#   BASE:   0=Preset, 1=Scene, 2=Stomp.
#   HYBRID: 3-8 = a "top row A-D / bottom row E-H" pairing of two base modes. The row
#           order is part of the id, following id = 3 + 2*top + bottom_rank (bottom_rank
#           = 0/1 = which of the two OTHER base modes sits on the bottom, lower id first):
#             3=Preset/Scene 4=Preset/Stomp | 5=Scene/Preset 6=Scene/Stomp |
#             7=Stomp/Preset 8=Stomp/Scene.  A hybrid always combines exactly 2 modes
#           (one per footswitch row). All nine ids verified on-device.
_BASE = {"preset": 0, "scene": 1, "stomp": 2}
MODES = dict(_BASE, hybrid=6,
             **{f"{a}+{b}": v for a, b, v in (
                 ("preset", "scene", 3), ("preset", "stomp", 4),
                 ("scene", "preset", 5), ("scene", "stomp", 6),
                 ("stomp", "preset", 7), ("stomp", "scene", 8))},
             **{f"{a}/{b}": v for a, b, v in (
                 ("preset", "scene", 3), ("preset", "stomp", 4),
                 ("scene", "preset", 5), ("scene", "stomp", 6),
                 ("stomp", "preset", 7), ("stomp", "scene", 8))})
MODE_NAMES = {0: "Preset", 1: "Scene", 2: "Stomp",
              3: "Preset+Scene Hybrid (Preset top A-D / Scene bottom E-H)",
              4: "Preset+Stomp Hybrid (Preset top A-D / Stomp bottom E-H)",
              5: "Scene+Preset Hybrid (Scene top A-D / Preset bottom E-H)",
              6: "Scene+Stomp Hybrid (Scene top A-D / Stomp bottom E-H)",
              7: "Stomp+Preset Hybrid (Stomp top A-D / Preset bottom E-H)",
              8: "Stomp+Scene Hybrid (Stomp top A-D / Scene bottom E-H)"}


@mcp.tool()
def get_mode() -> dict:
    """Read the current performance mode (footswitch behavior) and the mode cycle.
    Mode(14) READ. Returns {mode, mode_name, available_modes:[ids], cycle:[names]} —
    `available_modes` is the Preset/Scene/Stomp/Hybrid cycle the device steps through
    (BANK DOWN+TEMPO on the unit). Read-only. Ids: 0=Preset, 6=Scene+Stomp Hybrid."""
    m = _conn().read_state("Mode", timeout_ms=3000)
    if m is None:
        return {"note": "no Mode reply"}
    avail = [int(x) for x in m.available_modes.modes] if m.HasField("available_modes") else []
    return {"mode": int(m.mode), "mode_name": MODE_NAMES.get(int(m.mode), f"#{m.mode}"),
            "available_modes": avail,
            "cycle": [MODE_NAMES.get(i, f"#{i}") for i in avail]}


@mcp.tool()
def switch_mode(mode) -> dict:
    """WRITE: set the active performance mode (footswitch behavior). Accepts a name
    ('preset', 'hybrid'/'scene+stomp') or a raw numeric id. Mode(14) UPDATE {mode};
    the device echoes it. The target must be in the preset's available_modes cycle
    (see get_mode) — switching to an unavailable mode is refused. Confirmed ids:
    0=Preset, 6=Scene+Stomp Hybrid."""
    mid = MODES.get(str(mode).lower()) if not isinstance(mode, int) else mode
    if mid is None:
        return {"error": f"unknown mode {mode!r}. Known: {sorted(set(MODES))} or an id."}
    qc = _conn()
    avail = []
    cur = qc.read_state("Mode", timeout_ms=2000)
    if cur is not None and cur.HasField("available_modes"):
        avail = [int(x) for x in cur.available_modes.modes]
    if avail and mid not in avail:
        return {"error": f"mode {mid} not in this preset's cycle {avail} — set the "
                         "cycle first with set_mode_cycle(...)."}
    qc.set_mode(mid)
    return {"mode": mid, "mode_name": MODE_NAMES.get(mid, f"#{mid}")}


@mcp.tool()
def set_mode_cycle(modes: list) -> dict:
    """WRITE: set which performance modes the footswitch cycle steps through
    (Modes Configuration). Mode(14) UPDATE {available_modes{modes:[...]}}; e.g.
    [0, 6] = Preset <-> Scene+Stomp Hybrid. Accepts names or ids. NOTE: if the
    currently-active mode is dropped from the cycle, the device falls back to Preset
    (0) — confirmed live. Ids: 0=Preset, 6=Scene/Stomp hybrid, 8=Stomp/Scene (swapped
    rows). Does not itself change the active mode (call switch_mode after)."""
    ids = []
    for x in modes:
        v = MODES.get(str(x).lower()) if not isinstance(x, int) else x
        if v is None:
            return {"error": f"unknown mode {x!r} in {modes}."}
        ids.append(v)
    _conn().set_mode_cycle(ids)
    return {"available_modes": ids,
            "cycle": [MODE_NAMES.get(i, f"#{i}") for i in ids]}


@mcp.tool()
def get_io_settings() -> dict:
    """Read the hardware I/O port settings: inputs (level, type instrument/line,
    impedance/Z-mode, ground lift, plugged), outputs (level, mute, ground lift),
    headphones, USB, and input/output pairing. Read-only."""
    io = _conn().read_message("IOSettings")
    if io is None or not io.HasField("settings"):
        return {"note": "no IO settings returned"}
    s = io.settings
    def ins(p):
        return {"port": p.input_port_id, "plugged": p.plugged,
                "level": round(p.level, 3),
                "type": "instrument" if round(p.input_type) == 0 else "line/mic",
                "impedance": round(p.input_zmode, 3),
                "ground_lift": bool(p.ground_lift)}
    def outs(p):
        return {"port": p.output_port_id, "plugged": p.plugged,
                "level": round(p.level, 3), "mute": p.mute,
                "ground_lift": bool(p.ground_lift)}
    return {
        "input_pairing_xlr1_2": io.xlr1_2_linked,
        "output_pairing_3_4": io.out3_4_linked,
        "inputs": [ins(p) for p in s.in_port],
        "outputs": [outs(p) for p in s.out_port],
        "headphones": ({"level": round(s.hp_port.level, 3),
                        "plugged": s.hp_port.plugged} if s.HasField("hp_port") else None),
        "usb": ({"level": round(s.usb_port.level, 3),
                 "dry_wet": round(s.usb_port.dry_wet, 3)} if s.HasField("usb_port") else None),
    }


def _norm(model_hash, param_index, display_value):
    """Convert a display value to the normalized 0-1 the device stores (taper-aware)."""
    return catalog.to_norm(model_hash, param_index, display_value)


@mcp.tool()
def build_preset(spec: dict, clear: bool = True) -> dict:
    """Build a whole preset from a spec (the way to create multiamp-class presets
    from a prompt). Clears the grid then lays out chains/blocks/routing/splitters
    and sets params. Grid is 4 rows x 8 columns; rows 1&3 = Path A, 2&4 = Path B.

    spec = {
      "name": "My Rig",
      "chains": [
        { "row": 0, "in_port": 1, "out_port": 16,
          "splitter": {"split_col": -1, "mix_col": 6},   # optional (parallel)
          "mixer": true,                                  # optional
          "blocks": [
            {"col": 1, "hash": 27, "params": {"0": 5.0}}, # params are DISPLAY values,
            {"col": 3, "hash": 1166}                       #   keyed by param index
          ] } ] }

    Find device hashes with find_devices. Returns the resulting topology.
    WRITE. Does not persist until you save; spec `name` is NOT applied to the live
    grid (preset names live on the preset FILE) — pass it to save_preset /
    save_preset_as, which is what actually names the preset."""
    qc = _conn()
    if clear:
        qc.clear_grid()
        import time
        time.sleep(0.3)
    import time
    for ch in spec.get("chains", []):
        row = ch.get("row", 0)
        if ch.get("in_port") is not None or ch.get("out_port") is not None:
            qc.set_routing(row, ch.get("in_port"), ch.get("out_port")); time.sleep(0.06)
        sp = ch.get("splitter")
        if sp:
            qc.set_split_points(row, sp.get("split_col", -1),
                                sp.get("mix_col", -1)); time.sleep(0.06)
        for blk in ch.get("blocks", []):
            qc.add_block(blk["hash"], row=row, column=blk["col"]); time.sleep(0.06)
            for pidx, disp in (blk.get("params") or {}).items():
                qc.set_param(row, blk["col"], int(pidx),
                             _norm(blk["hash"], int(pidx), disp)); time.sleep(0.03)
    time.sleep(0.5)
    bp = qc.get_current_preset()
    return _preset_summary(bp) if bp else {"note": "built; read-back pending"}


@mcp.tool()
def add_block(row: int, column: int, device_hash: int, params: dict = None) -> str:
    """WRITE: place a device block at grid (row 0-3, column 0-7). params = {param
    index: DISPLAY value}. Use find_devices for hashes."""
    qc = _conn()
    qc.add_block(device_hash, row=row, column=column)
    import time
    for pidx, disp in (params or {}).items():
        time.sleep(0.05)
        qc.set_param(row, column, int(pidx), _norm(device_hash, int(pidx), disp))
    return f"Added {catalog.name_of(device_hash)} at row{row} col{column}."


@mcp.tool()
def add_capture(row: int, column: int, capture_key: str = "", capture_name: str = "",
                version: str = "v1", query: str = "") -> dict:
    """WRITE: place a Neural Capture block at grid (row, column) and load a capture
    into it. Provide either capture_key (+capture_name) from search_directory, OR a
    `query` to look one up by name in the on-device captures. version 'v1'|'v2' picks
    the V1/V2 capture block. The capture must already be on the device (factory, or
    downloaded via Cortex Cloud)."""
    from . import directory
    if query and not capture_key:
        hits = directory.search(_catalog(), query, category="captures", limit=1)
        if not hits:
            return {"error": f"no on-device capture matches {query!r}"}
        capture_key = hits[0]["key"]; capture_name = hits[0]["name"]
    if not capture_key:
        return {"error": "provide capture_key (from search_directory) or query"}
    _conn().set_capture(row, column, capture_key, capture_name, version=version)
    return {"placed": "Neural Capture", "version": version, "row": row, "column": column,
            "capture": capture_name or capture_key[:12]}


@mcp.tool()
def add_ir(row: int, column: int, ir_key: str = "", ir_name: str = "",
           stereo: bool = False, query: str = "") -> dict:
    """WRITE: place an IR-loader block at grid (row, column) and load an impulse
    response. Provide ir_key (a `CIR_…` key from search_directory category='irs')
    +ir_name, OR a `query` to look one up by name. stereo picks the mono/stereo loader.
    The IR must already be on the device."""
    from . import directory
    if query and not ir_key:
        hits = directory.search(_catalog(), query, category="irs", limit=1)
        if not hits:
            return {"error": f"no on-device IR matches {query!r}"}
        ir_key = hits[0]["key"]; ir_name = hits[0]["name"]
    if not ir_key:
        return {"error": "provide ir_key (from search_directory category='irs') or query"}
    _conn().set_ir(row, column, ir_key, ir_name, stereo=stereo)
    return {"placed": "IR loader", "stereo": stereo, "row": row, "column": column,
            "ir": ir_name or ir_key}


@mcp.tool()
def add_split(row: int, split_col: int = 0, mix_col: int = -1) -> str:
    """WRITE: set a lane's split/mix points — COLUMN indices, not percentages.
    split_col = the column the signal branches BEFORE (put it after the shared blocks);
    mix_col = the column the branch merges back at, or -1 for a no-merge tap (feeds the
    row below without polluting this lane — the shared-front trick). (-1, -1) removes
    the split. See build_preset's `splitter` and the build-preset-routing skill."""
    qc = _conn()
    qc.set_split_points(row, int(split_col), int(mix_col))
    return f"Lane {row} split at col {split_col}, mix at col {mix_col}."


@mcp.tool()
def set_lane_routing(row: int, in_portid: int = None, out_portid: int = None) -> str:
    """WRITE: set a grid lane's input/output routing (portids). in=1 main input, out=19
    main output; 0 = idle. Mirrors clear_grid's clean routing."""
    _conn().set_routing(row, in_portid=in_portid, out_portid=out_portid)
    return f"Routed lane {row}: in={in_portid} out={out_portid}."


@mcp.tool()
def set_lane_output(row: int, pan: float = None, volume: float = None,
                    mute: bool = None, solo: bool = None) -> str:
    """WRITE: set a grid lane's output (Lane Output Control): pan (0.0=hard L, 0.5=
    center, 1.0=hard R), volume (0.0-1.0), mute, solo. For a STEREO multi-amp blend,
    pan the parallel BRANCH amp lanes (e.g. one ~0.3 left, one ~0.7 right) and leave
    the main/output row centered — panning the output row pans the whole mix."""
    qc = _conn()
    import time
    # LaneOutputControl param order: VOLUME=0, PAN=1, MUTE=2, SOLO=3
    updates = [(0, volume), (1, pan)]
    if mute is not None:
        updates.append((2, 1.0 if mute else 0.0))
    if solo is not None:
        updates.append((3, 1.0 if solo else 0.0))
    done = {}
    for idx, val in updates:
        if val is not None:
            qc.set_lane_param(row, "output_control", idx, float(val))
            done[{0: "volume", 1: "pan", 2: "mute", 3: "solo"}[idx]] = float(val)
            time.sleep(0.05)
    return f"Set lane {row} output {done}."


@mcp.tool()
def set_mixer(row: int, level_a: float = None, pan_a: float = None,
              level_b: float = None, pan_b: float = None, phase: bool = None,
              mixer_level: float = None, column: int = 0) -> str:
    """WRITE: set the MIXER at a lane's merge point (the magenta 'M' node created by
    a split whose mix_col >= 0). pan_a/pan_b: 0.0=hard L, 0.5=center, 1.0=hard R —
    this is how you get STEREO WIDTH from a merged multi-amp rig (pan A left, B right).
    A = the main lane, B = the branch that merges in. Levels are 0.0-1.0.
    PAN SCALE (verified on-device): the QC displays pan as 0-50 per side, so the
    reading is (0.5 - value) * 100 — pan_a=0.25 shows "25 L", 0.375 shows "13 L",
    and 0.0/1.0 are hard L/R shown as "50". Halve the number you want, then offset
    from 0.5.
    NOTE the mixer is a lane SUB-BLOCK, not a grid block: it has no row/column cell and
    does NOT appear in get_current_preset's `blocks`, so set_parameter(row, col, ...)
    silently does nothing to it — use this tool."""
    qc = _conn()
    import time
    # Mixer param order: LEVEL A=0, PAN A=1, LEVEL B=2, PAN B=3, PHASE=4, MIXER LEVEL=5
    names = {0: "level_a", 1: "pan_a", 2: "level_b", 3: "pan_b",
             4: "phase", 5: "mixer_level"}
    updates = [(0, level_a), (1, pan_a), (2, level_b), (3, pan_b),
               (4, None if phase is None else (1.0 if phase else 0.0)),
               (5, mixer_level)]
    done = {}
    for idx, val in updates:
        if val is not None:
            qc.set_lane_param(row, "mixer", idx, float(val), column=column)
            done[names[idx]] = float(val)
            time.sleep(0.05)
    if not done:
        return "No mixer values given (nothing sent)."
    return f"Set lane {row} mixer {done}."


@mcp.tool()
def set_preset_meta(name: str = None, tempo: int = None, default_scene: int = None,
                    scene_labels: list = None, scene_colors: list = None) -> str:
    """WRITE: set preset metadata on the LIVE preset. scene_labels / scene_colors (up
    to 8 entries for scenes A-H; None entries skipped, "" clears) use the VERIFIED
    SceneLabel/SceneColor ops. `name` cannot be set live — preset names live on the
    preset file; pass it to save_preset / save_preset_as instead (ignored here with a
    warning). tempo/default_scene are best-effort (unverified op). Unlabeled scenes
    that hold scene data show as "Undefined" on the device — label the ones you use."""
    _conn().set_preset_meta(tempo=tempo, default_scene=default_scene,
                            scene_labels=scene_labels, scene_colors=scene_colors)
    out = f"Set preset meta: scene_labels={scene_labels} scene_colors={scene_colors}"
    if tempo is not None or default_scene is not None:
        out += f" tempo={tempo} default_scene={default_scene} (best-effort/unverified)"
    if name is not None:
        out += (f". NOTE: name={name!r} NOT applied — names are set by the save op; "
                "pass it to save_preset/save_preset_as.")
    return out + "."


def _slot_occupant(folder_key, index):
    """Best-effort: the preset file already at (folder_key, index) per the DIRECTORY
    catalog, or None if the slot looks empty / the catalog can't tell."""
    cat = _catalog()
    for fo in (cat or {}).get("presets", []):
        if fo.get("key") == folder_key:
            for f in fo.get("files", []):
                if f.get("index") == index:
                    return f
    return None


@mcp.tool()
def list_empty_slots(setlist_key: str = "/media/p4/Presets/My Presets",
                     limit: int = 8) -> dict:
    """Free ("Unsaved") preset positions in a setlist folder — the SAFE build targets
    (never build over a named preset). A setlist is a fixed 256-slot table; free slots
    are listed with an EMPTY name. Positions map to banks: 0-7 = 1A-1H, 8-15 = 2A-2H, …
    Read-only. NOTE `source`: 'snapshot' can be stale — a slot saved since the snapshot
    may still show empty; verify the target with current_preset_position/read if unsure."""
    cur = None    # the slot the user has OPEN — if it's empty, it's the intended target
    try:
        p = _conn().get_setlist_position() or {}
        if p.get("folder_key") == setlist_key and not p.get("is_factory"):
            cur = int(p.get("position"))
    except Exception:
        pass
    cat = _catalog()
    for fo in (cat or {}).get("presets", []):
        if fo.get("key") == setlist_key:
            files = fo.get("files", [])
            present = {f.get("index") for f in files}
            free = sorted({f["index"] for f in files if not f.get("name")} |
                          {i for i in range(256) if i not in present})
            def label(i):
                return f"{i // 8 + 1}{'ABCDEFGH'[i % 8]}"
            out = {"setlist": setlist_key, "source": _catalog_source}
            if cur is not None and cur in free:
                free.remove(cur)
                free.insert(0, cur)
                out["recommended"] = {"position": cur, "slot": label(cur),
                                      "why": "currently OPEN on the device and empty — "
                                             "the user likely selected it on purpose; "
                                             "build here (no recall needed)."}
            out["empty_positions"] = [{"position": i, "slot": label(i)}
                                      for i in free[:limit]]
            return out
    out = {"error": f"folder {setlist_key!r} not in the directory catalog",
           "source": _catalog_source}
    if _catalog_source == "empty":
        out["bootstrap"] = _BOOTSTRAP_HINT
    return out


@mcp.tool()
def save_preset_as(name: str, setlist_key: str = "", position: int = -1,
                   overwrite: bool = False) -> dict:
    """WRITE: save the CURRENT working grid as preset `name` via File CREATE — the same
    op Cortex Control's Save uses (File{action=CREATE, folder{key, files{index, name}}},
    no payload; the device commits its live grid). Defaults target the currently loaded
    slot. SAFETY: refuses to write into a slot that already holds a preset with a
    different name unless overwrite=True — use list_empty_slots to pick a free target.
    Never uses RecallPreset SAVE (which hangs on an Unsaved slot)."""
    qc = _conn()
    pos = qc.get_setlist_position() or {}
    folder = setlist_key or pos.get("folder_key") or "/media/p4/Presets/My Presets"
    idx = position if position >= 0 else int(pos.get("position", 0))
    occ = _slot_occupant(folder, idx)
    if occ and occ.get("name") and occ["name"] != name and not overwrite:
        return {"error": f"slot {idx} in {folder!r} already holds {occ['name']!r} — "
                         "pass an empty position (see list_empty_slots) or overwrite=True."}
    qc.write_preset_file(folder, idx, name)
    return {"saved_as": name, "setlist": folder, "position": idx,
            "replaced": (occ or {}).get("name") or None}


@mcp.tool()
def remove_block(row: int, column: int) -> str:
    """WRITE: remove the block at grid (row, column)."""
    _conn().delete_block(row, column)
    return f"Removed block at row{row} col{column}."


@mcp.tool()
def set_parameter(row: int, column: int, param_index: int, display_value: float,
                  device_hash: int = 0) -> str:
    """WRITE: set a block parameter to a DISPLAY value (converted using the device's
    range if device_hash is given, else sent as-is 0-1)."""
    val = _norm(device_hash, param_index, display_value) if device_hash else display_value
    _conn().set_param(row, column, param_index, val)
    return f"Set param {param_index} = {display_value} at row{row} col{column}."


@mcp.tool()
def set_parameter_scenes(row: int, column: int, param_index: int, values: list,
                         device_hash: int = 0) -> str:
    """WRITE: set a parameter's per-scene values at once (values = up to 8 DISPLAY
    values, one per scene A-H). Marks the param scene-varying. If device_hash is given,
    values are converted from display units; else sent as-is (0-1)."""
    vals = [_norm(device_hash, param_index, v) if device_hash else v for v in values]
    _conn().set_param_scenes(row, column, param_index, vals)
    return f"Set param {param_index} across {len(vals)} scenes at row{row} col{column}."


@mcp.tool()
def set_block_bypass(row: int, column: int, bypassed: bool = True,
                     scenes: list = None) -> str:
    """WRITE: bypass (bypassed=True) or re-enable a block at grid (row, column).
    scenes: omit to apply to the current scene, or pass up to 8 booleans for explicit
    per-scene (A-H) bypass states. KNOWN LIMIT: per-scene bypass is verified on
    drive/amp blocks but is a SILENT NO-OP on Delay blocks (e.g. Analog Delay 6001;
    likely a different bypass path for trails-capable blocks) — for delays,
    scene-control the MIX param instead (0 = off; also preserves trails)."""
    _conn().set_block_bypass(row, column, bypassed=bypassed, scenes=scenes)
    state = "bypassed" if (bypassed and scenes is None) else ("per-scene" if scenes else "enabled")
    return f"Block at row{row} col{column} -> {state}."


@mcp.tool()
def clear_grid() -> str:
    """WRITE: delete all blocks in the current preset (start from empty)."""
    n = _conn().clear_grid()
    return f"Cleared {n} blocks."


@mcp.tool()
def save_preset(name: str = "") -> str:
    """WRITE: persist the current working grid to the loaded slot via File CREATE (the
    app's real Save op). Pass `name` for a new/Unsaved slot; if omitted, reuses the
    slot's existing preset name. Does NOT use RecallPreset SAVE (that hangs an Unsaved
    slot). To save into a DIFFERENT slot use save_preset_as."""
    qc = _conn()
    pos = qc.get_setlist_position() or {}
    folder = pos.get("folder_key") or "/media/p4/Presets/My Presets"
    idx = int(pos.get("position", 0))
    if not name:
        # reuse the slot's existing name: directory occupant first (authoritative),
        # then the working preset's own name field
        occ = _slot_occupant(folder, idx)
        name = (occ or {}).get("name") or ""
        if not name:
            bp = qc.get_current_preset()
            name = getattr(bp, "name", "") or ""
        if not name:
            return ("This slot is Unsaved and the working preset has no name — "
                    "pass name= explicitly (a nameless save would create 'Untitled').")
    qc.write_preset_file(folder, idx, name)
    return f"Saved preset as '{name}' (pos {idx})."


@mcp.tool()
def find_devices(query: str = "", category: str = "", limit: int = 25) -> list:
    """Search the device catalog (amps, cabs, pedals, …) by name, emulated gear,
    or category. Returns hash/id, name, category, and the real gear it's based on.
    Read-only. Examples: find_devices(category='Guitar Amplifier'),
    find_devices(query='Marshall'), find_devices(query='DLX', category='Amp')."""
    res = catalog.find(query or None, category or None)[:limit]
    return [{"hash": m["id"], "name": m["name"], "category": m["category"],
             "based_on": m["tm"]} for m in res]


_catalog_cache = None
_catalog_source = None       # 'device' | 'snapshot' | 'empty' — where the cache came from


def _snapshot_path():
    """On-disk DIRECTORY snapshot (structure_directory output + _favorites/_counts).
    Written by tools/gui/dump_catalog.py and refreshed here after a good live read."""
    return os.environ.get("QC_CATALOG_JSON") or os.path.join(
        os.path.dirname(__file__), "..", "..", "interceptor", "catalog.json")


def _catalog_file_count(cat):
    from . import directory
    return sum(v["files"] for v in directory.counts(cat).values())


def _load_snapshot():
    import json
    try:
        with open(_snapshot_path()) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _save_snapshot(cat):
    import json
    from . import directory
    try:
        out = dict(cat)
        out.setdefault("_counts", directory.counts(cat))
        with open(_snapshot_path(), "w") as fh:
            json.dump(out, fh, indent=1)
    except (OSError, TypeError):
        pass


def _catalog(refresh=False):
    """Cached device DIRECTORY (presets / IRs / captures). Prefers a live read — which
    works in BOTH bridge and direct mode (one File READ; the device streams the whole
    catalog in ~12s). Falls back to the on-disk snapshot if the live read is worse than
    the snapshot (e.g. interrupted stream). A good live read refreshes the snapshot,
    keeping it warm. Cached per session; refresh=True re-pulls."""
    global _catalog_cache, _catalog_source
    if _catalog_cache is not None and not refresh:
        return _catalog_cache
    try:
        live = _conn().list_directory()
    except Exception:
        live = None
    live_n = _catalog_file_count(live) if live else 0
    snap = _load_snapshot()
    snap_n = _catalog_file_count(snap) if snap else 0
    # Bridge reads often return only a partial trickle (e.g. 0 presets, a few
    # captures), so a nonzero count isn't enough — trust the live read only when it's
    # at least as complete as the snapshot; otherwise use the snapshot. A good live
    # read refreshes the snapshot, keeping it warm for later bridge sessions.
    if live and live_n >= snap_n and live_n > 0:
        _catalog_cache, _catalog_source = live, "device"
        _save_snapshot(live)
    elif snap and snap_n > 0:
        _catalog_cache, _catalog_source = snap, "snapshot"
    else:
        _catalog_cache = live or {"presets": [], "irs": [], "captures": []}
        _catalog_source = "empty"
    return _catalog_cache


_BOOTSTRAP_HINT = (
    "No directory snapshot yet (interceptor/catalog.json is gitignored — it holds "
    "personal library names). Call directory_summary(refresh=True): the live listing "
    "works in BOTH bridge and direct mode (takes ~15s — the device streams the whole "
    "catalog) and auto-saves the snapshot. Fallback if the live read fails: "
    "tools/gui/dump_catalog.py mines the app's own listing from the interposer log.")


@mcp.tool()
def directory_summary(refresh: bool = False) -> dict:
    """Counts of the on-device DIRECTORY: presets, IRs (impulse responses), and neural
    captures, each as {folders, files}. Read-only. Cached per session. If source is
    'empty', the returned bootstrap hint explains how to (re)generate the snapshot."""
    from . import directory
    out = directory.counts(_catalog(refresh))
    out["source"] = _catalog_source   # 'device' | 'snapshot' | 'empty'
    if _catalog_source == "empty":
        out["bootstrap"] = _BOOTSTRAP_HINT
    return out


@mcp.tool()
def search_directory(query: str = "", category: str = "", limit: int = 30,
                     refresh: bool = False) -> list:
    """Search the on-device DIRECTORY by name. category = 'presets' | 'captures' |
    'irs' (blank = all). Returns items with folder_key + position so a preset hit can
    be loaded via switch_preset(position, setlist_key=folder_key, is_factory=...).
    Capture/IR hits include their content 'key' (hash) used to reference them in a
    block. Read-only. Examples: search_directory('SRV','presets'),
    search_directory('Laney','captures')."""
    from . import directory
    cat = category or None
    hits = directory.search(_catalog(refresh), query, category=cat, limit=limit)
    return [{"name": h["name"], "category": h["category"],
             "folder_name": h["folder_name"], "setlist_key": h["folder_key"],
             "position": h["index"], "is_factory": h["is_factory"],
             "is_downloads": h["folder_key"].startswith("cloud-0"),
             "downloads_cloud_id": h.get("cloud_id", ""),
             "author": h["author"], "key": h["key"]} for h in hits]


@mcp.tool()
def list_favorites(favorites: bool = True) -> list:
    """The device's Favorites (favorites=True) or Recents (favorites=False) preset
    list: [{name, folder_name, setlist_key, is_factory, is_plugin}]. Load one via
    switch_preset. Read-only."""
    items = _conn().list_recents_favorites(favorites=favorites)
    if not items:
        # bridge read empty -> fall back to the snapshot's captured favorites/recents
        snap = _load_snapshot() or {}
        items = (snap.get("_favorites") or {}).get(
            "favorites" if favorites else "recents", [])
    return [{"name": it["name"], "folder_name": it["folder_name"],
             "setlist_key": it["folder_key"], "is_factory": it["is_factory"],
             "is_plugin": it["is_plugin"]} for it in items]


@mcp.tool()
def current_preset_position() -> dict:
    """The currently loaded preset's pointer: {folder_key, position, is_factory}.
    Read-only (SetlistPosition READ)."""
    return _conn().get_setlist_position() or {"note": "no position reported"}


def main():
    mcp.run()


if __name__ == "__main__":
    main()
