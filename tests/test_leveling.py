"""Offline unit tests for qc_mcp.leveling — the dB calibration the preset-leveling
bench is built on, and how it reads a lane's output block. Fakes stand in for the
decoded protobuf, so this needs no device."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from qc_mcp import leveling  # noqa: E402


class _Val:
    def __init__(self, v):
        self.float_value = v


class _Param:
    def __init__(self, v, scenes=8):
        # one entry per scene; a list of values makes a per-scene param
        self.param_values = [_Val(x) for x in v] if isinstance(v, list) \
            else [_Val(v) for _ in range(scenes)]


class _Block:
    def __init__(self, *values):
        self.hash = leveling.LANE_OUTPUT_HASH
        self.params = [_Param(v) for v in values]


class _Model:
    def __init__(self, h):
        self.hash = h


class _Chain:
    """A grid row as a whole-preset READ delivers it: `row` is 0 and the array
    position carries the index."""

    def __init__(self, out_portid, block, models=()):
        self.row = 0
        self.out_portid = out_portid
        self.output_control = [block] if block else []
        self.models = [_Model(h) for h in models]


class TestCalibration(unittest.TestCase):
    """0.5 reads -14.0 dB in Cortex Control and the untouched default
    0.769230783 is 0 dB; those two points are what pin -40..+12."""

    def test_anchor_points(self):
        self.assertAlmostEqual(leveling.db_of(0.5), -14.0, places=4)
        self.assertAlmostEqual(leveling.db_of(0.769230783), 0.0, places=4)
        self.assertAlmostEqual(leveling.db_of(0.0), -40.0, places=4)
        self.assertAlmostEqual(leveling.db_of(1.0), 12.0, places=4)

    def test_range_is_what_the_bench_reports(self):
        self.assertEqual(leveling.db_range(), (-40.0, 12.0))

    def test_round_trip(self):
        for db in (-40.0, -14.0, -3.5, 0.0, 6.25, 12.0):
            self.assertAlmostEqual(leveling.db_of(leveling.norm_of(db)), db, places=4)

    def test_unity_is_not_the_middle_of_the_dial(self):
        # the trap a symmetric guess falls into: 0 dB is 10/13 up, not halfway
        self.assertAlmostEqual(leveling.norm_of(0.0), 10 / 13, places=6)


def _lane_db(block, scene):
    return leveling._lane(0, _Chain(19, block), scene)["db"]


class TestLane(unittest.TestCase):
    def test_reads_volume_pan_and_switches(self):
        # VOLUME 0.5, PAN 0.35, MUTE off, SOLO off
        lane = leveling._lane(2, _Chain(19, _Block(0.5, 0.35, 0.0, 0.0), models=(1, 0, 2)))
        self.assertEqual(lane["row"], 2)          # position, not chain.row
        self.assertEqual(lane["db"], -14.0)
        self.assertEqual(lane["pan"], -15)        # the app shows this as "15 L"
        self.assertFalse(lane["mute"])
        self.assertEqual(lane["out"], "Multi Out")
        self.assertEqual(lane["blocks"], 2)       # hash 0 is an empty slot

    def test_merge_bus_is_active_but_not_physical(self):
        """A mix bus feeds another row, so the master knob must skip it."""
        lane = leveling._lane(1, _Chain(16, _Block(0.769230783, 0.5, 0.0, 0.0)))
        self.assertTrue(lane["active"])
        self.assertFalse(lane["physical"])
        self.assertEqual(lane["db"], 0.0)

    def test_unrouted_lane_is_neither(self):
        lane = leveling._lane(3, _Chain(0, _Block(0.5, 0.5, 0.0, 0.0)))
        self.assertFalse(lane["active"])
        self.assertFalse(lane["physical"])

    def test_mute_and_solo_flags(self):
        lane = leveling._lane(0, _Chain(19, _Block(0.5, 0.5, 1.0, 1.0)))
        self.assertTrue(lane["mute"])
        self.assertTrue(lane["solo"])

    def test_reads_the_active_scene_not_always_scene_a(self):
        """Output level can be scened; showing scene A's value while the device
        is on scene C would report a level the rig is not playing at."""
        vols = [0.5] * 8
        vols[2] = 0.769230783                     # scene C sits at 0 dB
        block = _Block(vols, 0.5, 0.0, 0.0)
        self.assertEqual(_lane_db(block, scene=0), -14.0)
        self.assertEqual(_lane_db(block, scene=2), 0.0)

    def test_scene_beyond_the_stored_values_falls_back(self):
        block = _Block(0.5, 0.5, 0.0, 0.0)
        self.assertEqual(_lane_db(block, scene=99), -14.0)

    def test_row_without_an_output_block_is_skipped(self):
        self.assertIsNone(leveling._lane(0, _Chain(19, None)))


class _Frame:
    """A decoded RecallPreset broadcast as the transport buffers it."""

    def __init__(self, name):
        self.preset = type("BP", (), {"name": name, "chains": [], "scene_labels": []})()

    def HasField(self, f):
        return f == "preset"


class _RecallQC:
    """A device that already holds a stale broadcast (from a save) when the
    recall is asked for, then answers the recall with the real one."""

    def __init__(self, stale, real, pointer_lag=1):
        self._pending = [(P_RECALL, _Frame(stale), b"", b"x")]
        self.real = real
        self.recalled = []
        self.pointer = None
        self.lag = pointer_lag          # collects before the pointer moves
        self.collects = 0

    def recall(self, folder_key="", position=0, is_factory=False, downloads_key=""):
        self.recalled.append((folder_key, position))
        self.target = position

    def _collect(self, _t):
        self.collects += 1
        if self.recalled and self.collects == self.lag + 1:
            self._pending.append((P_RECALL, _Frame(self.real), b"", b"y"))
            self.pointer = self.target

    def get_setlist_position(self):
        return {"folder_key": "f", "position": self.pointer, "is_factory": False}

    def read_state(self, *_a, **_k):
        return None

    def get_current_preset(self):
        return _Frame(self.real).preset


P_RECALL = leveling.P.NAME_TO_CMD["RecallPreset"]


class TestOpenTakesItsOwnBroadcast(unittest.TestCase):
    """A save leaves a RecallPreset payload in the buffer; open() must not take
    it for its own recall's answer. On hardware that shifted every later open by
    one preset for the rest of the session."""

    def test_stale_broadcast_is_drained_before_the_recall(self):
        qc = _RecallQC(stale="Saved One", real="Wanted")
        st = leveling.Bench(qc).open("f", 7, settle=1.0)
        self.assertEqual(qc.recalled, [("f", 7)])
        self.assertEqual(st["name"], "Wanted")
        self.assertEqual(st["position"], 7)

    def test_a_payload_before_the_pointer_moved_is_not_believed(self):
        # the drain happens, but a second stale frame arrives AFTER the recall
        # was sent and BEFORE the pointer moved: the pointer check rejects it
        qc = _RecallQC(stale="Saved One", real="Wanted", pointer_lag=3)
        orig = qc._collect

        def collect(t):
            orig(t)
            if qc.collects == 1:
                qc._pending.append((P_RECALL, _Frame("Late Echo"), b"", b"z"))
        qc._collect = collect
        st = leveling.Bench(qc).open("f", 7, settle=2.0)
        self.assertEqual(st["name"], "Wanted")


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    total = r.testsRun
    print(f"{total - len(r.failures) - len(r.errors)}/{total} passed")
    sys.exit(1 if (r.failures or r.errors) else 0)
