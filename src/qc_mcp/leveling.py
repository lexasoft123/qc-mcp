"""The preset-leveling service — the device half of Patchbay's Leveling bench.

Why this exists
---------------
Balancing a setlist means auditioning presets against each other and trimming
each one until they sit at the same loudness. On the device that is a lot of
walking: recall a preset, find its output block, nudge, recall the next one,
walk back. The bench collapses it into one screen — and this module is what
that screen talks to.

It is deliberately a *separate attached client*, not new MCP tools. The daemon
already fans device->host reports out to every client with a disjoint
`request_id` range, so the bench rides the same session Claude and Cortex
Control are on, and the user can keep editing in all three at once.

What "level" means here
-----------------------
The **Lane Output Control** (#23000, `Chain.output_control`) VOLUME parameter —
one per grid row. That is the value the leveling workflow wants because it is
stored *in the preset*, so trimming it and saving makes the balance permanent.
It is normalized 0..1 on the wire over a calibrated -40..+12 dB range
(`catalog.SYMBOLIC`), so the bench talks pure dB and converts at this edge.

Wire protocol — newline-delimited JSON on stdio, same shape as the daemon's.

    -> {"id": 1, "op": "hello"}
    <- {"id": 1, "ok": true, "firmware": "4.1.0", "mixer_db": [-40.0, 12.0]}
    -> {"id": 2, "op": "open", "folder_key": "...", "position": 3}
    <- {"id": 2, "ok": true, "preset": {...}}
    <- {"event": "meter", "outputs": {...}}          (unsolicited, while metering)

Threading: stdin is drained by one reader thread into a queue and **every**
device call happens on the main loop, because `QuadCortex._pending` is shared
state — a meter pump collecting on a second thread would eat the replies a read
is waiting for. See `transport._serialized` for the same trap one layer down.
"""
from __future__ import annotations

import json
import queue
import sys
import threading
import time

from . import catalog, directory
from . import protocol as P

#: LaneOutputControl parameter order.
VOLUME, PAN, MUTE, SOLO = 0, 1, 2, 3
LANE_OUTPUT_HASH = 23000

#: How often the loop pumps the device for streamed telemetry, in seconds.
PUMP = 0.04

_IOMETER = P.NAME_TO_CMD["IOMeter"]

#: Physical destinations a lane can be routed to, for the bench's lane labels.
#: `out_portid` values observed on a QC; unknown ids fall back to "Out <id>".
# NB the em-dash label is fine: it travels as JSON to the renderer and is drawn
# in HTML, never printed to a Windows console (the ASCII rule is about consoles).
OUT_PORTS = {
    0: "—", 1: "Out 1/2", 2: "Out 3/4", 3: "Send 1/2",
    16: "Mix bus", 19: "Multi Out",
}

#: Destinations that are NOT a way out of the preset: 0 is unrouted, 16 merges
#: into another row. Levelling a preset means moving what feeds the jacks, so the
#: bench's master knob skips these — moving a merge bus as well would apply the
#: same trim twice to any signal that passes through it.
INTERNAL_OUTS = {0, 16}

#: The IOMeter fields the bench shows, in the order it shows them.
METER_FIELDS = ("xlr_1", "xlr_2", "out_3", "out_4", "hp_l", "hp_r")
LIMITER_FIELDS = {"xlr_1": "xlr_1_limiter", "xlr_2": "xlr_2_limiter",
                  "out_3": "out_3_limiter", "out_4": "out_4_limiter"}


def db_of(norm):
    """Stored 0..1 -> dB on the lane output's calibrated range."""
    return catalog.to_display(LANE_OUTPUT_HASH, VOLUME, float(norm))


def norm_of(db):
    """dB -> the stored 0..1 the device wants."""
    return catalog.to_norm(LANE_OUTPUT_HASH, VOLUME, float(db))


def db_range():
    lo = catalog.SYMBOLIC["MIN_MIXER_DB"]
    hi = catalog.SYMBOLIC["MAX_MIXER_DB"]
    return lo, hi


# ─────────────────────────────────────────────────────────── reading state ────

def _lane(pos, chain, scene=0):
    """One grid row's output block as the bench sees it.

    NB the read-vs-delta indexing rule: in a whole-preset read the array
    *position* is the row and `chain.row` is 0, so the caller passes `pos`.

    `param_values` holds one entry per scene. Reading [0] unconditionally shows
    scene A's level whatever scene the device is actually on, which is wrong for
    any preset that scenes its output level.
    """
    ocs = list(chain.output_control)
    if not ocs:
        return None
    params = list(ocs[0].params)

    def value(idx, default=0.0):
        if idx >= len(params) or not params[idx].param_values:
            return default
        vals = params[idx].param_values
        return float(vals[scene if scene < len(vals) else 0].float_value)

    out_id = int(getattr(chain, "out_portid", 0) or 0)
    return {
        "row": pos,
        "db": round(db_of(value(VOLUME, norm_of(0.0))), 2),
        # PAN is stored 0..1 around centre; the app shows it as 50L..50R.
        "pan": round((value(PAN, 0.5) - 0.5) * 100.0),
        "mute": value(MUTE) >= 0.5,
        "solo": value(SOLO) >= 0.5,
        "out_portid": out_id,
        "out": OUT_PORTS.get(out_id, f"Out {out_id}"),
        # A lane routed nowhere is still in the preset but makes no sound, so the
        # bench shows it greyed rather than hiding it (hiding it would renumber
        # the rows and make the row indices lie).
        "active": out_id != 0,
        # …and only a lane that reaches an output counts toward the preset's level
        "physical": out_id not in INTERNAL_OUTS,
        "blocks": sum(1 for m in chain.models if m.hash),
    }


class Bench:
    """One attached session, plus the little bit of state the bench needs."""

    def __init__(self, qc):
        self.qc = qc
        self.metering = False
        self._last_meter = 0.0

    # -- device reads ------------------------------------------------------

    def preset_state(self):
        """Everything one bench column shows about the loaded preset.

        A whole-preset read can come back empty when the session is busy (two
        clients reading at once will do it), so retry through a reconnect once
        and then say so plainly — an `AttributeError` on `None.chains` tells the
        user nothing.
        """
        bp = self.qc.get_current_preset()
        if bp is None:
            try:
                self.qc.reconnect()
            except Exception:
                pass
            bp = self.qc.get_current_preset()
        if bp is None:
            raise RuntimeError(
                "the device did not answer a preset read; it may be busy - try again")
        pos_info = self.qc.get_setlist_position() or {}
        return self._state(bp, folder_key=pos_info.get("folder_key", ""),
                           position=pos_info.get("position"),
                           is_factory=pos_info.get("is_factory", False))

    def _state(self, bp, folder_key, position, is_factory):
        """One bench column's view of a preset. `bp` is a BinaryPreset, however
        it was obtained — a read, or the device's own recall broadcast."""
        scene = self.current_scene()
        lanes = []
        for pos, ch in enumerate(bp.chains):
            lane = _lane(pos, ch, scene)
            if lane:
                lanes.append(lane)
        return {
            "name": getattr(bp, "name", "") or "",
            "folder_key": folder_key or "",
            "position": position,
            "is_factory": bool(is_factory),
            "scene": scene,
            "scene_labels": [s for s in getattr(bp, "scene_labels", [])],
            "lanes": lanes,
        }

    def current_scene(self, default=0):
        """The active scene, best-effort.

        A Scene READ is not always answered promptly — and a preset's level is
        still perfectly editable when we don't know which scene is up — so this
        never fails the whole read the way an uncaught `QCError` would.
        """
        try:
            scene = self.qc.read_state("Scene", timeout_ms=1500)
        except Exception:
            return default
        return int(getattr(scene, "selected_scene", default)) if scene else default

    def folders(self, refresh=False):
        """Preset folders and their occupied slots, for the bench's picker.

        Prefers the on-disk snapshot because a live listing costs ~12s of
        streamed `File` messages; falls back to the live read when there is no
        snapshot yet, and keeps the result for next time.
        """
        cat = None if refresh else _snapshot()
        if not cat or not cat.get("presets"):
            try:
                live = self.qc.list_directory()
            except Exception:
                live = None
            if live and live.get("presets"):
                _save_snapshot(live)
                cat = live
        if not cat:
            cat = {"presets": []}
        out = []
        for folder in cat.get("presets", []):
            files = [{"position": f["index"], "name": f["name"],
                      "cloud_id": f.get("cloud_id", "") or ""}
                     for f in folder.get("files", []) if f.get("name")]
            if not files:
                continue
            out.append({
                "key": folder["key"],
                "name": folder.get("name") or folder["key"],
                "is_factory": bool(folder.get("is_factory")),
                # Downloads are addressed by cloud_id, not folder+position — the
                # bench has to carry that through or the recall is silently refused.
                "is_downloads": bool(folder.get("is_downloads")),
                "presets": sorted(files, key=lambda f: f["position"]),
            })
        return out

    # -- device writes -----------------------------------------------------

    def open(self, folder_key, position, is_factory=False, cloud_id="", settle=6.0):
        """Recall a preset, and build its state from the device's own answer.

        A recall makes the QC push the whole preset back — `RecallPreset` with a
        payload — so the bench LISTENS for that rather than asking. Waiting by
        polling the setlist pointer every 150ms and then re-reading the grid cost
        roughly thirty round trips for one preset change, every one of them
        landing on a device that was in the middle of loading; it was slow, and
        it made the device behave oddly while it worked.

        Falls back to a plain read if the broadcast never comes.
        """
        if cloud_id:
            self.qc.recall(downloads_key=cloud_id)
        else:
            self.qc.recall(folder_key=folder_key, position=int(position),
                           is_factory=bool(is_factory))

        want = P.NAME_TO_CMD["RecallPreset"]
        bp = None
        deadline = time.time() + settle
        while bp is None and time.time() < deadline:
            try:
                self.qc._collect(0.1)
            except Exception:
                break
            batch, self.qc._pending = self.qc._pending, []
            for cmd, obj, _raw, _pb in batch:
                if cmd == want and obj is not None and obj.HasField("preset"):
                    bp = obj.preset

        if bp is None:
            return self.preset_state()
        # The broadcast IS the confirmation the recall landed, so the pointer we
        # asked for is the pointer the device is on — no need to read it back.
        return self._state(bp, folder_key=folder_key,
                           position=None if cloud_id else int(position),
                           is_factory=bool(is_factory))

    def set_db(self, row, db):
        lo, hi = db_range()
        db = max(lo, min(hi, float(db)))
        self.qc.set_lane_param(int(row), "output_control", VOLUME, norm_of(db))
        return round(db, 2)

    def set_switch(self, row, which, on):
        idx = MUTE if which == "mute" else SOLO
        self.qc.set_lane_param(int(row), "output_control", idx, 1.0 if on else 0.0)
        return bool(on)

    def set_scene(self, index):
        self.qc.set_scene(int(index))
        # A scene switch must be *confirmed* before anything is written against
        # it; the bench's next level write would otherwise land on the old scene.
        self.qc._await_scene(int(index), timeout_s=2.0)
        return int(index)

    def save(self, name=""):
        pos = self.qc.get_setlist_position() or {}
        folder = pos.get("folder_key") or "/media/p4/Presets/My Presets"
        idx = int(pos.get("position", 0))
        if not name:
            bp = self.qc.get_current_preset()
            name = getattr(bp, "name", "") or ""
        if not name:
            raise ValueError("this slot has no preset name; pass one to save it")
        # File CREATE, never RecallPreset SAVE: the latter hangs the device on an
        # empty slot and needs a reboot.
        self.qc.write_preset_file(folder, idx, name)
        return {"name": name, "position": idx, "folder_key": folder}

    # -- streamed telemetry -------------------------------------------------

    def pump(self, emit):
        """Collect streamed frames and emit the newest meter reading.

        The device broadcasts telemetry with `request_id=0`, so the newest frame
        wins — never the first one buffered. Levels are **linear amplitude**
        (0..1), not dB; the bench passes them through and the UI converts, so
        this stays the one place that knows the wire format.

        Frames are not guaranteed: one bridge-mode session produced none at all
        (see PROTOCOL.md 5). Callers should render "no reading" as a resting
        state rather than treating it as a failure.
        """
        try:
            self.qc._collect(PUMP)
        except Exception:
            return
        # DRAIN, don't filter. The loop is single-threaded and only pumps
        # between commands, so nothing here is a reply anyone is waiting for.
        # Keeping "everything that isn't known telemetry" instead leaks: any
        # other broadcast the device makes piles up for ever, the list is
        # rescanned every tick, and the process grows in both memory and CPU
        # until it pegs a core.
        batch, self.qc._pending = self.qc._pending, []
        newest = None
        for cmd, obj, _raw, _pb in batch:
            if cmd == _IOMETER and obj is not None:
                newest = obj          # request_id=0 broadcast: the last one wins
        if newest is None:
            return
        outs = {}
        for f in METER_FIELDS:
            if not hasattr(newest, f):
                continue
            entry = {"level": float(getattr(newest, f))}
            lim = LIMITER_FIELDS.get(f)
            if lim and hasattr(newest, lim):
                entry["limit"] = float(getattr(newest, lim))
            outs[f] = entry
        self._last_meter = time.time()
        emit({"event": "meter", "at": self._last_meter, "outputs": outs})


def _snapshot_path():
    import os
    return os.environ.get("QC_CATALOG_JSON") or os.path.join(
        os.path.dirname(__file__), "..", "..", "interceptor", "catalog.json")


def _snapshot():
    """The gitignored on-disk DIRECTORY snapshot, if one has been written."""
    try:
        with open(_snapshot_path()) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _save_snapshot(cat):
    """Keep a good live listing warm — it holds personal library names, so it
    stays gitignored, exactly like the MCP server's own snapshot."""
    try:
        out = dict(cat)
        out.setdefault("_counts", directory.counts(cat))
        with open(_snapshot_path(), "w") as fh:
            json.dump(out, fh, indent=1)
    except (OSError, TypeError):
        pass


# ──────────────────────────────────────────────────────────────── the loop ────

def _stdin_reader(q):
    for line in sys.stdin:
        q.put(line)
    q.put(None)


def serve(socket_path):
    """Attach to the daemon and serve the bench on stdio until stdin closes."""
    from .daemon import attach

    out_lock = threading.Lock()

    def emit(obj):
        with out_lock:
            sys.stdout.write(json.dumps(obj) + "\n")
            sys.stdout.flush()

    try:
        qc = attach(socket_path)
    except Exception as exc:
        emit({"event": "fatal", "error": f"{type(exc).__name__}: {exc}"})
        return 1

    bench = Bench(qc)
    q: queue.Queue = queue.Queue()
    threading.Thread(target=_stdin_reader, args=(q,), daemon=True).start()

    ops = {
        "hello": lambda m: {"firmware": qc.firmware, "mixer_db": list(db_range())},
        "state": lambda m: {"preset": bench.preset_state()},
        "folders": lambda m: {"folders": bench.folders(bool(m.get("refresh")))},
        "open": lambda m: {"preset": bench.open(m["folder_key"], m["position"],
                                                m.get("is_factory", False),
                                                m.get("cloud_id", ""))},
        "level": lambda m: {"db": bench.set_db(m["row"], m["db"])},
        "switch": lambda m: {"on": bench.set_switch(m["row"], m["which"], m["on"])},
        "scene": lambda m: {"scene": bench.set_scene(m["index"])},
        "save": lambda m: {"saved": bench.save(m.get("name", ""))},
        "meter": lambda m: {"metering": _set_metering(bench, qc, m.get("on", True))},
    }

    while True:
        # Block for a command, and pump once per idle tick. `get_nowait` here
        # burns a core: the QC streams metronome ticks continuously, so the
        # transport almost always has something buffered and returns instantly
        # without its own sleep — the loop then spins as fast as it can and
        # hammers the daemon socket with reads.
        try:
            line = q.get(timeout=PUMP)
        except queue.Empty:
            bench.pump(emit)
            continue
        if line is None:
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        rid = msg.get("id")
        op = msg.get("op")
        try:
            fn = ops.get(op)
            if fn is None:
                emit({"id": rid, "ok": False, "error": f"unknown op {op!r}"})
                continue
            emit({"id": rid, "ok": True, **fn(msg)})
        except Exception as exc:           # never let one bad call kill the bench
            emit({"id": rid, "ok": False, "error": f"{type(exc).__name__}: {exc}"})

    try:
        qc.close()
    except Exception:
        pass
    return 0


def _set_metering(bench, qc, on):
    """Ask the device to stream IOMeter (what Cortex Control sends on connect).

    Observed: the QC answers this with frames only while audio is actually
    moving — a silent rig produces none at all, on our subscription or the app's.
    """
    m = P.message_class("IOMeter")()
    m.action = P.ACTION["CREATE" if on else "DELETE"]
    m.request_id = qc.next_request_id()
    qc.send("IOMeter", m)
    bench.metering = bool(on)
    return bench.metering
