r"""Windows bridge (named pipes) against a fake interposer. No device, no app.

The DLL that hooks Cortex Control doesn't exist yet, so this stands a fake server
on the same two pipes and checks the half that does: framing, reassembly across
chunk boundaries, inject padding, and self-healing when the app end goes away.

On non-Windows only the API-parity check runs (named pipes are Windows-only).
"""
import ctypes
import os
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from qc_mcp import backend as B             # noqa: E402
from qc_mcp.winbridge import WinBridge      # noqa: E402

OUT_PIPE = r"\\.\pipe\qc_test_out"
INJECT_PIPE = r"\\.\pipe\qc_test_inject"

PIPE_ACCESS_INBOUND, PIPE_ACCESS_OUTBOUND = 1, 2
PIPE_TYPE_BYTE = PIPE_READMODE_BYTE = PIPE_WAIT = 0
NMPWAIT_USE_DEFAULT_WAIT = 0


def _frame(report):
    """The interposer's device->host framing: [uint16 LE len][report]."""
    return struct.pack("<H", len(report)) + report


class FakeInterposer:
    """The pipe-server half the injected DLL will play."""

    def __init__(self):
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k32.CreateNamedPipeW.restype = ctypes.c_void_p
        self.k32.CreateNamedPipeW.argtypes = [ctypes.c_wchar_p] + [ctypes.c_uint32] * 6 + [ctypes.c_void_p]
        self.k32.ConnectNamedPipe.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.k32.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                                       ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        self.k32.ReadFile.argtypes = self.k32.WriteFile.argtypes
        self.k32.CloseHandle.argtypes = [ctypes.c_void_p]
        self.k32.CancelIoEx.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.k32.DisconnectNamedPipe.argtypes = [ctypes.c_void_p]
        self.received = []
        self._stop = threading.Event()
        self.out_h = self._make(OUT_PIPE, PIPE_ACCESS_OUTBOUND)
        self.inject_h = self._make(INJECT_PIPE, PIPE_ACCESS_INBOUND)
        self._t = threading.Thread(target=self._serve_inject, daemon=True)
        self._t.start()
        self._c = threading.Thread(
            target=lambda: self.k32.ConnectNamedPipe(self.out_h, None), daemon=True)
        self._c.start()

    def _make(self, name, access):
        h = self.k32.CreateNamedPipeW(name, access,
                                      PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
                                      255, 65536, 65536, NMPWAIT_USE_DEFAULT_WAIT, None)
        if not h or h == ctypes.c_void_p(-1).value:
            raise OSError(f"CreateNamedPipe {name} failed ({ctypes.get_last_error()})")
        return h

    def _serve_inject(self):
        self.k32.ConnectNamedPipe(self.inject_h, None)
        buf = ctypes.create_string_buffer(4096)
        while not self._stop.is_set():
            n = ctypes.c_uint32(0)
            if not self.k32.ReadFile(self.inject_h, buf, 4096, ctypes.byref(n), None):
                break
            if n.value:
                self.received.append(buf.raw[:n.value])

    def push(self, data):
        """Write raw bytes down the out pipe (caller does its own framing)."""
        n = ctypes.c_uint32(0)
        self.k32.WriteFile(self.out_h, data, len(data), ctypes.byref(n), None)
        return n.value

    def close(self):
        # Same trap the bridge itself hit: CloseHandle does NOT unblock the
        # ReadFile already pending on the inject pipe, and then blocks behind it.
        self._stop.set()
        for h in (self.out_h, self.inject_h):
            self.k32.CancelIoEx(h, None)
            self.k32.DisconnectNamedPipe(h)
            self.k32.CloseHandle(h)
        self._t.join(timeout=1.0)


def test_api_matches_the_fifo_bridge():
    """Nothing above the backend may need to know which bridge it holds.
    Only checkable where the FIFO bridge imports, i.e. on POSIX — note
    bridge_supported() is True on Windows too now, so it can't be the gate."""
    if sys.platform == "win32":
        return
    import inspect
    from qc_mcp.bridge import FifoBridge
    for m in ("open", "set_report", "read_reports", "close", "reopen"):
        a = inspect.signature(getattr(WinBridge, m))
        b = inspect.signature(getattr(FifoBridge, m))
        assert str(a) == str(b), f"{m}: WinBridge{a} vs FifoBridge{b}"


def test_frames_reassemble_across_chunk_boundaries():
    if sys.platform != "win32":
        return
    srv = FakeInterposer()
    br = WinBridge(inject_path=INJECT_PIPE, out_path=OUT_PIPE).open()
    try:
        r1 = bytes([1, 5, 0xC0]) + b"hello".ljust(126, b"\x00")
        r2 = bytes([1, 3, 0xC0]) + b"abc".ljust(126, b"\x00")
        blob = _frame(r1) + _frame(r2)
        # split mid-frame so the reader must buffer a partial frame
        srv.push(blob[:70]); time.sleep(0.15); srv.push(blob[70:])
        got = []
        for _ in range(20):
            got += br.read_reports(0.1)
            if len(got) >= 2:
                break
        assert len(got) == 2, f"expected 2 reports, got {len(got)}"
        assert got[0] == (1, r1) and got[1] == (1, r2)
    finally:
        br.close(); srv.close()


def test_inject_writes_exactly_one_129_byte_frame():
    if sys.platform != "win32":
        return
    srv = FakeInterposer()
    br = WinBridge(inject_path=INJECT_PIPE, out_path=OUT_PIPE).open()
    try:
        br.set_report(0x02, bytes([5, 0xC0]) + b"hello", include_id=True)
        for _ in range(20):
            if srv.received:
                break
            time.sleep(0.1)
        assert srv.received, "interposer received nothing"
        sent = b"".join(srv.received)
        assert len(sent) == 129, f"inject frame must be 129 bytes, got {len(sent)}"
        assert sent[:8] == bytes([0x02, 5, 0xC0]) + b"hello"
        assert set(sent[8:]) == {0}, "tail must be zero-padded"
    finally:
        br.close(); srv.close()


def test_reader_survives_the_app_going_away():
    if sys.platform != "win32":
        return
    srv = FakeInterposer()
    br = WinBridge(inject_path=INJECT_PIPE, out_path=OUT_PIPE).open()
    try:
        rpt = bytes([1, 1, 0xC0]) + b"x".ljust(126, b"\x00")
        srv.push(_frame(rpt))
        time.sleep(0.3)
        assert br.read_reports(0.1), "no report before the restart"
        srv.close()                       # Cortex Control quits
        time.sleep(0.3)
        srv2 = FakeInterposer()           # ...and comes back
        try:
            # Retry the reconnect, don't just wait on it. Both servers use the
            # same pipe NAME, and a named pipe allows many instances of one
            # name: while srv's handles are still winding down, reopen() can
            # land on the dead instance instead of srv2's and then sit there
            # forever. Reconnecting each round converges once the old instance
            # is gone. That collision is an artifact of two fake servers in one
            # process — Cortex Control is a single writer.
            got = []
            for _ in range(20):
                br.reopen()
                srv2.push(_frame(rpt))
                got += br.read_reports(0.2)
                if got:
                    break
            assert got, "reader did not recover after the app restarted"
        finally:
            srv2.close()
    finally:
        br.close()


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    ok = 0
    for fn in fns:
        try:
            fn(); ok += 1
            note = "" if sys.platform == "win32" or fn.__name__.endswith("fifo_bridge") else " (skipped: not Windows)"
            print(f"PASS {fn.__name__}{note}")
        except Exception:
            print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{ok}/{len(fns)} passed")
    sys.exit(0 if ok == len(fns) else 1)
