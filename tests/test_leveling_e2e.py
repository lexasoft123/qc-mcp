"""The whole leveling loop, end to end, with everything real except the hardware.

The existing loop tests script `autolevel.measure` with canned numbers, which
proves the arithmetic and nothing about the signal. This runs the real thing:

    a real riff (qc_mcp.riffs, synthesised, identical everywhere)
      -> a modelled preset that actually applies its gain and clips
        -> the real loudness maths (ITU-R BS.1770 K-weighting, true peak)
          -> the real autolevel loop, whose writes really move the model

so a sign error, a taper error or a loop that does not converge has nowhere to
hide. Nothing here touches a device, an audio interface, or a socket.

Run: .venv/bin/python tests/test_leveling_e2e.py
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

import numpy as np                                  # noqa: E402
from qc_mcp import autolevel, loudness, riffs        # noqa: E402
from qc_mcp import leveling as bench_mod             # noqa: E402

RATE = riffs.RATE
LANE_OUTPUT_HASH = 23000        # LaneOutputControl, whose param 0 is VOLUME
ok = fail = 0
_cache = {}


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
    else:
        fail += 1
        print("FAIL: %s %s" % (name, detail))


def riff(name):
    if name not in _cache:
        _cache[name] = riffs.render(name)
    return _cache[name]


# ── the modelled preset ──────────────────────────────────────────────────

class Amp:
    """A preset, as far as a loudness measurement can tell.

    Gain, then a soft knee, then the lane fader. The knee matters: a preset that
    is simply multiplied cannot ever trip the true-peak guard, and the guard is
    one of the things being tested.
    """

    def __init__(self, name, gain_db, drive=0.0):
        self.name = name
        self.gain_db = float(gain_db)
        self.drive = float(drive)          # 0 = clean, 1 = squashed
        self.trim_db = 0.0                 # what the loop writes

    def render(self, x):
        y = x * (10.0 ** (self.gain_db / 20.0))
        if self.drive > 0:
            # tanh knee, normalised so a small signal passes at unity
            k = 1.0 + 9.0 * self.drive
            y = np.tanh(y * k) / k
        return y * (10.0 ** (self.trim_db / 20.0))

    def measure(self, source):
        return loudness.analyze(self.render(riff(source)), RATE)


# ── the device, wired to the model ───────────────────────────────────────

class _Param:
    """What the device echoes back: a normalised value, not dB."""

    def __init__(self, value=0.0):
        self.value = float(value)


class _Model:
    def __init__(self, hash_=0):
        self.hash = hash_
        self.params = []

    def set(self, index, value):
        while len(self.params) <= index:
            self.params.append(_Param(0.0))
        self.params[index].value = float(value)


class _Chain:
    def __init__(self, in_port, out_port):
        self.in_portid = in_port
        self.out_portid = out_port
        self.models = [_Model(12345), _Model(0), _Model(0)]


class _Preset:
    def __init__(self, chains):
        self.chains = chains


class Device:
    """A Quad Cortex whose lane fader really changes what the next measurement
    sees — which is the whole point of an end-to-end test of a closed loop."""

    def __init__(self, amp, in_port=1, out_port=19):
        self.amp = amp
        self.chain = _Chain(in_port, out_port)
        self.routing = []
        self.writes = []
        self.scene_writes = []
        self.scene = 0
        self.scene_trims = {}
        self.saved = []

    # -- reads
    def get_current_preset(self, timeout_ms=6000):
        return _Preset([self.chain])

    # -- routing
    def set_routing(self, row, in_portid=None, out_portid=None):
        self.routing.append((row, in_portid, out_portid))
        if in_portid is not None:
            self.chain.in_portid = in_portid
        if out_portid is not None:
            self.chain.out_portid = out_portid
        return True

    # -- writes that move the model
    def set_param(self, row, column, param_index, value):
        """A write really changes what the next measurement hears.

        The knob has to be read the way the DEVICE would read it, not the way
        the caller meant it: the Gain block's LEVEL is -60..+12 dB on a JUCE
        skew of 3.8018, and the lane fader is -40..+12 linear. Treating one as
        the other is a taper error, which is exactly the class of bug an
        end-to-end test exists to catch — the first draft of this fake made
        precisely that mistake and the loop oscillated for ever.
        """
        from qc_mcp import catalog
        self.writes.append((row, column, param_index, value))
        model = self.chain.models[column] if column < len(self.chain.models) else None
        if model is not None:
            model.set(param_index, value)
        if model is not None and model.hash == autolevel.GAIN_HASH \
                and param_index == autolevel.GAIN_LEVEL:
            self.amp.trim_db = float(catalog.to_display(
                autolevel.GAIN_HASH, autolevel.GAIN_LEVEL, float(value)))
        elif model is not None and model.hash == LANE_OUTPUT_HASH and param_index == 0:
            self.amp.trim_db = bench_mod.db_of(value)
        return True

    def set_param_scenes(self, row, column, param_index, values):
        self.scene_writes.append((row, column, param_index, list(values)))
        return True

    def add_block(self, model_hash, row=0, column=0, wait_echo_ms=800):
        while len(self.chain.models) <= column:
            self.chain.models.append(_Model(0))
        self.chain.models[column] = _Model(model_hash)
        return True

    def set_scene(self, scene):
        self.scene = scene
        return True

    def _await_scene(self, scene, timeout_s=2.0):
        self.scene = scene
        return True


def wire(device, source="chords"):
    """Point autolevel.measure at the model instead of an audio interface."""
    def fake(di_path=None, perceived=False, in_channels=None, out_channels=None):
        return device.amp.measure(source)
    autolevel.measure = fake
    return fake


# ── the riffs themselves ─────────────────────────────────────────────────

def test_every_riff_is_worth_measuring():
    """The recorded riff this feature was first tried with peaked at -22.8 dBFS
    with 74% of it near silence, and nothing downstream could tell that from a
    quiet preset. These have to be better than that, by the same measures."""
    for name, why, secs in riffs.catalogue():
        a = riff(name)
        r = loudness.analyze(a, RATE)
        quiet = float(np.mean(np.abs(a) < 10 ** (-40 / 20.0)))
        check("%s is long enough" % name, 2.5 <= secs <= 12.0, "%.2fs" % secs)
        check("%s peaks where it says" % name,
              abs(r["sample_peak_dbfs"] - riffs.PEAK_DBFS) < 0.01, r["sample_peak_dbfs"])
        check("%s has headroom" % name, r["true_peak_dbtp"] < 0.0, r["true_peak_dbtp"])
        check("%s is loud enough to measure" % name,
              r["lufs_integrated"] > -30.0, r["lufs_integrated"])
        check("%s is mostly signal" % name, quiet < 0.5, "%.0f%% quiet" % (quiet * 100))
        check("%s is not silent" % name, not r["silent"])
        check("%s has a description" % name, len(why) > 30)


def test_riffs_are_identical_on_every_machine():
    """Cached to disk and used as a measurement reference, so the bytes have to
    be the same twice — otherwise two runs of the bench are not comparable."""
    for name, _w, _s in riffs.catalogue():
        a, b = riffs.render(name), riffs.render(name)
        check("%s renders identically" % name, np.array_equal(a, b))
    check("an unknown riff says so", _raises(KeyError, riffs.render, "nope"))


def test_riffs_differ_from_each_other():
    """Five copies of one riff would be a worse catalogue than one riff."""
    sigs = {}
    for name, _w, _s in riffs.catalogue():
        r = loudness.analyze(riff(name), RATE)
        sigs[name] = (round(r["lufs_integrated"], 1), round(r["duration_s"], 1))
    check("each riff is its own signal", len(set(sigs.values())) == len(sigs), sigs)


# ── the loop, on real audio ──────────────────────────────────────────────

PRESETS = [
    Amp("clean", gain_db=0.0),
    Amp("crunch", gain_db=+9.0, drive=0.25),
    Amp("lead", gain_db=+16.0, drive=0.6),
    Amp("quiet", gain_db=-11.0),
]


def test_the_loop_converges_on_real_audio():
    """Every preset, every riff: measure, correct, land on target."""
    for source in ("chords", "chug", "lead"):
        for spec in PRESETS:
            amp = Amp(spec.name, spec.gain_db, spec.drive)
            dev = Device(amp)
            wire(dev, source)
            before = amp.measure(source)["lufs_integrated"]
            out = autolevel.level_current(dev, target=-18.0, tolerance=0.5, max_iterations=6)
            after = amp.measure(source)["lufs_integrated"]
            where = "%s/%s: %.2f -> %.2f LUFS in %d passes" % (
                spec.name, source, before, after, len(out["iterations"]))
            if out.get("limited_by"):
                # Held short, and it has to say which stop it hit — the ceiling
                # or the end of the knob. "Did not converge" on its own tells
                # nobody whether to try again or to change the preset.
                check("%s/%s held for a stated reason" % (spec.name, source),
                      out["limited_by"] in ("true_peak", "range"), out.get("limited_by"))
                if out["limited_by"] == "range":
                    check("%s/%s says how far short" % (spec.name, source),
                          out.get("short_by_db", 0) > 0, out.get("short_by_db"))
                    check("%s/%s stopped rather than rewriting the stop"
                          % (spec.name, source), len(out["iterations"]) <= 3,
                          len(out["iterations"]))
                continue
            check("converges " + where, out["converged"] is True, out)
            check("lands on target " + where, abs(after - (-18.0)) <= 0.6, where)


def test_a_preset_already_on_target_is_left_alone():
    for source in ("chords", "sweep"):
        amp = Amp("ok", 0.0)
        dev = Device(amp)
        wire(dev, source)
        # tune the gain so the riff lands on -18 with no help
        amp.gain_db = -18.0 - amp.measure(source)["lufs_integrated"]
        out = autolevel.level_current(dev, target=-18.0, tolerance=0.5)
        check("no write when already there (%s)" % source, out["written"] is False, out)
        check("and no fader moved (%s)" % source, dev.writes == [], dev.writes)


def test_routing_is_put_back_however_it_ends():
    amp = Amp("x", 4.0)
    dev = Device(amp, in_port=1, out_port=19)
    wire(dev)
    autolevel.level_current(dev, target=-18.0)
    check("input port restored", dev.chain.in_portid == 1, dev.chain.in_portid)
    check("output port restored", dev.chain.out_portid == 19, dev.chain.out_portid)

    def boom(**_kw):
        raise RuntimeError("interface went away")
    autolevel.measure = boom
    dev2 = Device(Amp("y", 4.0), in_port=1, out_port=19)
    check("a failed measurement still raises", _raises(RuntimeError, autolevel.level_current, dev2))
    check("and still restores the input", dev2.chain.in_portid == 1, dev2.chain.in_portid)
    check("and still restores the output", dev2.chain.out_portid == 19, dev2.chain.out_portid)


def test_the_true_peak_guard_holds_a_preset_that_would_clip():
    """Reaching the target is within the knob's range, but doing it would put
    the output over the ceiling. The loop backs off and says so."""
    amp = Amp("hot", gain_db=0.0, drive=0.0)
    dev = Device(amp)
    wire(dev, "chug")
    # aim a few dB above where the riff already sits, so the trim needed is
    # small and well inside -60..+12 — but the true peak has only 6 dB of room
    here = amp.measure("chug")["lufs_integrated"]
    out = autolevel.level_current(dev, target=here + 9.0, tolerance=0.3,
                                  max_iterations=6, true_peak_ceiling=-1.0)
    final = amp.measure("chug")
    check("the guard reports itself", out.get("limited_by") == "true_peak", out)
    check("and the output really is under the ceiling",
          final["true_peak_dbtp"] <= -1.0 + 0.4, final["true_peak_dbtp"])
    check("and it did not spend every pass finding out", len(out["iterations"]) <= 3,
          len(out["iterations"]))


def test_a_preset_beyond_the_knob_says_how_far_short_it_is():
    """+12 dB is all a Gain block has. A preset that needs more must be told to
    the user as a preset problem, not left looking like a failed measurement."""
    amp = Amp("far too quiet", gain_db=-30.0)
    dev = Device(amp)
    wire(dev, "chords")
    out = autolevel.level_current(dev, target=-18.0, tolerance=0.5, max_iterations=6)
    check("it stops at the stop", out.get("limited_by") == "range", out.get("limited_by"))
    check("it names the limit", out.get("knob_limit_db") == autolevel.GAIN_MAX_DB,
          out.get("knob_limit_db"))
    check("it says how much is missing", out.get("short_by_db", 0) >= 5.0,
          out.get("short_by_db"))
    check("it did not converge, and admits it", out["converged"] is False)
    check("and it gave up quickly", len(out["iterations"]) <= 3, len(out["iterations"]))


def test_a_report_ranks_presets_the_way_they_actually_differ():
    """What Measure all produces: every preset on one riff, and the corrections
    have to put them in the same order as their real loudness."""
    source = "chords"
    measured = []
    for spec in PRESETS:
        amp = Amp(spec.name, spec.gain_db, spec.drive)
        wire(Device(amp), source)
        r = amp.measure(source)
        measured.append((spec.name, r["lufs_integrated"], -18.0 - r["lufs_integrated"]))
    loud_order = [n for n, l, _c in sorted(measured, key=lambda t: -t[1])]
    corr_order = [n for n, _l, c in sorted(measured, key=lambda t: t[2])]
    check("the loudest needs the smallest lift", loud_order == corr_order,
          "%s vs %s" % (loud_order, corr_order))
    spread = max(l for _n, l, _c in measured) - min(l for _n, l, _c in measured)
    check("the presets really are far apart", spread > 6.0, "%.1f dB" % spread)


def test_scenes_are_levelled_one_by_one():
    amp = Amp("scened", 6.0)
    dev = Device(amp)
    wire(dev)
    out = autolevel.level_scenes(dev, scenes=[0, 1, 2], target=-18.0, dry_run=True)
    rows = out.get("scenes") or out.get("rows") or []
    check("every scene is reported", len(rows) == 3, out)
    check("a dry run writes nothing", dev.writes == [] and dev.scene_writes == [],
          (dev.writes, dev.scene_writes))


def test_measuring_never_sends_sound_to_the_room():
    """The tail lane is tapped to USB for the duration, so the XLRs are silent —
    the property that makes it safe to level a whole setlist at a desk."""
    amp = Amp("x", 3.0)
    dev = Device(amp, in_port=1, out_port=19)
    wire(dev)
    autolevel.level_current(dev, target=-18.0)
    taps = [(r, i, o) for r, i, o in dev.routing if o is not None]
    check("the output was tapped away from the jacks",
          any(o == autolevel.OUT_PORT_USB_5_6 for _r, _i, o in taps), dev.routing)
    check("and put back", dev.chain.out_portid == 19)


# ── the bench service, driven the way Patchbay drives it ─────────────────

def test_the_bench_offers_the_riffs_it_ships():
    names = {n for n, _w, _s in riffs.catalogue()}
    check("there is more than one", len(names) >= 4, names)
    for n in names:
        check("%s renders through the wav writer path" % n,
              isinstance(riffs.path_for(n), str) and riffs.path_for(n).endswith(".wav"))


def test_db_and_norm_round_trip_across_the_whole_fader():
    """Every trim the loop can write has to survive the taper both ways, or a
    correction of -6 dB lands somewhere else entirely."""
    lo, hi = bench_mod.db_range()
    worst = 0.0
    v = lo
    while v <= hi + 1e-9:
        worst = max(worst, abs(bench_mod.db_of(bench_mod.norm_of(v)) - v))
        v += 0.25
    check("dB -> norm -> dB is exact to 0.01", worst < 0.01, worst)
    check("0 dB is the documented norm", abs(bench_mod.norm_of(0.0) - 0.769230783) < 1e-6,
          bench_mod.norm_of(0.0))


def test_converging_never_leaves_the_output_clipping():
    """On target and clipping is not a success.

    The guard used to require that the loop still wanted MORE level, so a preset
    that reached its target exactly while sitting at +3.09 dBTP came back
    "converged": true. Every preset, every riff, every target within reach: the
    output must end under the ceiling or the run must say it was held.
    """
    for source in ("chords", "chug", "sweep"):
        for gain in (-6.0, 0.0, +6.0):
            amp = Amp("t", gain)
            dev = Device(amp)
            wire(dev, source)
            here = amp.measure(source)["lufs_integrated"]
            for aim in (here - 6.0, here, here + 6.0, here + 12.0):
                amp.trim_db = 0.0
                out = autolevel.level_current(dev, target=aim, tolerance=0.3,
                                              max_iterations=5, true_peak_ceiling=-1.0)
                tp = amp.measure(source)["true_peak_dbtp"]
                where = "%s gain%+.0f aim %.1f -> %.2f dBTP" % (source, gain, aim, tp)
                if tp > -1.0 + 0.4:
                    # Either the loop was held short of target to stay under the
                    # ceiling, or the preset was already over it before anything
                    # was written — and then it is left alone but reported. What
                    # must never happen is a clean "converged" over the ceiling.
                    held = out.get("limited_by") == "true_peak"
                    already = out.get("over_ceiling") is True
                    check("clipping is never passed off as fine " + where,
                          held or already, out)
                    if held:
                        check("held means not converged " + where,
                              out["converged"] is False, out.get("converged"))


def test_a_dry_run_measures_and_touches_nothing():
    """The report-only mode the interface leads with: numbers, no writes."""
    for source in ("chords", "lead"):
        amp = Amp("dry", 7.0)
        dev = Device(amp, in_port=1, out_port=19)
        wire(dev, source)
        before = amp.trim_db
        out = autolevel.level_current(dev, target=-18.0, dry_run=True)
        check("a dry run suggests a correction (%s)" % source,
              out.get("suggested_db") is not None, out)
        check("and reports the ceiling honestly (%s)" % source,
              (out.get("over_ceiling") is True)
              == (out["measurement"]["true_peak_dbtp"] > autolevel.TRUE_PEAK_CEILING_DBTP),
              (out.get("over_ceiling"), out["measurement"]["true_peak_dbtp"]))
        check("and writes nothing (%s)" % source, dev.writes == [], dev.writes)
        check("and adds no block (%s)" % source,
              all(m.hash != autolevel.GAIN_HASH for m in dev.chain.models))
        check("and moves no fader (%s)" % source, amp.trim_db == before)
        check("and still puts the routing back (%s)" % source,
              dev.chain.in_portid == 1 and dev.chain.out_portid == 19)
        # the suggestion has to be the one that would actually work
        amp.trim_db = out["suggested_db"]
        after = amp.measure(source)["lufs_integrated"]
        check("the suggested trim really lands on target (%s)" % source,
              abs(after - (-18.0)) < 0.6, "%.2f" % after)


def test_the_suggestion_is_what_apply_would_write():
    """What the report shows and what Apply does have to be the same number, or
    the table is describing a change nobody is going to make."""
    for spec in PRESETS:
        amp = Amp(spec.name, spec.gain_db, spec.drive)
        dev = Device(amp)
        wire(dev, "chords")
        dry = autolevel.level_current(dev, target=-18.0, dry_run=True)
        amp.trim_db = 0.0
        wet = autolevel.level_current(dev, target=-18.0, tolerance=0.5, max_iterations=1)
        first = wet["iterations"][-1].get("wrote_db")
        if first is None or wet.get("limited_by"):
            continue
        check("%s: the report and the first write agree" % spec.name,
              abs(first - dry["suggested_db"]) < 0.05,
              "%.2f vs %.2f" % (first, dry["suggested_db"]))


# ── applying a proposal, which is what the report's button does ──────────

class FakeBench:
    """Just enough Bench for the apply/undo ops: a lane with a dB, and a name."""

    def __init__(self, device, name="A Preset", db=0.0, row=0):
        self.qc = device
        self.name = name
        self.lanes = {row: float(db)}
        self.opened = []

    def open(self, folder_key, position, is_factory=False, cloud_id=""):
        self.opened.append((folder_key, position))
        return self.preset_state()

    def preset_state(self):
        return {"name": self.name,
                "lanes": [{"row": r, "db": v} for r, v in sorted(self.lanes.items())]}

    def set_db(self, row, db):
        self.lanes[int(row)] = float(db)
        self.qc.amp.trim_db = float(db)
        return True


def _ops(bench):
    events = []
    return bench_mod.measure_ops(bench, events.append), events


def test_apply_writes_the_number_the_report_is_showing():
    """Not a fresh decision — the proposal, possibly edited, exactly as shown."""
    dev = Device(Amp("p", 0.0))
    bench = FakeBench(dev, db=-3.0)
    ops, _ev = _ops(bench)
    if "apply_trim" not in ops:
        check("apply_trim is offered", False, sorted(ops))
        return
    out = ops["apply_trim"]({"row": 0, "db": 4.5})
    check("it reports where the fader was", out.get("from_db") == -3.0, out)
    check("the correction is relative", out.get("db") == 1.5, out)
    check("and it says what it actually gave", out.get("applied_db") == 4.5, out)
    check("the lane really moved", bench.lanes[0] == 1.5, bench.lanes)
    check("and the model hears it", dev.amp.trim_db == 1.5, dev.amp.trim_db)


def test_apply_is_undone_by_revert():
    dev = Device(Amp("p", 0.0))
    bench = FakeBench(dev, db=-2.0)
    ops, _ev = _ops(bench)
    ops["apply_trim"]({"row": 0, "db": 6.0})
    check("moved", bench.lanes[0] == 4.0, bench.lanes)
    back = ops["revert_levels"]({})
    check("revert puts it back", bench.lanes[0] == -2.0, bench.lanes)
    check("and says what it put back", back["reverted"] == [{"row": 0, "db": -2.0}], back)
    check("and nothing is left owing", back["remaining"] == 0, back)


def test_applying_twice_still_reverts_to_the_original():
    """The undo store keeps the FIRST value it saw, not the last one written —
    otherwise a second Apply makes the first one permanent by accident."""
    dev = Device(Amp("p", 0.0))
    bench = FakeBench(dev, db=0.0)
    ops, _ev = _ops(bench)
    ops["apply_trim"]({"row": 0, "db": 3.0})
    ops["apply_trim"]({"row": 0, "db": 2.0})
    check("the second is relative to the first", bench.lanes[0] == 5.0, bench.lanes)
    ops["revert_levels"]({})
    check("and revert goes all the way back", bench.lanes[0] == 0.0, bench.lanes)


def test_a_trim_the_fader_cannot_give_says_so():
    lo, hi = bench_mod.db_range()
    dev = Device(Amp("p", 0.0))
    bench = FakeBench(dev, db=hi - 1.0)
    ops, _ev = _ops(bench)
    out = ops["apply_trim"]({"row": 0, "db": 6.0})
    check("it lands on the stop", out["db"] == hi, out)
    check("it gave only what it could", out["applied_db"] == 1.0, out)
    check("it says it was short", out.get("limited_by") == "range", out)
    check("and by how much", out.get("short_by_db") == 5.0, out)


def test_applying_to_a_row_with_no_fader_is_an_error_not_a_lie():
    dev = Device(Amp("p", 0.0))
    bench = FakeBench(dev, db=0.0, row=0)
    ops, _ev = _ops(bench)
    out = ops["apply_trim"]({"row": 7, "db": 3.0})
    check("it refuses", "error" in out, out)
    check("and nothing moved", bench.lanes == {0: 0.0}, bench.lanes)


def test_apply_opens_the_preset_it_was_told_to():
    dev = Device(Amp("p", 0.0))
    bench = FakeBench(dev, db=0.0)
    ops, _ev = _ops(bench)
    ops["apply_trim"]({"folder_key": "/f", "position": 4, "row": 0, "db": 1.0})
    check("it opened the right slot", bench.opened == [("/f", 4)], bench.opened)


# ── the riffs, reachable ─────────────────────────────────────────────────

def test_the_bench_can_list_and_load_a_shipped_riff():
    """The gap the review opened with: five riffs in the package and no way in.
    A player without a guitar to hand could not start at all."""
    import shutil
    import tempfile
    from qc_mcp import audio_io, riffs as R

    dev = Device(Amp("p", 0.0))
    bench = FakeBench(dev)
    ops, _ev = _ops(bench)
    check("riffs is offered", "riffs" in ops, sorted(ops))
    check("use_riff is offered", "use_riff" in ops, sorted(ops))

    listed = ops["riffs"]({})
    names = [r["name"] for r in listed.get("riffs", [])]
    check("all five are listed", len(names) == 5, names)
    check("each carries a reason", all(len(r["why"]) > 30 for r in listed["riffs"]))
    check("each carries a length", all(r["seconds"] > 2 for r in listed["riffs"]))

    with tempfile.TemporaryDirectory() as d:
        real_default = audio_io.DEFAULT_SAMPLE_PATH
        real_dir = R.default_dir
        audio_io.DEFAULT_SAMPLE_PATH = os.path.join(d, "reference_di.wav")
        R.default_dir = lambda: os.path.join(d, "riffs")
        try:
            out = ops["use_riff"]({"name": "chords"})
            check("loading one reports it", out.get("loaded") == "chords", out)
            check("and it lands where measurements read from",
                  os.path.exists(audio_io.DEFAULT_SAMPLE_PATH))
            again = ops["riffs"]({})
            check("and the list now knows which", again.get("loaded") == "chords", again)
            check("and stops calling it a recording", again.get("recorded") is False, again)
            bad = ops["use_riff"]({"name": "nope"})
            check("an unknown riff is refused", "error" in bad, bad)
            check("and nothing was named for it", not shutil.os.path.exists(
                os.path.join(d, "riffs", "nope.wav")))
        finally:
            audio_io.DEFAULT_SAMPLE_PATH = real_default
            R.default_dir = real_dir


def test_a_loaded_riff_is_good_enough_to_measure_with():
    """Whatever the bench offers has to pass the bar the verdict shows the user:
    hotter than -12 dBFS peak and not mostly gaps."""
    for name, _w, _s in riffs.catalogue():
        a = riff(name)
        r = loudness.analyze(a, RATE)
        check("%s would pass the verdict" % name,
              r["sample_peak_dbfs"] >= -12.0 and r["lufs_integrated"] >= -30.0,
              (r["sample_peak_dbfs"], r["lufs_integrated"]))


def _raises(exc, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except exc:
        return True
    except Exception:
        return False
    return False


def main():
    real = autolevel.measure
    try:
        for name, fn in sorted(globals().items()):
            if not name.startswith("test_"):
                continue
            try:
                fn()
            except Exception as exc:                # noqa: BLE001
                global fail
                fail += 1
                import traceback
                print("FAIL: %s raised" % name)
                traceback.print_exc()
    finally:
        autolevel.measure = real
    print("%d passed, %d failed" % (ok, fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
