"""High-level Quad Cortex transport over IOKit HID (see iohid.py).

Requires exclusive HID access (opens with seize), so Cortex Control must be
quit while connected. Note: IOHIDDeviceSetReport returns a benign 0xe0005000 on
this device (the official app ignores it too) — it is NOT an error.
"""
from __future__ import annotations
import threading
import time

from . import protocol as P
from .iohid import IOHIDTransport


class QCError(Exception):
    pass


class QuadCortex:
    def __init__(self, session_id="claudemcp0000000000000000000000", bridge=False):
        # bridge=True shares Cortex Control's live session over the interposer
        # FIFOs (app + MCP run simultaneously); otherwise seize the device directly.
        self.bridge = bridge
        if bridge:
            from .bridge import FifoBridge
            self.io = FifoBridge()
        else:
            self.io = IOHIDTransport(seize=True)
        self._rx = P.Reassembler()
        self._req_id = 100
        self._session_id = session_id
        self._pending = []  # decoded (cmd, obj, raw) not yet consumed
        self._send_lock = threading.Lock()
        self._hb_stop = None
        self._hb_thread = None

    # -- lifecycle --
    def open(self, handshake=True):
        self.io.open()
        # In bridge mode Cortex Control already owns the handshake + heartbeat.
        if handshake and not self.bridge:
            self._start_heartbeat()   # keep session "online" (required for reads)
            self._handshake()
        return self

    def close(self):
        self._stop_heartbeat()
        self.io.close()

    # -- heartbeat: the QC only streams/answers reads while it receives a
    #    steady KeepAlive{action:UPDATE, is_online:true}. --
    def _start_heartbeat(self, interval=0.2):
        if self._hb_thread:
            return
        self._hb_stop = threading.Event()
        ka = P.message_class("KeepAlive")()
        for f, v in (("action", P.ACTION["UPDATE"]), ("is_online", True)):
            if f in [x.name for x in ka.DESCRIPTOR.fields]:
                setattr(ka, f, v)

        def loop():
            while not self._hb_stop.is_set():
                try:
                    self.send("KeepAlive", ka)
                except Exception:
                    pass
                self._hb_stop.wait(interval)
        self._hb_thread = threading.Thread(target=loop, daemon=True)
        self._hb_thread.start()

    def _stop_heartbeat(self):
        if self._hb_stop:
            self._hb_stop.set()
        self._hb_thread = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *a):
        self.close()

    def _handshake(self, client_version="4.0.1"):
        # Faithfully mirror Cortex Control's connect sequence, which the QC
        # requires before it will stream state and accept grid edits.
        try:
            rcb = P.message_class("ResetCommsBuffers")()
            if "session_id" in [f.name for f in rcb.DESCRIPTOR.fields]:
                rcb.session_id = self._session_id
            if "request_id" in [f.name for f in rcb.DESCRIPTOR.fields]:
                rcb.request_id = 0
            self.send("ResetCommsBuffers", rcb)
            self._collect(0.15)

            # READ then announce our client version (compatibility check)
            self.send("Version", P.message_class("Version")(action=P.ACTION["READ"]))
            self._collect(0.15)
            vmsg = P.message_class("Version")()
            vmsg.action = P.ACTION["UPDATE"]
            vmsg.request_id = self.next_request_id()
            for fld in ("cortex_control_version", "app_version"):
                if fld in [f.name for f in vmsg.DESCRIPTOR.fields]:
                    setattr(vmsg, fld, client_version)
            self.send("Version", vmsg)
            self._collect(0.15)

            conn = P.message_class("Connection")()
            conn.connected = True
            self.send("Connection", conn)

            # Subscribe to live state (the app READs these on connect, which
            # makes the QC start streaming and marks the session active).
            for st in ("ModelRepo", "ModuleStats", "UndoRedo", "IOSettings",
                       "GeneralSettings", "Mode", "GlobalEQ", "MasterVolume",
                       "GlobalTempo", "Scene", "PresetDirty", "SetlistPosition"):
                try:
                    cls = P.message_class(st)
                    m = cls()
                    if "action" in [f.name for f in cls.DESCRIPTOR.fields]:
                        m.action = P.ACTION["READ"]
                    if "request_id" in [f.name for f in cls.DESCRIPTOR.fields]:
                        m.request_id = self.next_request_id()
                    self.send(st, m)
                except Exception:
                    pass
            time.sleep(0.3)
            self._collect(0.4)
            self._pending.clear()
        except Exception:
            pass

    def next_request_id(self):
        self._req_id += 1
        return self._req_id

    # -- io --
    def send(self, command, proto_message=None, proto_bytes=None):
        if proto_bytes is None:
            proto_bytes = proto_message.SerializeToString() if proto_message else b""
        full = P.encode_message(command, proto_bytes)
        reports = P.message_to_reports(full)
        # serialize against the heartbeat thread so frames don't interleave.
        with self._send_lock:
            for rpt in reports:
                self.io.set_report(P.REPORT_HOST_TO_QC, rpt[1:], include_id=True)

    def _collect(self, seconds):
        """Pump input, decode any complete messages into self._pending."""
        for report_id, data in self.io.read_reports(seconds):
            # the IOKit input callback delivers [reportId][chunkLen][flags][payload]
            for raw in self._rx.feed(data, has_report_id=True):
                cmd, pb, h = P.decode_message(raw)
                obj = None
                try:
                    obj = P.message_class(cmd).FromString(pb)
                except Exception:
                    obj = None
                self._pending.append((cmd, obj, raw, pb))

    def request(self, command, proto_message=None, proto_bytes=None,
                expect=None, timeout_ms=2000):
        want = command if isinstance(command, int) else P.NAME_TO_CMD[command]
        if expect is not None:
            want = expect if isinstance(expect, int) else P.NAME_TO_CMD[expect]
        self.send(command, proto_message, proto_bytes)
        deadline = time.time() + timeout_ms / 1000.0
        # prefer a response whose protobuf actually decodes (skip bare READ acks)
        best = None
        while time.time() < deadline:
            self._collect(0.1)
            keep = []
            for cmd, obj, raw, pb in self._pending:
                if cmd == want:
                    if obj is not None and len(pb) > 2:
                        return cmd, obj, raw
                    best = (cmd, obj, raw)
                else:
                    keep.append((cmd, obj, raw, pb))
            self._pending = keep
        if best:
            return best
        raise QCError(f"no response to {command} within {timeout_ms}ms")

    def add_block(self, model_hash, row=0, column=0, wait_echo_ms=800):
        """Place a device block on the grid at (row, column). Sends a Grid
        UPDATE carrying just that block; the device echoes it back on success.
        Returns the echoed block info or None."""
        g = P.message_class("Grid")()
        g.action = P.ACTION["UPDATE"]
        g.request_id = self.next_request_id()
        ch = g.preset.chains.add()
        ch.row = row
        m = ch.models.add()
        m.hash = int(model_hash)
        m.column = int(column)
        self.send("Grid", g)
        # capture echo
        import time
        deadline = time.time() + wait_echo_ms / 1000.0
        while time.time() < deadline:
            self._collect(0.1)
            for cmd, obj, raw, pb in list(self._pending):
                if cmd == P.NAME_TO_CMD["Grid"] and obj is not None \
                        and obj.HasField("preset"):
                    for c in obj.preset.chains:
                        for mm in c.models:
                            if mm.hash == int(model_hash):
                                self._pending.remove((cmd, obj, raw, pb))
                                return {"row": c.row, "column": mm.column,
                                        "hash": mm.hash}
        return None

    def get_current_preset(self, timeout_ms=6000):
        """Read the full currently-loaded preset (BinaryPreset) from the device,
        the way Cortex Control does on boot. Requires the heartbeat (started in
        open()). Returns the BinaryPreset or None."""
        cls = P.message_class("RecallPreset")
        m = cls(action=P.ACTION["READ"])
        if "request_id" in [f.name for f in cls.DESCRIPTOR.fields]:
            m.request_id = self.next_request_id()
        self.send("RecallPreset", m)
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            self._collect(0.15)
            for cmd, obj, raw, pb in list(self._pending):
                if cmd == P.NAME_TO_CMD["RecallPreset"] and obj is not None \
                        and obj.HasField("preset") and len(obj.preset.chains):
                    self._pending.remove((cmd, obj, raw, pb))
                    return obj.preset
        return None

    def clear_grid(self):
        """Delete every block in the current grid (rows = chain index, columns =
        models-array index in the full-preset read)."""
        bp = self.get_current_preset()
        if not bp:
            return 0
        n = 0
        for ridx, ch in enumerate(bp.chains):
            for cidx, m in enumerate(ch.models):
                if m.hash:
                    self.delete_block(ridx, cidx)
                    n += 1
                    time.sleep(0.05)
            self.set_split_points(ridx, -1, -1)   # collapse any parallel split
            time.sleep(0.05)
        # reset to clean single-chain routing (row0 -> main out, others idle)
        self.set_routing(0, in_portid=1, out_portid=19); time.sleep(0.05)
        for r in (1, 2, 3):
            self.set_routing(r, in_portid=0, out_portid=0); time.sleep(0.05)
        return n

    def apply_spec(self, spec, per_scene=False, pace=0.06):
        """Build a full preset onto the (cleared) current grid from a preset spec
        (see preset.describe): routing, splitters/mixers, blocks, and params."""
        for ridx, ch in enumerate(spec.get("chains", [])):
            # lane routing
            if ch.get("in_portid") or ch.get("out_portid"):
                self.set_routing(ridx, ch.get("in_portid"), ch.get("out_portid"))
                time.sleep(pace)
            # parallel split geometry (auto-creates splitter/mixer), then params
            for pair in ch.get("split_points", []):
                if tuple(pair) != (-1, -1):
                    self.set_split_points(ridx, pair[0], pair[1])
                    time.sleep(pace)
            for sp in ch.get("splitter", []):
                if sp.get("hash"):
                    self._apply_lane_params(ridx, "splitter", sp, pace)
            for mx in ch.get("mixer", []):
                if mx.get("hash"):
                    self.add_mixer(ridx, mx["hash"], mx.get("column", 0))
                    time.sleep(pace)
                    self._apply_lane_params(ridx, "mixer", mx, pace)
            # blocks + params (param index == array position)
            for cidx, m in enumerate(ch.get("models", [])):
                if not m.get("hash"):
                    continue
                self.add_block(m["hash"], row=ridx, column=cidx)
                time.sleep(pace)
                for pidx, p in enumerate(m.get("params", [])):
                    vals = [v[0] for v in p.get("values", [])]
                    if per_scene and len(vals) >= 8:
                        self.set_param_scenes(ridx, cidx, pidx, vals)
                    elif vals and vals[0]:
                        self.set_param(ridx, cidx, pidx, vals[0])
                    time.sleep(0.03)
            # input/output lane blocks (auto-created with the row) — set their params
            for field in ("input_control", "output_control"):
                for sub in ch.get(field, []):
                    if sub.get("hash"):
                        self._apply_lane_params(ridx, field, sub, pace)

    def _apply_lane_params(self, row, field, sub, pace=0.04):
        for pidx, p in enumerate(sub.get("params", [])):
            vals = [v[0] for v in p.get("values", [])]
            if vals and vals[0]:
                self.set_lane_param(row, field, pidx, vals[0], column=sub.get("column", 0))
                time.sleep(pace)

    def set_routing(self, row, in_portid=None, out_portid=None):
        """Set a lane's input/output routing (Chain.in_portid / out_portid)."""
        g = P.message_class("Grid")()
        g.action = P.ACTION["UPDATE"]
        g.request_id = self.next_request_id()
        ch = g.preset.chains.add()
        ch.row = row
        if in_portid is not None:
            ch.in_portid = in_portid
        if out_portid is not None:
            ch.out_portid = out_portid
        self.send("Grid", g)

    def set_split_points(self, row, split=-1, mix=-1):
        """Set a lane's parallel-routing geometry via split_control_points (the QC
        auto-manages the splitter/mixer blocks). split = column of the split,
        mix = column of the merge; -1 = none. (-1, -1) removes the split entirely."""
        g = P.message_class("Grid")()
        g.action = P.ACTION["UPDATE"]
        g.request_id = self.next_request_id()
        ch = g.preset.chains.add()
        ch.row = row
        scp = ch.split_control_points.add()
        scp.split = split
        scp.mix = mix
        self.send("Grid", g)

    def remove_split(self, row):
        """Collapse a lane's parallel split back to a single path."""
        self.set_split_points(row, -1, -1)

    def add_splitter(self, row, model_hash=10004, column=0, split=-1, mix=-1):
        # scp-based: split at `split` (or `column`), merge at `mix`
        self.set_split_points(row, split if split >= 0 else column, mix)

    def set_lane_param(self, row, field, param_index, value, column=0):
        """Set a parameter on a lane sub-block: field is 'splitter', 'mixer',
        'input_control', or 'output_control'."""
        g = P.message_class("Grid")()
        g.action = P.ACTION["UPDATE"]
        g.request_id = self.next_request_id()
        ch = g.preset.chains.add()
        ch.row = row
        sub = getattr(ch, field).add()
        sub.column = column
        p = sub.params.add()
        p.index = param_index
        p.param_values.add().float_value = float(value)
        self.send("Grid", g)

    def add_mixer(self, row, model_hash=11000, column=0):
        # the mixer/merge is created by the mix column in set_split_points; kept
        # for API compatibility (no-op — call set_split_points with a mix column).
        pass

    def delete_block(self, row, column, action="DELETE"):
        """Remove the block at (row, column). Tries a Grid message with the given
        action carrying just that cell."""
        g = P.message_class("Grid")()
        g.action = P.ACTION[action]
        g.request_id = self.next_request_id()
        ch = g.preset.chains.add()
        ch.row = row
        m = ch.models.add()
        m.column = column
        self.send("Grid", g)

    def set_param(self, row, column, param_index, value):
        """Set a block parameter (float) at a grid position, matching the app's
        Grid UPDATE param-edit message."""
        g = P.message_class("Grid")()
        g.action = P.ACTION["UPDATE"]
        g.request_id = self.next_request_id()
        ch = g.preset.chains.add()
        ch.row = row
        m = ch.models.add()
        m.column = column
        p = m.params.add()
        p.index = param_index
        pv = p.param_values.add()
        pv.float_value = float(value)
        self.send("Grid", g)

    def load_preset(self, binary_preset, timeout_ms=2000):
        """Apply a full BinaryPreset to the current grid via Grid UPDATE (does
        not persist). Returns the device's echoed preset if any."""
        g = P.message_class("Grid")()
        g.action = P.ACTION["UPDATE"]
        g.request_id = self.next_request_id()
        g.preset.CopyFrom(binary_preset)
        self.send("Grid", g)
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            self._collect(0.15)
            for cmd, obj, raw, pb in list(self._pending):
                if cmd in (P.NAME_TO_CMD["Grid"], P.NAME_TO_CMD["RecallPreset"]) \
                        and obj is not None and obj.HasField("preset"):
                    self._pending.remove((cmd, obj, raw, pb))
                    return obj.preset
        return None

    def save_preset(self, binary_preset=None, reason="SAVE"):
        """Persist the preset. If binary_preset is None, saves the current grid.
        Uses RecallPreset UPDATE with reason=SAVE."""
        m = P.message_class("RecallPreset")()
        m.action = P.ACTION["UPDATE"]
        m.request_id = self.next_request_id()
        if binary_preset is not None:
            m.preset.CopyFrom(binary_preset)
        m.reason = P.pool().FindEnumTypeByName(
            "cortex_protobuf_v2.RecallPresetReason.Enum").values_by_name[reason].number
        self.send("RecallPreset", m)

    def write_preset_file(self, binary_preset, folder_key, name):
        """Create a preset file in a folder via FileMessage CREATE preset_payload."""
        f = P.message_class("File")()
        f.action = P.ACTION["CREATE"]
        f.request_id = self.next_request_id()
        f.preset_payload.CopyFrom(binary_preset)
        binary_preset.name = name
        f.folder.key = folder_key
        self.send("File", f)

    def read_message(self, command, timeout_ms=4000):
        """Send a READ and return the decoded reply message (or None)."""
        cls = P.message_class(command)
        m = cls()
        if "action" in [f.name for f in cls.DESCRIPTOR.fields]:
            m.action = P.ACTION["READ"]
        if "request_id" in [f.name for f in cls.DESCRIPTOR.fields]:
            m.request_id = self.next_request_id()
        self.send(command, m)
        want = P.NAME_TO_CMD[command]
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            self._collect(0.15)
            for cmd, obj, raw, pb in list(self._pending):
                if cmd == want and obj is not None and len(pb) > 2:
                    self._pending.remove((cmd, obj, raw, pb))
                    return obj
        return None

    def set_scene(self, scene):
        m = P.message_class("Scene")(action=P.ACTION["UPDATE"],
                                     request_id=self.next_request_id(),
                                     selected_scene=int(scene))
        self.send("Scene", m)

    def set_mode(self, mode):
        m = P.message_class("Mode")(action=P.ACTION["UPDATE"],
                                    request_id=self.next_request_id(), mode=int(mode))
        self.send("Mode", m)

    def set_param_scenes(self, row, column, param_index, values):
        """Set all per-scene values of a parameter at once (values = up to 8
        floats, one per scene A-H)."""
        g = P.message_class("Grid")()
        g.action = P.ACTION["UPDATE"]
        g.request_id = self.next_request_id()
        ch = g.preset.chains.add()
        ch.row = row
        m = ch.models.add()
        m.column = column
        p = m.params.add()
        p.index = param_index
        p.scene_mode = True
        for v in list(values)[:8]:
            p.param_values.add().float_value = float(v)
        self.send("Grid", g)

    def recall(self, folder_key, position, is_factory=False):
        m = P.message_class("SetlistPosition")()
        m.action = P.ACTION["UPDATE"]
        m.request_id = self.next_request_id()
        m.folder_key = folder_key
        m.position = position
        m.is_factory = is_factory
        self.send("SetlistPosition", m)

    def read_state(self, command, timeout_ms=2500):
        cls = P.message_class(command)
        msg = cls()
        if "action" in [f.name for f in cls.DESCRIPTOR.fields]:
            msg.action = P.ACTION["READ"]
        if "request_id" in [f.name for f in cls.DESCRIPTOR.fields]:
            msg.request_id = self.next_request_id()
        _c, obj, _r = self.request(command, msg, timeout_ms=timeout_ms)
        return obj
