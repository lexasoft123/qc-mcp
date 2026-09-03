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

from . import loudness

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
def _lane_ports(qc, row):
    """Current (in_port, out_port) for a lane, read back from the device."""
    bp = qc.get_current_preset()
    if not bp or row >= len(bp.chains):
        raise LevelingError("could not read the current preset (row %d)" % row)
    ch = bp.chains[row]
    return int(getattr(ch, "in_portid", 0)), int(getattr(ch, "out_portid", 0))


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
    in_row = out_row = None
    for pos, ch in enumerate(bp.chains):
        has_blocks = any(getattr(m, "hash", 0) for m in ch.models)
        ip = int(getattr(ch, "in_portid", 0) or 0)
        op = int(getattr(ch, "out_portid", 0) or 0)
        if in_row is None and ip in PHYSICAL_IN_PORTS:
            in_row = pos
        if op and op not in INTERNAL_OUT_PORTS and has_blocks:
            out_row = pos                      # the last such lane wins: it is the tail
    if in_row is None:
        in_row = 0
    if out_row is None:
        out_row = in_row
    return in_row, out_row


@contextlib.contextmanager
def reamp_routing(qc, row=0, in_port=IN_PORT_USB_5_6, out_port=OUT_PORT_USB_5_6,
                  restore=True, in_row=None, out_row=None, feed=True, tap=True):
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
    orig_in, _ = _lane_ports(qc, ir)
    _, orig_out = _lane_ports(qc, orow)
    try:
        if feed:
            qc.set_routing(ir, in_portid=in_port)
        if tap:
            qc.set_routing(orow, out_portid=out_port)
        yield {"in_row": ir, "out_row": orow,
               "original_in": orig_in, "original_out": orig_out}
    finally:
        if restore:
            try:
                if feed:
                    qc.set_routing(ir, in_portid=orig_in)
                if tap:
                    qc.set_routing(orow, out_portid=orig_out)
            except Exception:
                pass


# --------------------------------------------------------------- trim block ----
def find_gain_block(qc, row):
    """Column of the Gain block on `row`, or None. Reads position, not the id field."""
    bp = qc.get_current_preset()
    if not bp or row >= len(bp.chains):
        return None
    for col, m in enumerate(bp.chains[row].models):
        if getattr(m, "hash", 0) == GAIN_HASH:
            return col
    return None


def ensure_gain_block(qc, row):
    """Return the column of the lane's Gain trim, adding one at the end if absent."""
    col = find_gain_block(qc, row)
    if col is not None:
        return col, False
    bp = qc.get_current_preset()
    used = [i for i, m in enumerate(bp.chains[row].models) if getattr(m, "hash", 0)]
    col = (max(used) + 1) if used else 0
    qc.add_block(GAIN_HASH, row=row, column=col)
    placed = find_gain_block(qc, row)
    if placed is None:
        raise LevelingError("failed to add a Gain trim block on row %d" % row)
    return placed, True


def set_trim_db(qc, row, col, db):
    """Write the Gain block's LEVEL in real dB (taper-aware)."""
    from . import catalog
    db = loudness.clamp_db(db, GAIN_MIN_DB, GAIN_MAX_DB)
    qc.set_param(row, col, GAIN_LEVEL, catalog.to_norm(GAIN_HASH, GAIN_LEVEL, db))
    return db


def read_trim_db(qc, row, col):
    from . import catalog
    bp = qc.get_current_preset()
    try:
        pv = bp.chains[row].models[col].params[GAIN_LEVEL]
    except (IndexError, AttributeError):
        return 0.0
    raw = getattr(pv, "value", None)
    if raw is None:
        raw = getattr(pv, "float_value", 0.0)
    return float(catalog.to_display(GAIN_HASH, GAIN_LEVEL, float(raw)))


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
                  in_row=None, out_row=None):
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
    if perceived is None:
        perceived = (metric == "perceived")
    if in_row is None or out_row is None:
        found_in, found_out = measurement_rows(qc)
        in_row = found_in if in_row is None else in_row
        out_row = found_out if out_row is None else out_row
    out = {"target": target, "metric": metric, "tolerance": tolerance,
           "row": row, "in_row": in_row, "out_row": out_row,
           "iterations": [], "written": False}

    with reamp_routing(qc, in_row=in_row, out_row=out_row):
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
        if dry_run:
            out["suggested_db"] = delta
            return out
        if abs(delta) <= tolerance:
            out["converged"] = True
            out["final_delta_db"] = delta
            return out

        knob = trim or GainBlockTrim(qc, row)
        out["trim_block"] = knob.prepare()
        lo, hi = knob.limits
        current = knob.read()

        for n in range(2, max_iterations + 2):
            want = loudness.clamp_db(current + delta, lo, hi)
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
            # Guard: never trim into the ceiling. Back off and stop.
            if tp is not None and tp > true_peak_ceiling and delta and delta > 0:
                back = current - (tp - true_peak_ceiling)
                current = knob.write(loudness.clamp_db(back, lo, hi))
                step["limited_by"] = "true_peak"
                step["backed_off_to_db"] = current
                out["iterations"].append(step)
                if on_step:
                    on_step(step)
                out["limited"] = True
                break
            out["iterations"].append(step)
            if on_step:
                on_step(step)
            out["measurement"] = res
            if delta is not None and abs(delta) <= tolerance:
                out["converged"] = True
                break

    last = out["iterations"][-1]
    out["final_delta_db"] = last.get("delta_db")
    out["final_trim_db"] = last.get("backed_off_to_db", last.get("wrote_db"))
    out.setdefault("converged", False)
    return out


def level_scenes(qc, scenes=None, target=DEFAULT_TARGET_LUFS, metric="lufs",
                 tolerance=DEFAULT_TOLERANCE_LU, row=0, di_path=None, dry_run=False,
                 in_row=None, out_row=None):
    """Measure and trim each scene of the current preset independently.

    Per-scene values only exist once the parameter is assigned to scenes, which is what
    `set_param_scenes` does; it also confirms each scene switch before writing, because
    a fixed sleep races the device and silently drops values.
    """
    perceived = (metric == "perceived")
    wanted = list(scenes) if scenes else list(range(MAX_SCENES))
    out = {"target": target, "metric": metric, "row": row, "scenes": [],
           "written": False}

    if in_row is None or out_row is None:
        found_in, found_out = measurement_rows(qc)
        in_row = found_in if in_row is None else in_row
        out_row = found_out if out_row is None else out_row
    col = None
    with reamp_routing(qc, in_row=in_row, out_row=out_row):
        if not dry_run:
            col, added = ensure_gain_block(qc, row)
            out["trim_block"] = {"row": row, "column": col, "added": added}
        base = read_trim_db(qc, row, col) if col is not None else 0.0
        trims = [base] * MAX_SCENES

        for sc in wanted:
            if not qc.set_scene(sc) or not qc._await_scene(sc):
                out["scenes"].append({"scene": sc, "skipped": "scene switch not confirmed"})
                continue
            res = measure(di_path=di_path, perceived=perceived)
            if res.get("error"):
                out["scenes"].append({"scene": sc, "skipped": res["error"]})
                continue
            delta = _delta(res, target, metric)
            row_out = {"scene": sc, "measured": _metric(res, metric),
                       "delta_db": delta, "true_peak_dbtp": res.get("true_peak_dbtp")}
            if delta is not None:
                want = loudness.clamp_db(base + delta, GAIN_MIN_DB, GAIN_MAX_DB)
                tp = res.get("true_peak_dbtp")
                headroom = None if tp is None else (TRUE_PEAK_CEILING_DBTP - tp)
                if headroom is not None and delta > headroom:
                    want = loudness.clamp_db(base + headroom, GAIN_MIN_DB, GAIN_MAX_DB)
                    row_out["limited_by"] = "true_peak"
                row_out["trim_db"] = want
                trims[sc] = want
            out["scenes"].append(row_out)

        if not dry_run and col is not None and any(
                r.get("trim_db") is not None for r in out["scenes"]):
            from . import catalog
            qc.set_param_scenes(row, col, GAIN_LEVEL,
                                [catalog.to_norm(GAIN_HASH, GAIN_LEVEL, t)
                                 for t in trims])
            out["written"] = True
        qc.set_scene(0)
        qc._await_scene(0)
    return out
