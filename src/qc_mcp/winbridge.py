r"""Windows bridge: share Cortex Control's live device session over named pipes.

The Win32 sibling of `bridge.FifoBridge`, same four-method backend API, same wire
format — only the transport differs, because Windows has no FIFOs:

    macOS    /tmp/qc_inject   /tmp/qc_in        (mkfifo, interposer writes)
    Windows  \\.\pipe\qc_inject  \\.\pipe\qc_out   (CreateNamedPipe, DLL writes)

Wire format is identical to the macOS interposer, so the protocol layer above is
untouched:

    qc_out     device -> host, a byte stream of [uint16 LE len][report bytes]
    qc_inject  host -> device, raw 129-byte HID reports, one after another

The instrumented Cortex Control owns both pipes as the **server** end (mirroring
how the interposer creates the FIFOs); this class is always the client. That way
the app can run with nothing attached, and we can attach and detach freely.

Status: this is NOT what Windows bridge mode uses. The DLL in `interceptor-win/`
captures traffic fine but cannot inject (see its header), and it turned out not
to be needed: Windows delivers every HID input report to every open handle, so
the MCP simply opens its own shared handle next to the running app
(`QuadCortex(share=True)`). This class is kept, tested against a fake pipe
server, for the day a CorOS release starts opening the device exclusively —
then the interposer's inject path becomes the only route and this is its client.
"""
from __future__ import annotations
import ctypes
import os
import threading
import time

from . import winhid
from .backend import BridgeError

INJECT_PIPE = os.environ.get("QC_INJECT_PIPE", r"\\.\pipe\qc_inject")
OUT_PIPE = os.environ.get("QC_OUT_PIPE", r"\\.\pipe\qc_out")

_RX_CAP = 20000          # keep the last N device->host reports if the consumer stalls
_FRAME = 129             # report id + 128 data


class WinBridge:
    BENIGN_WRITE_CODES = frozenset({0})   # set_report raises on a failed write

    def __init__(self, inject_path=INJECT_PIPE, out_path=OUT_PIPE):
        self.inject_path = inject_path
        self.out_path = out_path
        self._inject = None                # binary file object, unbuffered
        self._out = None
        self._rx = []                      # list of (report_id, data)
        self._rx_lock = threading.Lock()
        self._io_lock = threading.Lock()   # guards (re)opening the pipe handles
        self._stop = None
        self._thread = None

    # -- pipe (re)open helpers ---------------------------------------------
    def _open_pipe(self, path, mode):
        """Connect to one pipe as a client. Returns None if the server end isn't
        there — the caller retries, exactly like waiting on a FIFO's writer."""
        try:
            return open(path, mode, buffering=0)
        except OSError:
            return None                    # not created yet, or all instances busy

    def _close_out(self):
        """Cancel any in-flight read, then drop the handle. Must hold _io_lock."""
        if self._out is not None:
            winhid._k32.CancelIoEx(self._out, None)
            winhid._k32.CloseHandle(self._out)
            self._out = None

    def _open_out(self):
        """The out pipe is opened OVERLAPPED on purpose. A blocking pipe read
        cannot be interrupted by closing the handle from another thread — the
        close itself then blocks behind the pending read, which wedged close()
        indefinitely. Overlapped + CancelIoEx is the same shape winhid.py uses.
        """
        winhid._load()
        with self._io_lock:
            self._close_out()
            h = winhid._k32.CreateFileW(
                self.out_path, winhid.GENERIC_READ, 0, None,
                winhid.OPEN_EXISTING, winhid.FILE_FLAG_OVERLAPPED, None)
            if not h or h == winhid.INVALID_HANDLE_VALUE:
                self._out = None
                return False
            self._out = h
            return True

    def _open_inject(self):
        with self._io_lock:
            if self._inject is not None:
                try:
                    self._inject.close()
                except OSError:
                    pass
                self._inject = None
            self._inject = self._open_pipe(self.inject_path, "wb")
            return self._inject is not None

    # -- lifecycle ----------------------------------------------------------
    def open(self, connect_timeout=5.0):
        deadline = time.time() + connect_timeout
        while time.time() < deadline and not self._open_inject():
            time.sleep(0.1)
        if self._inject is None:
            raise BridgeError(
                "bridge pipes not found - launch the instrumented Cortex Control "
                "first. (Windows bridge mode needs the injector DLL; see "
                "docs/WINDOWS-INTERPOSER.md.)")
        if not self._open_out():
            raise BridgeError("could not open the out pipe (is Cortex Control running?)")
        self._start_reader()
        return self

    def _start_reader(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def _ensure_alive(self):
        """Restart the reader / reconnect the out pipe if either died."""
        if self._stop is None or self._stop.is_set():
            return
        if self._out is None:
            self._open_out()
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._reader, daemon=True)
            self._thread.start()

    def _reader(self):
        """Overlapped reads in a thread, polling the stop event so close() is
        honoured promptly. A read of 0 bytes / a broken pipe means the server end
        went away (the app quit or restarted) — reconnect rather than die, so a
        bridge survives an app restart the way the FIFO one does."""
        k32 = winhid._k32
        rbuf = ctypes.create_string_buffer(65536)
        ovl = winhid.OVERLAPPED()
        ovl.hEvent = k32.CreateEventW(None, 1, 0, None)     # manual-reset
        buf = b""
        try:
            while self._stop is not None and not self._stop.is_set():
                out = self._out
                if out is None:
                    if not self._open_out():
                        self._stop.wait(0.1)
                    buf = b""
                    continue
                nread = winhid.DWORD(0)
                ok = k32.ReadFile(out, rbuf, 65536, ctypes.byref(nread),
                                  ctypes.byref(ovl))
                if not ok:
                    if ctypes.get_last_error() != winhid.ERROR_IO_PENDING:
                        self._open_out()             # pipe broken - reconnect
                        buf = b""
                        self._stop.wait(0.05)
                        continue
                    if not self._await(out, ovl, nread):
                        if self._stop.is_set():
                            break
                        self._open_out()
                        buf = b""
                        continue
                chunk = rbuf.raw[:nread.value]
                if not chunk:                        # server end closed
                    self._open_out()
                    buf = b""
                    self._stop.wait(0.05)
                    continue
                buf += chunk
                # parse [uint16 LE len][report] frames
                while len(buf) >= 2:
                    ln = buf[0] | (buf[1] << 8)
                    if len(buf) < 2 + ln:
                        break
                    report = buf[2:2 + ln]
                    buf = buf[2 + ln:]
                    report_id = report[0] if report else 0
                    with self._rx_lock:
                        self._rx.append((report_id, report))
                        if len(self._rx) > _RX_CAP:
                            del self._rx[:len(self._rx) - _RX_CAP]
        finally:
            if ovl.hEvent:
                k32.CloseHandle(ovl.hEvent)

    def _await(self, h, ovl, nread):
        """Wait out one pending read, polling so close() is honoured promptly."""
        k32 = winhid._k32
        while not self._stop.is_set():
            w = k32.WaitForSingleObject(ovl.hEvent, 200)
            if w == winhid.WAIT_OBJECT_0:
                return bool(k32.GetOverlappedResult(h, ctypes.byref(ovl),
                                                    ctypes.byref(nread), 0))
            if w != winhid.WAIT_TIMEOUT:
                return False
        k32.CancelIoEx(h, ctypes.byref(ovl))
        return False

    # -- io -----------------------------------------------------------------
    def set_report(self, report_id, data, include_id=False):
        buf = bytes(data)
        if include_id:
            buf = bytes([report_id]) + buf
        buf = buf[:_FRAME].ljust(_FRAME, b"\x00")
        for attempt in (0, 1):             # reconnect + retry once if it went stale
            if self._inject is None and not self._reopen_inject_briefly():
                continue
            try:
                self._inject.write(buf)
                return 0
            except OSError as e:
                if attempt == 0:
                    self._reopen_inject_briefly()
                    continue
                raise BridgeError(f"inject write failed ({e}); Cortex Control gone?")
        raise BridgeError("inject pipe unavailable; is Cortex Control running?")

    def _reopen_inject_briefly(self, timeout=1.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._open_inject():
                return True
            time.sleep(0.05)
        return False

    def read_reports(self, timeout=0.1):
        self._ensure_alive()               # revive a dead reader before waiting
        if timeout:
            time.sleep(timeout)
        with self._rx_lock:
            out = self._rx[:]
            self._rx.clear()
        return out

    def reopen(self):
        """Force a fresh connection (both pipes + reader thread)."""
        self._open_out()
        self._reopen_inject_briefly()
        self._ensure_alive()

    def close(self):
        if self._stop:
            self._stop.set()
        with self._io_lock:
            # CancelIoEx first: closing a handle does NOT unblock a read already
            # pending on it, and the close would then block behind that read.
            self._close_out()
            if self._inject is not None:
                try:
                    self._inject.close()
                except OSError:
                    pass
                self._inject = None
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
