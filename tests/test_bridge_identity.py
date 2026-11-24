"""Version reads must never rekey Cortex Control's active cloud session."""
import os
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
from qc_mcp import protocol as P
from qc_mcp.backend import BridgeError
from qc_mcp.daemon import SocketTransport
from qc_mcp.transport import QuadCortex, QCError


class IdentityTransport:
    def open(self): return self
    def close(self): self.closed = True

    def cached_version(self, timeout=2.5):
        return P.message_class('Version')(zenos_git_hash='4.1.1',
            comms_version=b'test-session-material').SerializeToString()

    def set_report(self, *args, **kwargs):
        raise AssertionError('identity lookup must not send a device request')


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.previous = P.active_version()
        P.set_version('4.1')

    def tearDown(self):
        P.set_version(self.previous)

    def test_attached_identity_is_local_and_daemon_metadata_is_sanitized(self):
        qc = QuadCortex(io=IdentityTransport())
        self.assertEqual(qc.read_state('Version').zenos_git_hash, '4.1.1')
        self.assertEqual(qc.read_state(10).zenos_git_hash, '4.1.1')
        qc.detect_version()
        self.assertTrue(qc.protocol_version_verified)
        self.assertEqual(qc.firmware, '4.1.1')
        self.assertFalse(P.message_class('Version').FromString(qc._version_info).comms_version)
        for command in ('Version', P.NAME_TO_CMD['Version']):
            with self.assertRaisesRegex(QCError, 'owned by the bridge'):
                qc.send(command)

    def test_native_bridge_reads_local_identity_and_missing_cache_fails_closed(self):
        with patch('qc_mcp.transport.open_bridge', return_value=IdentityTransport()):
            qc = QuadCortex(bridge=True)
        self.assertEqual(qc.detect_version(), '4.1')
        self.assertEqual(qc.read_state('Version').zenos_git_hash, '4.1.1')
        with patch.object(qc.io, 'cached_version', side_effect=BridgeError('cache missing')), \
             patch('qc_mcp.transport.time.sleep'):
            with self.assertRaisesRegex(QCError, 'could not read session owner'):
                qc.detect_version()
        self.assertFalse(qc.protocol_version_verified)
        self.assertIsNone(qc.firmware)

    def test_failed_cached_identity_open_releases_its_reader(self):
        for failure in (BridgeError('cache missing'), b'\x08\x03'):
            io = IdentityTransport()
            with patch('qc_mcp.transport.open_bridge', return_value=io):
                qc = QuadCortex(bridge=True)
            options = {'side_effect': failure} if isinstance(failure, Exception) else {'return_value': failure}
            with patch.object(io, 'cached_version', **options), patch('qc_mcp.transport.time.sleep'):
                with self.assertRaises(QCError): qc.open()
            self.assertTrue(io.closed)

    def test_build_hash_cannot_select_a_firmware_generation(self):
        io = IdentityTransport()
        qc = QuadCortex(io=io)
        version = P.message_class("Version")(app_fw_version="d14e")
        with patch.object(io, "cached_version", return_value=version.SerializeToString()):
            with self.assertRaisesRegex(QCError, "valid CorOS version"):
                qc.detect_version()
        self.assertFalse(qc.protocol_version_verified)

    def test_attached_legacy_daemon_uses_hello_firmware_without_wire_read(self):
        io = SocketTransport('/unused')
        io.firmware = '4.1.1'
        self.assertEqual(P.message_class('Version').FromString(io.cached_version()).zenos_git_hash,
                         '4.1.1')
        io.firmware = None
        with self.assertRaisesRegex(BridgeError, 'identity unavailable'):
            io.cached_version()

    @unittest.skipUnless(sys.platform == 'darwin', 'POSIX bridge is macOS-only')
    def test_private_live_cache_and_invalid_cache_fail_closed(self):
        from qc_mcp.bridge import FifoBridge
        with tempfile.TemporaryDirectory() as directory:
            out = os.path.join(directory, 'out')
            io = FifoBridge(out_path=out)
            cache = pathlib.Path(out + '.identity')
            payload = P.message_class('Version')(zenos_git_hash='4.1.1').SerializeToString()
            cache.write_bytes(f'QC_IDENTITY_V1 {os.getpid()}\n'.encode() + payload)
            cache.chmod(0o600)
            self.assertEqual(io.cached_version(timeout=0), payload)
            with patch('qc_mcp.bridge.os.kill', side_effect=ProcessLookupError):
                with self.assertRaisesRegex(BridgeError, 'identity unavailable'):
                    io.cached_version(timeout=0)
            cache.chmod(0o644)
            with self.assertRaises(BridgeError):
                io.cached_version(timeout=0)
            cache.chmod(0o600)
            for bad in (b'garbage', b'QC_IDENTITY_V1 0\nabc',
                        f'QC_IDENTITY_V1 {os.getpid()}\n'.encode()):
                cache.write_bytes(bad)
                with self.assertRaises(BridgeError):
                    io.cached_version(timeout=0)
            cache.unlink()
            with self.assertRaises(BridgeError):
                io.cached_version(timeout=0)

    @unittest.skipUnless(sys.platform == 'darwin', 'POSIX bridge is macOS-only')
    def test_wire_version_is_rejected_and_grid_still_forwards(self):
        from qc_mcp.bridge import FifoBridge
        io = FifoBridge()
        io._inject_fd = 42
        version = P.message_class('Version')(action=P.ACTION['READ'])
        packet = P.message_to_reports(P.encode_message('Version', version.SerializeToString()))[0]
        with patch('qc_mcp.bridge.os.write') as write:
            with self.assertRaisesRegex(BridgeError, 'rekey'):
                io.set_report(2, packet[1:], include_id=True)
            write.assert_not_called()
            packet = P.message_to_reports(P.encode_message('Grid', b'\x08\x01'))[0]
            self.assertEqual(io.set_report(2, packet[1:], include_id=True), 0)
            write.assert_called_once_with(42, packet)


if __name__ == '__main__':
    unittest.main()
