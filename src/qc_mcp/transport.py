"""High-level Quad Cortex transport over the platform HID backend (see
`backend.open_hid`: IOKit on macOS, hid.dll on Windows).

Requires exclusive HID access (opens with seize), so Cortex Control must be
quit while connected. Note: IOHIDDeviceSetReport returns a benign 0xe0005000 on
this device (the official app ignores it too) — it is NOT an error; the Windows
backend returns 0 there. Nothing upstream inspects the value either way.
"""
from __future__ import annotations
import functools
import threading
import time

from . import protocol as P
from .backend import open_bridge, open_hid


class QCError(Exception):
    pass


def _serialized(method):
    """Hold the device for one whole exchange (send, then collect the reply).

    A read is send-then-consume-until-matching, and `_pending` is shared, so two
    interleaved exchanges eat each other's replies and both come back empty —
    the same failure as running two readers on the bridge FIFO. Harmless under
    mcp 1.x, which runs sync tool handlers one at a time on the event loop, but
    mcp 2.x dispatches them to worker threads and really does run them at once.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._io_lock:
            return method(self, *args, **kwargs)
    return wrapper


class QuadCortex:
    def __init__(self, session_id="claudemcp0000000000000000000000", bridge=False,
                 share=False):
        # Three ways in, all behind the same backend API:
        #   bridge=True  share Cortex Control's session through the interposer
        #                (macOS: the app seizes the device, so this is the only way)
        #   share=True   our own NON-exclusive handle next to the running app
        #                (Windows: the HID stack copies every input report to every
        #                open handle, so no interposer is needed - see docs/WINDOWS.md)
        #   neither      seize the device for ourselves
        self.bridge = bridge
        self.shared = share
        if bridge:
            self.io = open_bridge()
        else:
            self.io = open_hid(seize=not share)
        self._rx = P.Reassembler()
        # High, distinctive base so our request_ids never collide with Cortex
        # Control's (small, incrementing) ids when sharing its session in bridge mode.
        # The device echoes request_id in solicited replies, so we match on it to
        # reject stale/buffered responses and the app's own traffic.
        self._req_id = 0x51C_00000    # "QC" — arbitrary high base
        self._session_id = session_id
        self._pending = []  # decoded (cmd, obj, raw) not yet consumed
        self._send_lock = threading.Lock()
        # Re-entrant: the exchange methods below call _collect() while holding it.
        self._io_lock = threading.RLock()
        self._hb_stop = None
        self._hb_thread = None
        # Filled in by detect_version() once the device answers a Version READ.
        self.firmware = None          # CorOS version, e.g. "4.1.0"
        self.device_type = None       # "QC" | "ATMA" (Quad Cortex mini)
        self.custom_name = ""         # user label, for multi-device setups (4.1+)
        self.protocol_version = P.LATEST_VERSION
        self.protocol_version_verified = False
        # Non-benign set_report codes seen this session. Reads that time out with
        # a non-zero count here mean our frames are not reaching the device.
        self.write_errors = 0
        self.last_write_error = None

    # -- lifecycle --
    def open(self, handshake=True):
        self.io.open()
        # In bridge mode Cortex Control already owns the handshake + heartbeat.
        # A shared handle does NOT - it is our own session alongside the app's.
        if handshake and not self.bridge:
            self._start_heartbeat()   # keep session "online" (required for reads)
            self._handshake()
        # Bridge mode skips the handshake (the app owns it) but still needs the
        # firmware to pick a schema, so ask here either way.
        self.detect_version()
        return self

    def detect_version(self):
        """Read the device's firmware and select the matching wire schema.

        CorOS releases change the protobuf schema (4.1 reuses GlobalEQ field 5
        for something else, and adds ModelPreset/RemoteControl), so every
        connection re-negotiates rather than assuming the newest generation.
        """
        v = None
        for attempt in range(3):
            try:
                v = self.read_state("Version", timeout_ms=3000)
                break
            except Exception:
                time.sleep(0.5)
        if v is None:
            # Unknown firmware: keep the newest schema (the common case) but say
            # so — on a 4.0 device it would mis-encode GlobalEQ field 5, and the
            # 4.1-only gates would all pass.
            self.firmware = None
            self.protocol_version_verified = False
            # Report whatever is actually encoding messages: a previous
            # connection may have left the process-global gate elsewhere, and
            # claiming this connection's default would misreport the schema.
            self.protocol_version = P.active_version()
            return self.protocol_version
        # Confusingly, the human CorOS version ("4.1.0") lives in zenos_git_hash;
        # app_fw_version holds an actual build hash ("d14e"). Prefer the former,
        # and only accept something that parses as a version number.
        for field in ("zenos_git_hash", "app_fw_version"):
            value = getattr(v, field, "") or ""
            if P.parse_version(value):
                self.firmware = value
                break
        else:
            self.firmware = None
        self.custom_name = getattr(v, "custom_name", "") or ""
        try:
            dt = v.DESCRIPTOR.fields_by_name["device_type"].enum_type
            self.device_type = dt.values_by_number[v.device_type].name
        except Exception:
            self.device_type = None
        self.protocol_version = P.set_version(self.firmware)
        self.protocol_version_verified = self.firmware is not None
        return self.protocol_version

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

    def _reported_firmware(self):
        """CorOS version from any Version reply already in the pending queue.

        Same field order as detect_version: zenos_git_hash carries the human
        version, app_fw_version a build hash — announcing the latter back as our
        `cortex_control_version` would fail the device's compatibility check.
        """
        for cmd, obj, _raw, _pb in self._pending:
            if cmd == P.NAME_TO_CMD["Version"] and obj is not None:
                for field in ("zenos_git_hash", "app_fw_version"):
                    value = getattr(obj, field, "") or ""
                    if P.parse_version(value):
                        return value
        return None

    def _handshake(self, client_version=None):
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

            # READ then announce our client version (compatibility check).
            # Mirror back whatever firmware the device reports, so we always
            # claim a matching Cortex Control rather than a hardcoded release.
            self.send("Version", P.message_class("Version")(action=P.ACTION["READ"]))
            self._collect(0.15)
            if client_version is None:
                client_version = self._reported_firmware() or f"{P.LATEST_VERSION}.0"
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
        benign = getattr(self.io, "BENIGN_WRITE_CODES", frozenset({0}))
        with self._send_lock:
            for rpt in reports:
                rc = self.io.set_report(P.REPORT_HOST_TO_QC, rpt[1:], include_id=True)
                if rc is not None and rc not in benign:
                    # NOT fatal: on Windows ERROR_GEN_FAILURE(31) comes back even
                    # on an exclusive handle whose writes plainly do land (the
                    # full preset build/save e2e runs through it), so raising here
                    # breaks a working path. But it is also exactly what a write
                    # that never reaches the device returns, which is how a shared
                    # handle can look like it is controlling the QC while every
                    # write is dropped. So: count it, expose it, don't guess.
                    self.write_errors += 1
                    self.last_write_error = rc

    @_serialized
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

    @_serialized
    def request(self, command, proto_message=None, proto_bytes=None,
                expect=None, timeout_ms=2000):
        want = command if isinstance(command, int) else P.NAME_TO_CMD[command]
        if expect is not None:
            want = expect if isinstance(expect, int) else P.NAME_TO_CMD[expect]
        # The device echoes request_id; match it to reject stale/buffered replies and
        # (in bridge mode) Cortex Control's own responses. Fall back to a command match
        # only if nothing with our id arrives (some message types may not echo it).
        exp_rid = int(getattr(proto_message, "request_id", 0) or 0) if proto_message is not None else 0
        self.send(command, proto_message, proto_bytes)
        deadline = time.time() + timeout_ms / 1000.0
        # prefer a response whose protobuf actually decodes (skip bare READ acks)
        best = None
        while time.time() < deadline:
            self._collect(0.1)
            keep = []
            for cmd, obj, raw, pb in self._pending:
                if cmd == want:
                    rid = int(getattr(obj, "request_id", 0) or 0) if obj is not None else 0
                    if exp_rid and rid == exp_rid and obj is not None and len(pb) > 2:
                        self._pending = keep + [t for t in self._pending
                                                if t is not (cmd, obj, raw, pb)]
                        return cmd, obj, raw
                    if not exp_rid and obj is not None and len(pb) > 2:
                        return cmd, obj, raw
                    best = best or (cmd, obj, raw)   # fallback: right command, id absent/mismatched
                else:
                    keep.append((cmd, obj, raw, pb))
            self._pending = keep
        if best:
            return best
        raise QCError(f"no response to {command} within {timeout_ms}ms")

    @_serialized
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
        open()). Returns the BinaryPreset or None. In bridge mode, an empty result
        can mean a stale reader connection, so we reopen the bridge and retry once."""
        bp = self._read_preset_once(timeout_ms)
        if bp is None and self.bridge:
            self.reconnect()
            bp = self._read_preset_once(timeout_ms)
        return bp

    @_serialized
    def _read_preset_once(self, timeout_ms):
        cls = P.message_class("RecallPreset")
        m = cls(action=P.ACTION["READ"])
        rid = None
        if "request_id" in [f.name for f in cls.DESCRIPTOR.fields]:
            rid = self.next_request_id()
            m.request_id = rid
        want = P.NAME_TO_CMD["RecallPreset"]
        self.send("RecallPreset", m)
        deadline = time.time() + timeout_ms / 1000.0
        while time.time() < deadline:
            self._collect(0.15)
            for cmd, obj, raw, pb in list(self._pending):
                # match our request_id so a buffered/older push or the app's own
                # recall echo can't be mistaken for the response to THIS read.
                if cmd == want and obj is not None and obj.HasField("preset") \
                        and len(obj.preset.chains) \
                        and (rid is None or int(getattr(obj, "request_id", 0) or 0) == rid):
                    self._pending.remove((cmd, obj, raw, pb))
                    return obj.preset
        return None

    def reconnect(self):
        """Recover a stale connection in place. In bridge mode this reopens the
        FIFO reader/writer (fixing a dead reader thread after the app restarted or
        the connection aged out) without tearing down the session."""
        reopen = getattr(self.io, "reopen", None)
        if callable(reopen):
            reopen()
            self._rx = P.Reassembler()   # drop any half-decoded frame from before
            self._pending = []

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
                    valspecs = p.get("values", [])
                    if not any(any(x is not None for x in v) for v in valspecs):
                        continue
                    # scene-varying float param with per-scene values -> assign + write
                    if per_scene and p.get("scene_mode") and len(valspecs) >= 8 \
                            and all(v[0] is not None for v in valspecs):
                        self.set_param_scenes(ridx, cidx, pidx, [v[0] for v in valspecs])
                    else:
                        self.set_param_typed(ridx, cidx, pidx, valspecs[:1])
                    time.sleep(0.03)
            # input/output lane blocks (auto-created with the row) — set their params
            for field in ("input_control", "output_control"):
                for sub in ch.get(field, []):
                    if sub.get("hash"):
                        self._apply_lane_params(ridx, field, sub, pace)

    def _apply_lane_params(self, row, field, sub, pace=0.04):
        for pidx, p in enumerate(sub.get("params", [])):
            vals = p.get("values", [])
            f = vals[0][0] if vals else None
            if f is not None:
                self.set_lane_param(row, field, pidx, f, column=sub.get("column", 0))
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

    def write_preset_file(self, folder_key, position, name, binary_preset=None):
        """Save the current WORKING preset to a setlist slot via File CREATE — exactly
        how Cortex Control's Save works (decoded from the wire): File{action=CREATE,
        folder{key, files{index=<position>, name}}} and **no preset_payload**. The
        device commits its live working grid to <folder>/<name>.pb at <position>. Do NOT
        use RecallPreset SAVE for this (it hangs on an empty/Unsaved slot). binary_preset
        is accepted for back-compat but intentionally NOT sent (the app sends none)."""
        f = P.message_class("File")()
        f.action = P.ACTION["CREATE"]
        f.request_id = self.next_request_id()
        f.folder.key = folder_key
        fi = f.folder.files.add()
        fi.index = int(position)
        fi.name = name
        self.send("File", f)

    @_serialized
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

    def set_param_typed(self, row, column, param_index, valspecs, scene_mode=False):
        """Set a param preserving value TYPE (float/int/string). valspecs = list of
        [f|None, i|None, s|None] per scene, as produced by preset.describe — so cab
        mic/IR-name strings, capture file_name, and IR paths survive a rebuild, not
        just floats. scene_mode writes all 8 per-scene values."""
        g = P.message_class("Grid")()
        g.action = P.ACTION["UPDATE"]
        g.request_id = self.next_request_id()
        ch = g.preset.chains.add()
        ch.row = row
        m = ch.models.add()
        m.column = column
        p = m.params.add()
        p.index = param_index
        if scene_mode:
            p.scene_mode = True
        for f, i, s in valspecs:
            pv = p.param_values.add()
            if f is not None:
                pv.float_value = f
            elif i is not None:
                pv.int_value = i
            elif s is not None:
                pv.string_value = s
        self.send("Grid", g)

    def set_scene(self, scene):
        m = P.message_class("Scene")(action=P.ACTION["UPDATE"],
                                     request_id=self.next_request_id(),
                                     selected_scene=int(scene))
        self.send("Scene", m)

    def set_mode(self, mode):
        """Set the active performance mode: Mode(14) UPDATE {mode} (device echoes)."""
        m = P.message_class("Mode")(action=P.ACTION["UPDATE"],
                                    request_id=self.next_request_id(), mode=int(mode))
        self.send("Mode", m)

    def set_mode_cycle(self, mode_ids):
        """Set the footswitch mode cycle: Mode(14) UPDATE {available_modes{modes[]}}
        (captured from Cortex Control's Modes Configuration). Does not change the
        active mode."""
        m = P.message_class("Mode")(action=P.ACTION["UPDATE"],
                                    request_id=self.next_request_id())
        for i in mode_ids:
            m.available_modes.modes.append(int(i))
        self.send("Mode", m)

    def set_scene_label(self, index, label):
        """Label scene index (0-7 = A-H) on the LIVE preset. Verified wire op (captured
        from Cortex Control's scene rename): SceneLabel(23) UPDATE {index, label}. A
        Grid UPDATE carrying preset-level scene_labels is silently IGNORED — don't."""
        m = P.message_class("SceneLabel")(action=P.ACTION["UPDATE"],
                                          request_id=self.next_request_id(),
                                          index=int(index), label=str(label))
        self.send("SceneLabel", m)

    def set_scene_color(self, index, color):
        """Color scene index (0-7). Verified: SceneColor(48) UPDATE {index, color}
        (color = ARGB int; the app auto-sends one alongside each label)."""
        m = P.message_class("SceneColor")(action=P.ACTION["UPDATE"],
                                          request_id=self.next_request_id(),
                                          index=int(index), color=int(color))
        self.send("SceneColor", m)

    def set_preset_meta(self, name=None, tempo=None, default_scene=None,
                        scene_labels=None, scene_colors=None):
        """Set preset-level metadata on the live preset.
        VERIFIED paths: scene_labels/scene_colors -> dedicated SceneLabel(23)/
        SceneColor(48) UPDATEs (a Grid UPDATE with these preset fields is a silent
        no-op — confirmed live). `name` CANNOT be set on the live grid at all: preset
        names live on the preset FILE and are set by the save op (write_preset_file) —
        it is ignored here. tempo/default_scene still go via Grid UPDATE (unverified —
        never observed working; treat as best-effort)."""
        if scene_labels is not None:
            for i, lbl in enumerate(list(scene_labels)[:8]):
                if lbl is not None:
                    self.set_scene_label(i, lbl)
                    time.sleep(0.03)
        if scene_colors is not None:
            for i, c in enumerate(list(scene_colors)[:8]):
                if c is not None:
                    self.set_scene_color(i, c)
                    time.sleep(0.03)
        if tempo is not None or default_scene is not None:
            g = P.message_class("Grid")()
            g.action = P.ACTION["UPDATE"]
            g.request_id = self.next_request_id()
            if tempo is not None:
                g.preset.tempo = int(tempo)
            if default_scene is not None:
                g.preset.default_scene = int(default_scene)
            self.send("Grid", g)

    def assign_param_to_scenes(self, row, column, param_index):
        """Make a param scene-varying — the "Assign to Scenes" right-click action in
        Cortex Control. Verified message: Grid UPDATE with the param carrying only
        scene_mode:true (no param_values)."""
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
        self.send("Grid", g)

    def _write_active_scene_param(self, row, column, param_index, value):
        """Plain value write. Once a param is scene-assigned, this lands on the ACTIVE
        scene; otherwise it's global. No scene_mode flag (matches the app)."""
        g = P.message_class("Grid")()
        g.action = P.ACTION["UPDATE"]
        g.request_id = self.next_request_id()
        ch = g.preset.chains.add()
        ch.row = row
        m = ch.models.add()
        m.column = column
        p = m.params.add()
        p.index = param_index
        p.param_values.add().float_value = float(value)
        self.send("Grid", g)

    def _await_scene(self, scene, timeout_s=2.0):
        """Block until the device reports `scene` active (Scene READ). Racing a plain
        param write against an un-settled scene switch lands the value on the WRONG
        scene — found live when a batch scene write dropped a value."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            st = self.read_state("Scene", timeout_ms=600)
            if st is not None and int(getattr(st, "selected_scene", -1)) == int(scene):
                return True
            time.sleep(0.05)
        return False

    def set_param_scenes(self, row, column, param_index, values):
        """Set a param's per-scene values (up to 8, scenes A-H). Verified sequence
        (reversed from Cortex Control): assign the param to scenes, then for each scene
        make it active and write its value (the device routes a plain write to the
        active scene). Each switch is CONFIRMED via Scene READ before writing — a fixed
        sleep raced the device and dropped values. Restores scene A afterward."""
        vals = list(values)[:8]
        self.assign_param_to_scenes(row, column, param_index)
        time.sleep(0.05)
        for scene, v in enumerate(vals):
            self.set_scene(scene)
            self._await_scene(scene)
            self._write_active_scene_param(row, column, param_index, v)
            time.sleep(0.04)
        self.set_scene(0)
        self._await_scene(0)

    # Neural Capture block model ids (Neural Capture catalog category).
    CAPTURE_V1 = 14000
    CAPTURE_V2 = 14001
    CAPTURE_FILE_NAME_PARAM = 5

    def set_capture(self, row, column, capture_key, capture_name="", version="v1"):
        """Place a Neural Capture block at (row, column) and load a specific capture
        into it. The capture is selected by param[5] `file_name` = the capture's 64-hex
        key concatenated with its display name (how the device binds a capture to a
        block; see docs/DIRECTORY.md). version 'v1'->block 14000, 'v2'->14001. The
        capture must already be on the device (factory, or downloaded from Cortex
        Cloud). Returns the echoed block or None."""
        hash_ = self.CAPTURE_V2 if str(version).lower() in ("v2", "2") else self.CAPTURE_V1
        echo = self.add_block(hash_, row=row, column=column)
        time.sleep(0.08)
        g = P.message_class("Grid")()
        g.action = P.ACTION["UPDATE"]
        g.request_id = self.next_request_id()
        ch = g.preset.chains.add()
        ch.row = row
        m = ch.models.add()
        m.column = column
        p = m.params.add()
        p.index = self.CAPTURE_FILE_NAME_PARAM
        p.param_values.add().string_value = f"{capture_key}{capture_name}"
        self.send("Grid", g)
        return echo

    # IR-loader block ids + param indices (see docs/DIRECTORY.md).
    IR_SINGLE_M = 29001
    IR_SINGLE_ST = 29002
    IR_PATH_PARAM = 2
    IR_NAME_PARAM = 22

    def set_ir(self, row, column, ir_key, ir_name="", stereo=False):
        """Place an IR-loader block at (row, column) and load an impulse response into
        it. The IR is bound by param[2] IR PATH = the IR's `CIR_…` key, and param[22]
        IR NAME = display name (two separate string params). The IR must be on the
        device. Returns the echoed block or None."""
        hash_ = self.IR_SINGLE_ST if stereo else self.IR_SINGLE_M
        echo = self.add_block(hash_, row=row, column=column)
        time.sleep(0.08)
        g = P.message_class("Grid")()
        g.action = P.ACTION["UPDATE"]
        g.request_id = self.next_request_id()
        ch = g.preset.chains.add(); ch.row = row
        m = ch.models.add(); m.column = column
        p1 = m.params.add(); p1.index = self.IR_PATH_PARAM
        p1.param_values.add().string_value = str(ir_key)
        p2 = m.params.add(); p2.index = self.IR_NAME_PARAM
        p2.param_values.add().string_value = str(ir_name)
        self.send("Grid", g)
        return echo

    BYPASS_PARAM = 4   # a block's bypass control is param index 4 (verified on OD/amp)

    def set_block_bypass(self, row, column, bypassed=True, scenes=None, bypass_param=BYPASS_PARAM):
        """Bypass or re-enable the block at (row, column). Verified message shape:
        Grid UPDATE preset.bypass[{ row, colBypass{ column, sceneBypass{bypass} } }].
        scenes=None -> single sceneBypass for the current scene (what the app sends);
        pass a list of up to 8 bools for explicit per-scene (A-H) bypass."""
        if scenes is None:
            self._write_bypass(row, column, bool(bypassed))
            return
        # per-scene bypass is just the block's BYPASS PARAM (index `bypass_param`, =4 for
        # most single blocks) assigned to scenes — reversed from the right-click "Assign
        # to Scenes" on the bypass button, which emits params{index:4, scene_mode:true}.
        # 1.0 = bypassed, 0.0 = active.
        self.set_param_scenes(row, column, bypass_param,
                              [1.0 if s else 0.0 for s in list(scenes)[:8]])

    def _write_bypass(self, row, column, bypassed):
        g = P.message_class("Grid")()
        g.action = P.ACTION["UPDATE"]
        g.request_id = self.next_request_id()
        b = g.preset.bypass.add()
        b.row = row
        cb = b.colBypass.add()
        cb.column = column
        cb.sceneBypass.add().bypass = bool(bypassed)
        self.send("Grid", g)

    def _assign_bypass_to_scenes(self, row, column):
        g = P.message_class("Grid")()
        g.action = P.ACTION["UPDATE"]
        g.request_id = self.next_request_id()
        b = g.preset.bypass.add()
        b.row = row
        cb = b.colBypass.add()
        cb.column = column
        cb.sceneMode = True
        self.send("Grid", g)

    def recall(self, folder_key="", position=0, is_factory=False,
               downloads_key="", plugin_key=""):
        """Load a preset via SetlistPosition UPDATE. The addressing differs by source:
          * My Presets / Factory: folder_key + position (+ is_factory).
          * Downloads (cloud):     downloads_key = the preset's cloud_id UUID
                                   (sets is_downloads + key_in_downloads).
          * Plugin banks:          plugin_key (sets is_plugin + key_in_plugin_folder).
        """
        m = P.message_class("SetlistPosition")()
        m.action = P.ACTION["UPDATE"]
        m.request_id = self.next_request_id()
        if downloads_key:
            m.is_downloads = True
            m.key_in_downloads = downloads_key
        elif plugin_key:
            m.is_plugin = True
            m.key_in_plugin_folder = plugin_key
        else:
            m.folder_key = folder_key
            m.position = position
            m.is_factory = is_factory
        self.send("SetlistPosition", m)

    @_serialized
    def list_directory(self, timeout_ms=30000, quiet_ms=1500):
        """Send a File READ and collect the full stream of File UPDATE folder
        messages the device emits (presets, IRs, captures). Returns the structured
        catalog from directory.structure_directory(). Read-only. Works in BRIDGE
        mode too (verified: the full ~400-message stream arrives in ~12s — hence
        the generous default window; the app's own boot listing is the same READ)."""
        from . import directory as _dir
        cls = P.message_class("File")
        m = cls(action=P.ACTION["READ"])
        if "request_id" in [f.name for f in cls.DESCRIPTOR.fields]:
            m.request_id = self.next_request_id()
        self.send("File", m)
        want = P.NAME_TO_CMD["File"]
        deadline = time.time() + timeout_ms / 1000.0
        last = time.time()
        folders = []
        while time.time() < deadline:
            self._collect(0.15)
            got = False
            for cmd, obj, raw, pb in list(self._pending):
                if cmd == want and obj is not None and obj.HasField("folder"):
                    folders.append(obj)
                    self._pending.remove((cmd, obj, raw, pb))
                    got = True
            if got:
                last = time.time()
            elif folders and (time.time() - last) * 1000 > quiet_ms:
                break
        return _dir.structure_directory(folders)

    def get_setlist_position(self):
        """Current loaded-preset pointer: {folder_key, position, is_factory}."""
        obj = self.read_state("SetlistPosition")
        if obj is None:
            return None
        return {"folder_key": obj.folder_key, "position": obj.position,
                "is_factory": obj.is_factory}

    FAVORITE_KINDS = {"preset": 0, "ir": 1, "capture": 2}

    def list_recents_favorites(self, favorites=False, kind="preset", timeout_ms=3000):
        """RecentsFavorites READ. favorites=True -> favorites list, else recents.

        CorOS 4.1 split the list into three: presets, impulse responses and
        neural captures (64 entries each). `kind` picks one; on 4.0 the field
        does not exist and the device answers with the single combined list.
        Returns [{name, folder_key, folder_name, is_factory, is_plugin,
        product_key}]."""
        cls = P.message_class("RecentsFavorites")
        fields = [f.name for f in cls.DESCRIPTOR.fields]
        m = cls(action=P.ACTION["READ"])
        if "is_favorites" in fields:
            m.is_favorites = bool(favorites)
        if "type" in fields:
            m.type = self.FAVORITE_KINDS.get(str(kind).lower(), 0)
        if "request_id" in fields:
            m.request_id = self.next_request_id()
        _c, obj, _r = self.request("RecentsFavorites", m, timeout_ms=timeout_ms)
        if obj is None:
            return []
        return [{"name": it.name, "folder_key": it.folder_key,
                 "folder_name": it.folder_name,
                 "is_factory": getattr(it, "is_factory", False),
                 "is_plugin": getattr(it, "is_plugin", False),
                 "product_key": getattr(it, "product_key", "")} for it in obj.items]

    # -- device presets (CorOS 4.1): per-model saved settings --
    def list_model_presets(self, model_hash=None, timeout_ms=25000):
        """ModelPreset(71) READ -> every device preset the QC holds.

        One READ returns the whole index (~2750 factory entries in 4.1.0) as
        {id{value, is_factory, hash}, name, is_default}; `hash` is the model the
        preset belongs to, so filter by it for one block's presets.
        """
        msg = self.read_state("ModelPreset", timeout_ms=timeout_ms)
        out = []
        for pr in getattr(msg, "presets", []):
            if model_hash is not None and pr.id.hash != int(model_hash):
                continue
            out.append({"id": pr.id.value, "name": pr.name,
                        "model_hash": pr.id.hash,
                        "is_factory": pr.id.is_factory,
                        "is_default": pr.is_default})
        return out

    STOMP_TYPES = {"primary": 0, "secondary": 1}

    def assign_stomp(self, row, column, stomp_index, kind="primary",
                     momentary=None, settle_s=1.0):
        """Bind a footswitch (0-7 = A-H) to the block at (row, column).

        Decoded from Cortex Control's "Assign footswitch" menu: a **Grid**(1)
        UPDATE whose preset carries only `stomp_mode_assignments[]{row, column,
        stomp_index, type}` — no other grid state. `kind` is 'primary' (bypass,
        the default) or 'secondary'; SECONDARY is the CorOS 4.1 Dual Footswitch
        feature and only means something on devices that have a second function
        (Vintage Digital, Aeons Reverb).

        `momentary=True` makes the switch momentary rather than latching, via
        the preset's `stomp_is_momentary` map — the device reports that back as
        a separate echo.
        """
        if kind not in self.STOMP_TYPES:
            raise QCError(f"kind must be 'primary' or 'secondary', not {kind!r}")
        g = P.message_class("Grid")()
        g.action = P.ACTION["UPDATE"]
        g.request_id = self.next_request_id()
        a = g.preset.stomp_mode_assignments.add()
        a.row = int(row)
        a.column = int(column)
        a.stomp_index = int(stomp_index)
        if "type" in [f.name for f in a.DESCRIPTOR.fields]:
            a.type = self.STOMP_TYPES[kind]
        self.send("Grid", g)
        time.sleep(settle_s)
        if momentary is not None:
            # Must be its own message: the device answers an assignment with its
            # own stomp_is_momentary echo, which overwrites a flag sent alongside.
            m = P.message_class("Grid")()
            m.action = P.ACTION["UPDATE"]
            m.request_id = self.next_request_id()
            m.preset.stomp_is_momentary[int(stomp_index)] = bool(momentary)
            self.send("Grid", m)
            time.sleep(settle_s)

    def unassign_stomp(self, row, column, stomp_index, settle_s=1.0):
        """Unbind a footswitch from a block — a Grid **DELETE** naming the same
        assignment. The device also clears that switch's momentary flag."""
        g = P.message_class("Grid")()
        g.action = P.ACTION["DELETE"]
        g.request_id = self.next_request_id()
        a = g.preset.stomp_mode_assignments.add()
        a.row = int(row)
        a.column = int(column)
        a.stomp_index = int(stomp_index)
        self.send("Grid", g)
        time.sleep(settle_s)

    # -- settings presets: Global EQ and I/O Settings ride pseudo-models --
    SETTINGS_MODELS = {"global_eq": 4004, "io_settings": 31000}

    def load_settings_preset(self, target, preset_id, is_factory=True, settle_s=2.0):
        """Apply a Global EQ or I/O Settings preset (CorOS 4.1).

        These are device presets on pseudo-models — Global EQ is catalog hash
        4004 "Output Equalizer" (its 28 params line up 1:1 with GlobalEQ's 28
        parameter indices), I/O Settings is 31000 — but each is applied through
        its OWN message rather than the Grid: `GlobalEQ.model_preset_to_load` /
        `IOSettings.preset_to_load`.
        """
        if target not in self.SETTINGS_MODELS:
            raise QCError(f"target must be one of {sorted(self.SETTINGS_MODELS)}")
        command = "GlobalEQ" if target == "global_eq" else "IOSettings"
        field = "model_preset_to_load" if target == "global_eq" else "preset_to_load"
        m = P.message_class(command)()
        m.action = P.ACTION["UPDATE"]
        m.request_id = self.next_request_id()
        pid = getattr(m, field)
        pid.value = str(preset_id)
        pid.is_factory = bool(is_factory)
        pid.hash = self.SETTINGS_MODELS[target]
        self.send(command, m)
        time.sleep(settle_s)

    def read_global_eq(self):
        """[(parameter_index, value)] plus the bypass flag."""
        g = self.read_state("GlobalEQ")
        return ([(p.parameter_index, p.value) for p in g.parameters],
                bool(g.bypassed))

    def write_global_eq(self, params, bypassed=None, settle_s=1.5):
        """Write Global EQ parameters back — the restore path for a preset load."""
        m = P.message_class("GlobalEQ")()
        m.action = P.ACTION["UPDATE"]
        m.request_id = self.next_request_id()
        for index, value in params:
            pp = m.parameters.add()
            pp.parameter_index = int(index)
            pp.value = float(value)
        if bypassed is not None:
            m.bypassed = bool(bypassed)
        self.send("GlobalEQ", m)
        time.sleep(settle_s)

    def set_io_port(self, kind, port_id, settle_s=1.5, **fields):
        """Set individual hardware I/O port fields (`kind` 'in' or 'out').

        **Send only the fields you are changing.** A full port record — every
        field at once, e.g. a protobuf CopyFrom of one the device sent — is
        silently rejected, which is what makes I/O writes look impossible. One
        field per call is always safe.
        """
        if kind not in ("in", "out"):
            raise QCError("kind must be 'in' or 'out'")
        m = P.message_class("IOSettings")()
        m.action = P.ACTION["UPDATE"]
        m.request_id = self.next_request_id()
        port = (m.settings.in_port if kind == "in" else m.settings.out_port).add()
        setattr(port, f"{'input' if kind == 'in' else 'output'}_port_id", int(port_id))
        valid = {f.name for f in port.DESCRIPTOR.fields}
        unknown = [n for n in fields if n not in valid]
        if unknown:
            raise QCError(f"{sorted(unknown)} not settable on an {kind} port; "
                          f"it has {sorted(valid)}")
        for name, value in fields.items():
            setattr(port, name, value)
        self.send("IOSettings", m)
        time.sleep(settle_s)

    def select_model_slot(self, row, column, timeout_ms=3000):
        """Tell the device which grid slot's editor is open, and read back which
        device preset that block currently holds.

        The device tracks exactly **one** active slot for device-preset writes —
        Cortex Control sends this the moment a block editor opens, and the
        firmware's own comment says the loaded-preset field "is correct only for
        the open panel / selected grid slot". Saving without it is silently
        ignored, because the device has no idea which block you mean.

        `ModelPreset`(71) with **no action field** (CREATE=0) plus loaded_row /
        loaded_column. Returns {"value", "is_factory", "hash"} — a value of
        "SpecialFactoryModelPresetID" means the block's settings don't match any
        saved preset.
        """
        cls = P.message_class("ModelPreset")
        m = cls()
        m.request_id = self.next_request_id()
        m.loaded_row = int(row)
        m.loaded_column = int(column)
        _c, obj, _r = self.request("ModelPreset", m, timeout_ms=timeout_ms)
        pid = getattr(obj, "loaded_preset_id", None)
        if pid is None:
            return None
        return {"value": pid.value, "is_factory": pid.is_factory, "hash": pid.hash}

    def save_model_preset(self, row, column, name, model_hash,
                          is_default=False, timeout_ms=5000):
        """Save the block at (row, column)'s current settings as a **user device
        preset** (max 32 per device). Decoded from Cortex Control's own
        "Save Current Parameters as…".

        Two steps: select the slot (above), then `ModelPreset`(71) — again with
        no action field — carrying `presets[0]{id{is_factory:false, hash}, name,
        is_default}`. Note there is no payload of parameter values and no
        `create_from_*`: the device snapshots the selected block itself. Returns
        the created preset's id.
        """
        self.select_model_slot(row, column)
        cls = P.message_class("ModelPreset")
        m = cls()
        m.request_id = self.next_request_id()
        pr = m.presets.add()
        pr.id.is_factory = False
        pr.id.hash = int(model_hash)
        pr.name = name
        pr.is_default = bool(is_default)
        rid = m.request_id
        _c, obj, _r = self.request("ModelPreset", m, timeout_ms=timeout_ms)
        # request() falls back to a command-only match, and the device broadcasts
        # its whole 2700-entry preset index unprompted — without the id check a
        # REFUSED save would report some factory preset as the one just created.
        if obj is None or int(getattr(obj, "request_id", 0) or 0) != rid:
            return None
        for created in getattr(obj, "presets", []):
            if created.id.is_factory:
                continue
            return {"id": created.id.value, "name": created.name,
                    "model_hash": created.id.hash,
                    "is_default": created.is_default}
        return None

    def delete_model_preset(self, preset_id, model_hash, timeout_ms=5000):
        """Delete a **user** device preset. Factory presets can't be removed."""
        cls = P.message_class("ModelPreset")
        m = cls()
        m.action = P.ACTION["DELETE"]
        m.request_id = self.next_request_id()
        pr = m.presets.add()
        pr.id.value = str(preset_id)
        pr.id.is_factory = False
        pr.id.hash = int(model_hash)
        self.send("ModelPreset", m)
        time.sleep(1.0)

    def load_model_preset(self, row, column, model_hash, preset_id,
                          is_factory=True, settle_s=1.5):
        """Apply a device preset to the block at (row, column).

        Not a ModelPreset(71) write — the device takes this as a **Grid UPDATE**
        with `update_type=MODEL_PRESET`, the preset id in `model_preset_to_load`,
        and a one-block grid naming the target (verified on 4.1.0).
        """
        g = P.message_class("Grid")()
        g.action = P.ACTION["UPDATE"]
        g.request_id = self.next_request_id()
        g.update_type = 1                       # GridMessage.UpdateType.MODEL_PRESET
        g.model_preset_to_load.value = str(preset_id)
        g.model_preset_to_load.is_factory = bool(is_factory)
        g.model_preset_to_load.hash = int(model_hash)
        ch = g.preset.chains.add()
        ch.row = int(row)
        mm = ch.models.add()
        mm.hash = int(model_hash)
        mm.column = int(column)
        self.send("Grid", g)
        time.sleep(settle_s)

    def read_state(self, command, timeout_ms=2500):
        cls = P.message_class(command)
        msg = cls()
        if "action" in [f.name for f in cls.DESCRIPTOR.fields]:
            msg.action = P.ACTION["READ"]
        if "request_id" in [f.name for f in cls.DESCRIPTOR.fields]:
            msg.request_id = self.next_request_id()
        _c, obj, _r = self.request(command, msg, timeout_ms=timeout_ms)
        return obj
