"""Offline tests for the bench's input-trim ops (qc_mcp.leveling.read/write_input_level).

The QC's IN 1 LEVEL is a -12..+60 dB preamp trim stored normalized; these pin
the dB round trip, the clamp, the port -> param mapping and the two wire traps
(send only the level field; drain the echo before verifying)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from qc_mcp import catalog, leveling  # noqa: E402


class _Port:
    def __init__(self, pid, level):
        self.input_port_id = pid
        self.level = level


class _IO:
    def __init__(self, ports):
        self.settings = self
        self.in_port = ports

    def HasField(self, name):
        return name == "settings"


class FakeQC:
    """Holds two input ports; a write is echoed once before it is real."""

    def __init__(self, levels=(0.166667, 0.513889)):
        self.levels = {1: levels[0], 2: levels[1]}
        self.writes = []
        self.reads = 0
        self._echo = None

    def set_io_port(self, kind, port, **fields):
        self.writes.append((kind, port, fields))
        # the device answers the next READ with the write itself
        self._echo = _IO([_Port(port, fields["level"])])
        self.levels[port] = fields["level"]

    def read_message(self, command):
        assert command == "IOSettings"
        self.reads += 1
        if self._echo is not None:
            e, self._echo = self._echo, None
            return e
        return _IO([_Port(p, v) for p, v in sorted(self.levels.items())])


class TestInputTrim(unittest.TestCase):
    def test_reads_the_measured_calibration(self):
        got = leveling.read_input_level(FakeQC(), 1)
        self.assertAlmostEqual(got["db"], 0.0, places=1)     # untouched default = 0 dB
        self.assertEqual((got["min_db"], got["max_db"]), (-12.0, 60.0))
        self.assertAlmostEqual(leveling.read_input_level(FakeQC(), 2)["db"], 25.0, places=1)

    def test_round_trip_lands_where_asked(self):
        for db in (-12.0, -3.5, 0.0, 6.0, 25.0, 60.0):
            qc = FakeQC()
            got = leveling.write_input_level(qc, db, port=1)
            self.assertAlmostEqual(got["db"], db, places=1)
            self.assertFalse(got["clamped"])

    def test_sends_only_the_level_field(self):
        qc = FakeQC()
        leveling.write_input_level(qc, 6.0)
        self.assertEqual(len(qc.writes), 1)
        kind, port, fields = qc.writes[0]
        self.assertEqual((kind, port), ("in", 1))
        self.assertEqual(set(fields), {"level"}, "a full port record is silently rejected")
        self.assertAlmostEqual(fields["level"], catalog.io_level_from_db(0, 6.0))

    def test_drains_the_echo_before_verifying(self):
        # The fake echoes the write once: a single read after the write would
        # see the echo and call it verified. Two reads = drain + verify.
        qc = FakeQC()
        leveling.write_input_level(qc, 6.0)
        self.assertEqual(qc.reads, 2)

    def test_clamps_to_the_range_and_says_so(self):
        got = leveling.write_input_level(FakeQC(), 99.0)
        self.assertAlmostEqual(got["db"], 60.0, places=1)
        self.assertTrue(got["clamped"])
        got = leveling.write_input_level(FakeQC(), -40.0)
        self.assertAlmostEqual(got["db"], -12.0, places=1)
        self.assertTrue(got["clamped"])

    def test_only_the_calibrated_ports(self):
        with self.assertRaises(ValueError):
            leveling.read_input_level(FakeQC(), 3)
        with self.assertRaises(ValueError):
            leveling.write_input_level(FakeQC(), 0.0, port=7)

    def test_ops_speak_the_stdio_shape(self):
        class B:
            qc = FakeQC()
        ops = leveling.io_ops(B())
        self.assertEqual(ops["input_level"]({"port": 2})["port"], 2)
        r = ops["set_input_level"]({"db": 3.0})
        self.assertEqual(r["port"], 1)
        self.assertAlmostEqual(r["db"], 3.0, places=1)


if __name__ == "__main__":
    unittest.main()
