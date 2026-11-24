"""The closed AUTOMATIC leveling loop: measure a preset, trim it, verify, move on.

Distinct from `leveling.py`, which is the Bench — the manual, by-ear service the
Patchbay Leveling view attaches to over a socket. The Bench trims Lane Output Control
VOLUME and streams meters; this module measures real loudness over the USB audio path
and closes the loop without a human in it. They are meant to be used together.

Device work goes through `transport.QuadCortex`; audio through `audio_io`; maths through
`loudness`. Nothing here talks to CoreAudio or protobuf directly.

The measurement rig, per the Quad Cortex manual's USB channel map:

    Mac  --host out 5/6-->  Grid USB Input 5/6   (lane in_portid 12)
                                  |
                            the preset under test
                                  |
    Mac  <--host in 5/6---  Grid USB Output 5/6  (lane out_portid 14)

Both ends are temporary. `reamp_routing` records the lane's original ports and puts them
back in a `finally`, so an aborted run never leaves a preset wired to USB.

Which knob gets trimmed: a `Gain` block (16005) appended to the measured lane, param 0
LEVEL, -60..+12 dB. Never the amp master, cab or drive — those change the tone rather
than the level. Per-scene leveling writes that same param through `set_param_scenes`,
which assigns it to scenes first and confirms each scene switch before writing.
"""
from __future__ import annotations

import contextlib
import time

from . import loudness

#: The device does not apply a routing change instantly. Playing into it too soon
#: captures part of the OLD routing — silence, since the tap is not live yet — which
#: drags the integrated loudness down and makes the first measurement of a session
#: disagree with every later one. Observed as a 2.6 dB discrepancy on real hardware.
ROUTING_SETTLE_S = 0.35

GAIN_HASH = 16005          # "Gain" utility block
GAIN_LEVEL = 0             # param index of LEVEL, -60..+12 dB, JUCE skew 3.8018
GAIN_MIN_DB, GAIN_MAX_DB = -60.0, 12.0

IN_PORT_USB_5_6 = 12       # GainCalInputPortParameter.InputPortId.USB_IN_5_6
OUT_PORT_USB_5_6 = 14      # lane out_port 14 == USB 5/6

DEFAULT_TARGET_LUFS = -18.0
DEFAULT_TOLERANCE_LU = 0.5
DEFAULT_MAX_ITERATIONS = 4
TRUE_PEAK_CEILING_DBTP = -1.0    # back the trim off rather than cross this
MAX_SCENES = 8


class LevelingError(RuntimeError):
    pass


# ------------------------------------------------------------------ routing ----
def require_active(qc, expected=None):
    """Check the target again before each batch write or readback."""
    pointer = qc.get_setlist_position()
    if (not pointer or (expected is not None and pointer != expected)
            or not ((pointer.get("folder_key") and pointer.get("position") is not None)
                    or pointer.get("downloads_key") or pointer.get("plugin_key"))):
        raise LevelingError("could not confirm the intended preset is still active")
    return pointer


# Ports that mean "this lane feeds another lane", not "this lane leaves the box".
INTERNAL_OUT_PORTS = {0, 16, 17, 18}
PHYSICAL_IN_PORTS = {1, 2, 3, 4, 5, 6}      # In 1/2, Return 1/2 and their pairs


def measurement_rows(qc):
    """Which lane takes the instrument in, and which one carries the sound out.

    They are often not the same lane: a two-row preset takes In 1 on row 0, feeds
    row 2 over the internal bus (out_port 16-18), and leaves from row 2. Swapping
    both ends of one row would then either capture nothing or cut the chain in
    half, which is exactly what a fake single-lane device cannot show you.
    """
    bp = qc.get_current_preset()
    if not bp:
        raise LevelingError("could not read the current preset")
    head = next((i for i, ch in enumerate(bp.chains) if ch.in_portid in PHYSICAL_IN_PORTS), None)
    if head is None:
        raise LevelingError("could not find an instrument input for the reamp")
    internal = {16: (2,), 17: (3,), 18: (2, 3)}
    reachable, outputs = [head], []
    seen = set()
    while reachable:
        row = reachable.pop(0)
        if row in seen or row >= len(bp.chains):
            continue
        seen.add(row)
        chain = bp.chains[row]
        port = chain.out_portid
        if port in internal:
            reachable.extend(internal[port])
        elif port not in INTERNAL_OUT_PORTS:
            outputs.append(row)
        if row + 1 < len(bp.chains) and bp.chains[row + 1].in_portid == 7:
            reachable.append(row + 1)
    if outputs:
        return head, outputs[-1]
    # Parallel layouts can sum unrouted amp lanes into one shared output lane.
    mixed = [i for i, chain in enumerate(bp.chains)
             if chain.out_portid not in INTERNAL_OUT_PORTS]
    if bp.chains[head].out_portid == 0 and len(mixed) == 1:
        return head, mixed[0]
    raise LevelingError("could not find the instrument path's physical output")


@contextlib.contextmanager
def reamp_routing(qc, row=0, in_port=IN_PORT_USB_5_6, out_port=OUT_PORT_USB_5_6,
                  restore=True, in_row=None, out_row=None, feed=True, tap=True, expected=None):
    """Point a preset at the USB reamp path for the duration of a measurement.

    `in_row`/`out_row` default to `row`; pass both (or let `measurement_rows` find
    them) when the signal enters and leaves on different lanes. `feed=False` leaves
    the instrument input alone and only taps the output — the safe shape for
    measuring what the player is actually playing.

    Always restores, including on error: a preset left listening to USB is silent
    to the player and looks like a broken preset.
    """
    ir = row if in_row is None else in_row
    orow = row if out_row is None else out_row
    # Reserve the working grid before a concurrent save can pass its guard.
    with qc._io_lock:
        if getattr(qc, "_reamp_incomplete", False):
            raise LevelingError("temporary reamp routing is active or restoration was not verified")
        expected = require_active(qc, expected)
        bp = qc.get_current_preset()
        require_active(qc, expected)
        if bp is None or not (0 <= ir < len(bp.chains) and 0 <= orow < len(bp.chains)):
            raise LevelingError("could not read both original reamp routes")
        orig_in = bp.chains[ir].in_portid
        orig_out = bp.chains[orow].out_portid
        routes = []
        if feed:
            routes.append((ir, "in_portid", in_port, orig_in))
        if tap:
            routes.append((orow, "out_portid", out_port, orig_out))

        qc._reamp_incomplete = True

    def verify(original):
        current = qc.get_current_preset()
        return current is not None and all(
            r < len(current.chains) and getattr(current.chains[r], field) == (old if original else new)
            for r, field, new, old in routes)

    touched = []
    try:
        for r, field, new, old in routes:
            require_active(qc, expected)
            touched.append((r, field, old))
            qc.set_routing(r, **{field: new})
        time.sleep(ROUTING_SETTLE_S)
        if not verify(False):
            time.sleep(ROUTING_SETTLE_S)
            if not verify(False):
                raise LevelingError("could not verify temporary reamp routing")
        require_active(qc, expected)
        yield {"in_row": ir, "out_row": orow, "original_in": orig_in,
               "original_out": orig_out, "applied_in": in_port if feed else orig_in,
               "applied_out": out_port if tap else orig_out, "routing_ok": True}
    finally:
        if restore:
            errors = []
            for r, field, old in touched:
                try:
                    require_active(qc, expected)
                    qc.set_routing(r, **{field: old})
                except Exception as exc:
                    errors.append(f"row {r} {field}: {exc}")
            try:
                time.sleep(ROUTING_SETTLE_S)
                require_active(qc, expected)
                if not verify(True):
                    errors.append("readback did not match the original routes")
            except Exception as exc:
                errors.append(f"readback failed: {exc}")
            if errors:
                raise LevelingError("could not restore reamp routing: " + "; ".join(errors))
            qc._reamp_incomplete = False


# --------------------------------------------------------------- trim block ----
def _gain_positions(qc, row):
    bp = qc.get_current_preset()
    if bp is None or not 0 <= row < len(bp.chains):
        raise LevelingError(f"could not read row {row} for the Gain trim")
    chain = bp.chains[row]
    blocks = [i for i, model in enumerate(chain.models) if model.hash]
    # A gain after every other block and after the mix point scales the entire
    # row. A gain feeding an amp or only one side of a split changes the tone.
    last_block = max(blocks, default=-1)
    last_mix = max((point.mix for point in getattr(chain, "split_control_points", ())), default=-1)
    if (last_block >= 0 and last_block > last_mix
            and chain.models[last_block].hash == GAIN_HASH):
        return last_block, None
    return None, max(last_block, last_mix) + 1


def find_gain_block(qc, row):
    return _gain_positions(qc, row)[0]


def ensure_gain_block(qc, row):
    """Return the verified downstream Gain column, adding one if necessary."""
    existing, column = _gain_positions(qc, row)
    if existing is not None:
        return existing, False
    if column >= 8:
        raise LevelingError(f"no downstream slot for a Gain trim on row {row}")
    qc.add_block(GAIN_HASH, row=row, column=column)
    if find_gain_block(qc, row) != column:
        raise LevelingError(f"failed to add a Gain trim block on row {row}")
    return column, True


def set_trim_db(qc, row, col, db):
    """Write the Gain block's LEVEL in real dB (taper-aware)."""
    from . import catalog
    db = loudness.clamp_db(db, GAIN_MIN_DB, GAIN_MAX_DB)
    qc.set_param(row, col, GAIN_LEVEL, catalog.to_norm(GAIN_HASH, GAIN_LEVEL, db))
    return db


def _trim_values(qc, row, col):
    bp = qc.get_current_preset()
    try:
        block = bp.chains[row].models[col]
        p = block.params[GAIN_LEVEL]
    except (AttributeError, IndexError) as exc:
        raise LevelingError("could not read the Gain block's LEVEL") from exc
    values = [v.float_value for v in p.param_values if v.WhichOneof("value") == "float_value"]
    if (block.hash != GAIN_HASH or len(values) != len(p.param_values)
            or len(values) not in (1, MAX_SCENES) or (p.scene_mode and len(values) != MAX_SCENES)):
        raise LevelingError("could not read the Gain block's LEVEL values")
    return p.scene_mode, values if p.scene_mode else values[:1] * MAX_SCENES


def read_trim_db(qc, row, col, scene=None):
    from . import catalog
    varying, values = _trim_values(qc, row, col)
    if scene is None and varying:
        st = qc.read_state("Scene", strict=True)
        if st is None or not st.HasField("selected_scene") or not 0 <= st.selected_scene < MAX_SCENES:
            raise LevelingError("could not read the active Gain trim scene")
        scene = st.selected_scene
    return float(catalog.to_display(GAIN_HASH, GAIN_LEVEL, values[scene or 0]))


# ----------------------------------------------------------------- trims ----
class GainBlockTrim:
    """Trim with a Gain block (16005) appended to the lane.

    The general-purpose choice: it works on any lane, and its LEVEL can be given
    per-scene values, which a lane sub-block cannot.
    """

    name = "gain_block"

    def __init__(self, qc, row):
        self.qc, self.row = qc, row
        self.col = None
        self.added = False

    def prepare(self):
        self.col, self.added = ensure_gain_block(self.qc, self.row)
        return {"row": self.row, "column": self.col, "added": self.added,
                "knob": self.name}

    def read(self):
        return read_trim_db(self.qc, self.row, self.col)

    def write(self, db):
        return set_trim_db(self.qc, self.row, self.col, db)

    limits = (GAIN_MIN_DB, GAIN_MAX_DB)


class LaneVolumeTrim:
    """Trim the lane's own output volume (LaneOutputControl 23000, param VOLUME).

    Preferable when levelling a whole preset: it adds nothing to the grid, it is
    stored in the preset, and it is the same knob the QC's own OUT LEVEL shows.
    Its range is a calibrated -40..+12 dB. It cannot carry per-scene values, so
    scene work still goes through GainBlockTrim.
    """

    name = "lane_volume"

    def __init__(self, bench, row):
        self.bench, self.row = bench, row
        from . import leveling as _bench_mod
        self.limits = tuple(_bench_mod.db_range())

    def prepare(self):
        return {"row": self.row, "knob": self.name}

    def read(self):
        state = self.bench.preset_state() or {}
        for lane in state.get("lanes", []) or []:
            if lane.get("row") == self.row:
                return float(lane.get("db", 0.0))
        return 0.0

    def write(self, db):
        return float(self.bench.set_db(self.row, db))


# ------------------------------------------------------------- measurement ----
def measure(di_path=None, perceived=False, in_channels=None, out_channels=None):
    """Play the reference riff into whatever the grid is currently set to, and measure."""
    from . import audio_io
    import os
    path = di_path or audio_io.DEFAULT_SAMPLE_PATH
    if not os.path.exists(path):
        raise LevelingError(
            "no reference sample at %s — record one with sample_arm(), play a riff, "
            "then sample_stop()" % path)
    stim, rate = audio_io.load_wav(path)
    rec, rate = audio_io.play_and_record(
        stim, rate=rate,
        out_channels=tuple(out_channels) if out_channels
        else audio_io.DEFAULT_REAMP_CHANNELS,
        in_channels=tuple(in_channels) if in_channels
        else audio_io.DEFAULT_MEASURE_CHANNELS)
    return loudness.analyze(rec, rate, perceived=perceived)


def _metric(result, metric):
    if metric == "perceived":
        n5 = result.get("zwicker_n5_rel")
        return None if n5 is None else float(n5)
    return result.get("lufs_integrated")


def _delta(result, target, metric):
    """Correction in dB. For perceived loudness, sones are a ratio scale, so the dB
    move that would equal the target is 10*log10(target/measured)."""
    import math
    got = _metric(result, metric)
    if got is None:
        return None
    if metric == "perceived":
        if got <= 0 or target <= 0:
            return None
        return round(10.0 * math.log10(float(target) / float(got)), 2)
    return round(float(target) - float(got), 2)


# ----------------------------------------------------------------- the loop ----
def level_current(qc, target=DEFAULT_TARGET_LUFS, metric="lufs",
                  tolerance=DEFAULT_TOLERANCE_LU, max_iterations=DEFAULT_MAX_ITERATIONS,
                  row=0, di_path=None, perceived=None, dry_run=False,
                  true_peak_ceiling=TRUE_PEAK_CEILING_DBTP, trim=None, on_step=None,
                  in_row=None, out_row=None, expected=None):
    """Measure -> correct -> verify the CURRENT preset until it lands within tolerance.

    `dry_run=True` measures once and reports the correction without touching anything —
    the report-only mode. Returns a dict with every iteration, so a caller can show the
    convergence rather than just the final number.

    `in_row`/`out_row` say where the signal enters and leaves; when omitted they are
    found with `measurement_rows`, because a preset routinely takes its input on one
    lane and leaves from another and getting that wrong measures nothing.

    `trim` picks the knob: a `GainBlockTrim` (default) or a `LaneVolumeTrim` when the
    caller has a Bench and would rather move the lane's own output level than add a
    block. `on_step(step)` is called after each iteration so a UI can follow along
    instead of waiting for the whole run.
    """
    expected = require_active(qc, expected)
    if perceived is None:
        perceived = (metric == "perceived")
    if in_row is None or out_row is None:
        found_in, found_out = measurement_rows(qc)
        in_row = found_in if in_row is None else in_row
        out_row = found_out if out_row is None else out_row
    if trim is None:
        row = out_row
    out = {"target": target, "metric": metric, "tolerance": tolerance,
           "row": row, "in_row": in_row, "out_row": out_row,
           "iterations": [], "written": False}

    with reamp_routing(qc, in_row=in_row, out_row=out_row, expected=expected):
        first = measure(di_path=di_path, perceived=perceived)
        if first.get("error"):
            out["error"] = first["error"]
            out["measurement"] = first
            return out
        delta = _delta(first, target, metric)
        first_step = {"n": 1, "measured": _metric(first, metric), "delta_db": delta,
                      "true_peak_dbtp": first.get("true_peak_dbtp"), "wrote_db": None}
        out["iterations"].append(first_step)
        if on_step:
            on_step(first_step)
        out["measurement"] = first
        if delta is None:
            out["error"] = "could not derive a correction from the measurement"
            return out
        # Over the ceiling ALREADY, before anything is written. Say so on every
        # path out of here, including the two that return early: a preset can be
        # exactly on target and clipping, and reporting a clean convergence over
        # +0.2 dBTP is the report lying about the thing it exists to check.
        # Not corrected automatically — that would detune a preset that is where
        # it was asked to be — but never hidden either.
        tp0 = first.get("true_peak_dbtp")
        if tp0 is not None and tp0 > true_peak_ceiling:
            out["over_ceiling"] = True
            out["true_peak_dbtp"] = tp0
            out["ceiling_dbtp"] = true_peak_ceiling

        if dry_run:
            out["suggested_db"] = delta
            return out
        if abs(delta) <= tolerance:
            out["converged"] = True
            out["final_delta_db"] = delta
            return out

        knob = trim or GainBlockTrim(qc, row)
        require_active(qc, expected)
        out["trim_block"] = knob.prepare()
        lo, hi = knob.limits
        current = knob.read()

        for n in range(2, max_iterations + 2):
            ideal = current + delta
            want = loudness.clamp_db(ideal, lo, hi)
            # The knob has ends. A preset that needs more lift than the Gain
            # block has (-60..+12 dB) used to sit at +12 rewriting +12 for every
            # remaining pass and then report "did not converge" with no reason —
            # from the outside, a preset that simply refuses to level.
            pinned = abs(ideal - want) > 1e-6
            require_active(qc, expected)
            current = knob.write(want)
            out["written"] = True
            res = measure(di_path=di_path, perceived=perceived)
            if res.get("error"):
                out["error"] = res["error"]
                break
            delta = _delta(res, target, metric)
            tp = res.get("true_peak_dbtp")
            step = {"n": n, "measured": _metric(res, metric), "delta_db": delta,
                    "true_peak_dbtp": tp, "wrote_db": current}
            # Guard: never leave the output over the ceiling. Back off and stop.
            #
            # This used to also require `delta > 0` — that the loop still wanted
            # MORE level — which meant a preset that reached its target exactly
            # while clipping was reported as converged. Measured: target hit to
            # 0.00 LU at +3.09 dBTP, "converged": true. Being on target is not a
            # reason to accept clipping; the ceiling is a ceiling.
            if tp is not None and tp > true_peak_ceiling:
                back = current - (tp - true_peak_ceiling)
                require_active(qc, expected)
                current = knob.write(loudness.clamp_db(back, lo, hi))
                step["limited_by"] = "true_peak"
                step["backed_off_to_db"] = current
                out["iterations"].append(step)
                if on_step:
                    on_step(step)
                out["limited"] = True
                break
            if pinned:
                step["limited_by"] = "range"
                step["knob_limit_db"] = hi if ideal > want else lo
                step["short_by_db"] = round(ideal - want, 2)
            out["iterations"].append(step)
            if on_step:
                on_step(step)
            out["measurement"] = res
            if delta is not None and abs(delta) <= tolerance:
                out["converged"] = True
                break
            if pinned:
                # Against the stop and still short: more passes write the same
                # number and measure the same result.
                out["limited"] = True
                break

    last = out["iterations"][-1]
    out["final_delta_db"] = last.get("delta_db")
    out["final_trim_db"] = last.get("backed_off_to_db", last.get("wrote_db"))
    # Say why, at the top level, where a report reads it: "did not converge" on
    # its own tells nobody whether to try again or to change the preset.
    if last.get("limited_by"):
        out["limited_by"] = last["limited_by"]
        if last.get("short_by_db") is not None:
            out["short_by_db"] = last["short_by_db"]
            out["knob_limit_db"] = last.get("knob_limit_db")
    out.setdefault("converged", False)
    return out


def level_scenes(qc, scenes=None, target=DEFAULT_TARGET_LUFS, metric="lufs",
                 tolerance=DEFAULT_TOLERANCE_LU, row=0, di_path=None, dry_run=False,
                 in_row=None, out_row=None, expected=None):
    """Measure and trim each scene of the current preset independently.

    Per-scene values only exist once the parameter is assigned to scenes, which is what
    `set_param_scenes` does; it also confirms each scene switch before writing, because
    a fixed sleep races the device and silently drops values.
    """
    from . import catalog

    wanted = list(range(MAX_SCENES)) if scenes is None else list(dict.fromkeys(scenes))
    if any(not isinstance(scene, int) or not 0 <= scene < MAX_SCENES for scene in wanted):
        raise LevelingError("scenes must be numbered 0–7")
    perceived = metric == "perceived"
    if not wanted:
        return {"target": target, "metric": metric, "row": row, "scenes": [], "written": False}
    expected = require_active(qc, expected)
    if in_row is None or out_row is None:
        found_in, found_out = measurement_rows(qc)
        in_row = found_in if in_row is None else in_row
        out_row = found_out if out_row is None else out_row
    row = out_row
    out = {"target": target, "metric": metric, "row": row, "scenes": [], "written": False}
    col = None if dry_run else find_gain_block(qc, row)
    unity = catalog.to_norm(GAIN_HASH, GAIN_LEVEL, 0.0)
    assigned, values = (False, [unity] * MAX_SCENES) if col is None else _trim_values(qc, row, col)
    trims = {}
    with reamp_routing(qc, in_row=in_row, out_row=out_row, expected=expected):
        try:
            for scene in wanted:
                require_active(qc, expected)
                qc.set_scene(scene)
                if not qc._await_scene(scene):
                    error = "scene switch not confirmed"
                    out["scenes"].append({"scene": scene, "skipped": error})
                    out["error"] = error
                    break
                res = measure(di_path=di_path, perceived=perceived)
                if res.get("error"):
                    out["scenes"].append({"scene": scene, "skipped": res["error"]})
                    continue
                delta = _delta(res, target, metric)
                tp = res.get("true_peak_dbtp")
                result = {"scene": scene, "measured": _metric(res, metric),
                          "delta_db": delta, "true_peak_dbtp": tp}
                if delta is not None and not dry_run:
                    base = catalog.to_display(GAIN_HASH, GAIN_LEVEL, values[scene])
                    headroom = None if tp is None else TRUE_PEAK_CEILING_DBTP - tp
                    if headroom is not None and delta > headroom:
                        delta = headroom
                        result["limited_by"] = "true_peak"
                    want = loudness.clamp_db(base + delta, GAIN_MIN_DB, GAIN_MAX_DB)
                    result["trim_db"] = want
                    if abs(base - want) > tolerance:
                        trims[scene] = catalog.to_norm(GAIN_HASH, GAIN_LEVEL, want)
                out["scenes"].append(result)
        finally:
            require_active(qc, expected)
            qc.set_scene(0)
            if not qc._await_scene(0):
                raise LevelingError("could not restore scene A")

        if trims and not out.get("error"):
            require_active(qc, expected)
            col, added = ensure_gain_block(qc, row)
            out["trim_block"] = {"row": row, "column": col, "added": added}
            if assigned:
                qc.set_assigned_param_scenes(row, col, GAIN_LEVEL, trims, expected=expected)
            else:
                for scene, value in trims.items():
                    values[scene] = value
                qc.set_param_scenes(row, col, GAIN_LEVEL, values, expected=expected)
            out["written"] = True
    return out
