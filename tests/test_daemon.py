"""Offline tests for the daemon's wire protocol — no device, no hardware.

A fake transport stands in for the HID backend, so these exercise exactly the
part that is ours: fan-out of device->host reports to every attached client,
disjoint request_id ranges, and the write path.
"""
import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from qc_mcp.daemon import Daemon, SocketTransport, endpoint_alive  # noqa: E402


class FakeIO:
    """Minimal stand-in for the HID transport."""

    def __init__(self):
        self.written = []
        self._queue = []
        self._lock = threading.Lock()

    def push(self, report_id, data):
        with self._lock:
            self._queue.append((report_id, bytes(data)))

    def read_reports(self, timeout=0.1):
        with self._lock:
            out, self._queue = self._queue, []
        if not out:
            time.sleep(min(timeout, 0.01))
        return out

    def set_report(self, report_id, data, include_id=False):
        self.written.append((report_id, bytes(data), include_id))

    def close(self):
        pass


class FakeQC:
    firmware = "4.1.0"
    device_type = "QC"

    def __init__(self):
        self.io = FakeIO()

    def close(self):
        self.io.close()


class DaemonTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "daemon.sock")
        self.qc = FakeQC()
        self.daemon = Daemon(self.qc, self.path, mode="direct")
        self.thread = threading.Thread(target=self.daemon.serve_forever, daemon=True)
        self.thread.start()
        for _ in range(100):
            if endpoint_alive(self.path):
                break
            time.sleep(0.02)
        else:
            self.fail("daemon never started listening")

    def tearDown(self):
        self.daemon.close()

    def test_endpoint_alive_is_not_just_a_file(self):
        self.assertTrue(endpoint_alive(self.path))
        self.daemon.close()
        self.assertFalse(endpoint_alive(self.path))

    def test_hello_hands_out_disjoint_request_id_ranges(self):
        a = SocketTransport(self.path).open()
        b = SocketTransport(self.path).open()
        try:
            self.assertEqual(a.firmware, "4.1.0")
            self.assertNotEqual(a.req_base, b.req_base)
            self.assertGreaterEqual(abs(a.req_base - b.req_base), 0x10000)
        finally:
            a.close()
            b.close()

    def test_writes_reach_the_device(self):
        io = SocketTransport(self.path).open()
        try:
            io.set_report(0, b"\x01\x02\x03", include_id=True)
            for _ in range(100):
                if self.qc.io.written:
                    break
                time.sleep(0.02)
            self.assertEqual(self.qc.io.written, [(0, b"\x01\x02\x03", True)])
        finally:
            io.close()

    def test_every_client_sees_every_report(self):
        a = SocketTransport(self.path).open()
        b = SocketTransport(self.path).open()
        try:
            self.qc.io.push(0, b"\xaa\xbb")
            got_a, got_b = [], []
            deadline = time.time() + 3
            while time.time() < deadline and not (got_a and got_b):
                got_a += a.read_reports(timeout=0.05)
                got_b += b.read_reports(timeout=0.05)
            self.assertEqual(got_a, [(0, b"\xaa\xbb")])
            self.assertEqual(got_b, [(0, b"\xaa\xbb")])
        finally:
            a.close()
            b.close()

    def test_status_counts_clients(self):
        io = SocketTransport(self.path).open()
        try:
            st = io.status()
            self.assertEqual(st["mode"], "direct")
            self.assertEqual(st["firmware"], "4.1.0")
            self.assertGreaterEqual(st["clients"], 1)
        finally:
            io.close()


if __name__ == "__main__":
    r = unittest.main(exit=False, verbosity=0).result
    total = r.testsRun
    print(f"{total - len(r.failures) - len(r.errors)}/{total} passed")
    sys.exit(1 if (r.failures or r.errors) else 0)
