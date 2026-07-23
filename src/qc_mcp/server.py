"""MCP server exposing Quad Cortex control over its reverse-engineered USB-HID
protocol. See PROTOCOL.md for the full protocol.

Capabilities: read the live preset (fx blocks, grid positions, per-lane input/
output blocks, params) the way Cortex Control does on boot; read hardware I/O
settings and CPU load; switch presets, scenes (A-H), and performance modes
(Preset=0 / Hybrid=6); recall presets; set master volume; and search the 533-
device catalog (name / emulated gear / parameters).

The connection maintains the session + KeepAlive heartbeat the QC requires before
it will stream state and answer READs (see transport.QuadCortex).

Exclusive HID access — **Cortex Control must be quit** while this server is
connected to the device (and vice-versa).
"""
from __future__ import annotations
import os
import threading

from mcp.server.fastmcp import FastMCP

from . import protocol as P
from . import catalog
from .transport import QuadCortex, QCError

mcp = FastMCP("quad-cortex")

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
        chains.append({"row": ch.row, "in_port": ch.in_portid,
                       "out_port": ch.out_portid, "blocks": blocks,
                       "input_block": lane_ctrl(ch.input_control),
                       "output_block": lane_ctrl(ch.output_control)})
    return {"name": bp.name, "tempo": bp.tempo, "default_scene": bp.default_scene,
            "scene_labels": [s for s in bp.scene_labels],
            "num_chains": len(bp.chains), "chains": chains}


@mcp.tool()
def connect() -> str:
    """Connect to the Quad Cortex over USB-HID. Cortex Control must be quit
    (it holds the device exclusively). Safe/read-only."""
    try:
        qc = _conn()
        v = qc.read_state("Version")
        return f"Connected. Firmware {getattr(v, 'zenos_git_hash', '?')}."
    except QCError as e:
        return f"Connect failed: {e}"


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
    preset on the device. Returns the loaded preset's signal chain if the QC
    pushes it back."""
    qc = _conn()
    cls = P.message_class("SetlistPosition")
    m = cls(action=P.ACTION["UPDATE"], request_id=qc.next_request_id(),
            position=position, is_factory=is_factory)
    if setlist_key:
        m.folder_key = setlist_key
    qc.send("SetlistPosition", m)
    import time
    deadline = time.time() + capture_seconds
    while time.time() < deadline:
        qc._collect(0.2)
        for cmd, obj, raw, pb in list(qc._pending):
            if cmd == P.NAME_TO_CMD["RecallPreset"] and obj is not None \
                    and obj.HasField("preset"):
                return {"recalled_position": position,
                        "preset": _preset_summary(obj.preset)}
    return {"recalled_position": position, "note": "sent; no preset echo captured"}


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
def cpu_load() -> dict:
    """Read the current DSP/CPU load (the QC streams this). Read-only."""
    qc = _conn()
    import time
    deadline = time.time() + 2.0
    while time.time() < deadline:
        qc._collect(0.2)
        for cmd, obj, raw, pb in list(qc._pending):
            if cmd == P.NAME_TO_CMD["CPULoad"] and obj is not None:
                return _fields(obj)
    return {"note": "no CPULoad update captured"}


@mcp.tool()
def switch_scene(scene: int) -> str:
    """WRITE: switch the active scene (0–7 = A–H) within the current preset.
    Scenes store per-block parameter values and bypass states."""
    _conn().set_scene(scene)
    return f"Switched to scene {'ABCDEFGH'[scene] if 0 <= scene < 8 else scene}."


@mcp.tool()
def switch_preset(position: int, setlist_key: str = "/media/p4/Presets/My Presets",
                  is_factory: bool = False) -> dict:
    """WRITE: switch to a preset by setlist + position (0-based). Returns the
    loaded preset's signal chain (the device pushes it on switch)."""
    qc = _conn()
    qc.recall(setlist_key, position, is_factory=is_factory)
    import time
    time.sleep(0.6)
    bp = qc.get_current_preset()
    return {"position": position, "preset": _preset_summary(bp) if bp else None}


# Performance-mode id map (confirmed live: 0=Preset, 6=Hybrid; Scene/Stomp TBD).
MODES = {"preset": 0, "hybrid": 6}
MODE_NAMES = {v: k for k, v in MODES.items()}


@mcp.tool()
def switch_mode(mode) -> str:
    """WRITE: set the performance mode (footswitch behavior). Accepts a name
    ('preset', 'hybrid') or a raw numeric id. Confirmed on this unit: 0=Preset,
    6=Hybrid. A preset only allows the modes in its available_modes list."""
    mid = MODES.get(str(mode).lower()) if not isinstance(mode, int) else mode
    if mid is None:
        return f"Unknown mode {mode!r}. Known: {list(MODES)} (or a numeric id)."
    _conn().set_mode(mid)
    return f"Set mode {mid} ({MODE_NAMES.get(mid, '?')})."


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
    WRITE. Does not persist until you call save_preset."""
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
def clear_grid() -> str:
    """WRITE: delete all blocks in the current preset (start from empty)."""
    n = _conn().clear_grid()
    return f"Cleared {n} blocks."


@mcp.tool()
def save_preset(name: str = "") -> str:
    """WRITE: persist the current grid to the loaded preset slot (RecallPreset SAVE)."""
    _conn().save_preset()
    return f"Saved preset{(' as ' + name) if name else ''}."


@mcp.tool()
def find_devices(query: str = "", category: str = "", limit: int = 25) -> list:
    """Search the device catalog (amps, cabs, pedals, …) by name, emulated gear,
    or category. Returns hash/id, name, category, and the real gear it's based on.
    Read-only. Examples: find_devices(category='Guitar Amplifier'),
    find_devices(query='Marshall'), find_devices(query='DLX', category='Amp')."""
    res = catalog.find(query or None, category or None)[:limit]
    return [{"hash": m["id"], "name": m["name"], "category": m["category"],
             "based_on": m["tm"]} for m in res]


def main():
    mcp.run()


if __name__ == "__main__":
    main()
