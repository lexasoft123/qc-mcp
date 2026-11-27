"""Offline checks of the pitch pedals known in semitones (catalog.PEDAL_SEMITONES):
the Gojira WOW's PEDAL per MODE, as measured on CorOS 4.1.1 (2026-11-27), read back
in get_current_preset, found by find_devices, written by set_semitones. No device."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from qc_mcp import catalog                 # noqa: E402
from qc_mcp import protocol as P           # noqa: E402
from qc_mcp import server                  # noqa: E402

WOW = 18009
BLADE2, BLADE1, FATSO = 0, 1, 2


def close(a, b, tol=1e-6):
    return abs(a - b) < tol


def test_the_spans():
    assert close(catalog.pedal_from_semitones(WOW, FATSO, 0), 50.0)
    assert close(catalog.pedal_from_semitones(WOW, FATSO, -12), 0.0)
    assert close(catalog.pedal_from_semitones(WOW, FATSO, 12), 100.0)
    assert close(catalog.pedal_from_semitones(WOW, BLADE1, 7), 700 / 12)       # a fifth at 58.33 %
    assert close(catalog.pedal_from_semitones(WOW, BLADE2, 12), 50.0)
    assert catalog.pedal_from_semitones(WOW, BLADE1, -1) is None              # Blade 1 only goes up
    assert catalog.pedal_from_semitones(WOW, FATSO, 13) is None
    assert catalog.pedal_from_semitones(1, FATSO, 0) is None                 # not a pitch pedal
    for step in (BLADE2, BLADE1, FATSO):
        for st in range(-12, 25):
            pct = catalog.pedal_from_semitones(WOW, step, st)
            if pct is not None:
                assert close(catalog.semitones_from_pedal(WOW, step, pct), st)


def test_the_wire():
    # PEDAL is linear 0..100 %, MODE a switch stored as its step over 0..2
    assert close(catalog.to_norm(WOW, 1, 700 / 12), 7 / 12)
    assert close(catalog.to_norm(WOW, 11, BLADE1), 0.5)
    assert close(catalog.to_display(WOW, 11, 1.0), FATSO)
    names = [p["name"] for p in catalog.lookup(WOW)["params"]]
    sp = catalog.semitone_pedal(WOW)
    assert (names[sp["pedal"]], names[sp["mode"]], names[sp["mix"]]) == ("PEDAL", "MODE", "FATSO MIX")


def preset(mode_norm=1.0, pedal_norm=0.5, mix_norm=0.5, pedal_scenes=None):
    """A preset with a Gojira WOW at row 0, column 1 (column 0 empty)."""
    bp = P.message_class("RecallPreset")().preset
    ch = bp.chains.add()
    ch.models.add()
    m = ch.models.add()
    m.hash = WOW
    for i, prm in enumerate(catalog.lookup(WOW)["params"]):
        p = m.params.add()
        v = {0: mix_norm, 1: pedal_norm, 11: mode_norm}.get(i, 0.0)
        for x in (pedal_scenes if i == 1 and pedal_scenes else [v]):
            p.param_values.add().float_value = x
    return bp


class FakeQC:
    def __init__(self, bp):
        self.bp, self.writes = bp, []

    def get_current_preset(self, *a, **k):
        return self.bp

    def set_param(self, row, column, index, value):
        self.writes.append((row, column, index, round(value, 6)))

    def set_param_scenes(self, row, column, index, values, **k):
        self.writes.append((row, column, index, [round(v, 6) for v in values]))


def with_fake(bp, fn):
    fake, old = FakeQC(bp), server._conn
    server._conn = lambda: fake          # noqa: E731  (test double)
    try:
        return fn(), fake.writes
    finally:
        server._conn = old


def test_read_back():
    m = preset(mode_norm=0.5, pedal_norm=7 / 12).chains[0].models[1]
    got = server._summary_params(m, catalog.lookup(WOW))["semitones"]
    assert got == {"mode": "Blade 1", "semitones": 7.0}
    m = preset(pedal_scenes=[0.5, 0.25, 0.75]).chains[0].models[1]       # FATSO, per scene
    got = server._summary_params(m, catalog.lookup(WOW))["semitones"]
    assert got["scene_semitones"] == [0.0, -6.0, 6.0]
    plain = preset().chains[0].models[1]
    plain.hash = 1                                                     # another block: nothing added
    assert "semitones" not in server._summary_params(plain, catalog.lookup(1))


def test_find_devices_says_so():
    rows = server.find_devices(query="Gojira WOW")
    wow = next(r for r in rows if r["hash"] == WOW)
    assert "set_semitones" in wow["semitones"] and "FATSO -12..+12" in wow["semitones"]


def test_set_semitones():
    # FATSO and a shift it holds: MODE stays, only PEDAL is written
    text, w = with_fake(preset(), lambda: server.set_semitones(0, 1, -2))
    assert w == [(0, 1, 1, round(0.5 - 2 / 24, 6))]
    assert "FATSO" in text and "FATSO MIX (param 0) is 50 %" in text
    # past FATSO: the first mode that reaches it, MODE written first
    text, w = with_fake(preset(), lambda: server.set_semitones(0, 1, 19))
    assert w == [(0, 1, 11, 0.0), (0, 1, 1, round(19 / 24, 6))]
    assert "Blade 2 (was FATSO)" in text
    # a mode asked for
    _, w = with_fake(preset(), lambda: server.set_semitones(0, 1, 7, mode="blade 1"))
    assert w == [(0, 1, 11, 0.5), (0, 1, 1, round(7 / 12, 6))]
    # per scene
    _, w = with_fake(preset(), lambda: server.set_semitones(0, 1, scenes=[0, -12, 12, 5]))
    assert w == [(0, 1, 1, [0.5, 0.0, 1.0, round(0.5 + 5 / 24, 6)])]
    # refused, nothing written
    for call in (lambda: server.set_semitones(0, 1, 30), lambda: server.set_semitones(0, 1, -3, mode="Blade 1"),
                 lambda: server.set_semitones(0, 0, 3), lambda: server.set_semitones(0, 1, 3, mode="Whammy")):
        text, w = with_fake(preset(), call)
        assert w == [], text


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
