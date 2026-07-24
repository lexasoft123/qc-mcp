"""FIFO bridge transport: share Cortex Control's live HID session instead of
seizing the device, so the app and the MCP can run at the same time.

Requires the instrumented Cortex Control (interceptor/) to be running — its
injected dylib exposes two FIFOs:
  QC_INJECT (/tmp/qc_inject) : we write 129-byte HID reports -> sent to the QC
  QC_OUT    (/tmp/qc_in)     : every device->host report, [uint16 LE len][report]

Drop-in for iohid.IOHIDTransport (same open/close/set_report/read_reports API), so
transport.QuadCortex can use it. No handshake/heartbeat needed here — Cortex Control
already maintains the session.

Reliability (so the MCP can start/stop repeatedly and survive Cortex Control
restarts across multiple sessions):
- The out FIFO is opened O_RDWR: we hold a writer end ourselves so the reader
  NEVER sees EOF when Cortex Control's forwarder momentarily closes or restarts.
  A plain O_RDONLY reader gets a sticky EOF and silently stops delivering data —
  that was the "read-back worked, then stopped" bug.
- The reader thread self-heals: any transient select/read error reopens the FIFO
  instead of killing the thread. It only exits on close().
- read_reports() restarts the reader thread if it ever died.
- set_report() reopens the inject FIFO and retries if the write end went stale
  (e.g. Cortex Control was relaunched).
"""
from __future__ import annotations
import fcntl
import os
import select
import threading
import time

INJECT_PATH = os.environ.get("QC_INJECT", "/tmp/qc_inject")
OUT_PATH = os.environ.get("QC_OUT", "/tmp/qc_in")

_RX_CAP = 20000          # keep the last N device->host reports if the consumer stalls


class BridgeError(Exception):
    pass


class FifoBridge:
    def __init__(self, inject_path=INJECT_PATH, out_path=OUT_PATH):
        self.inject_path = inject_path
        self.out_path = out_path
        self._inject_fd = -1
        self._out_fd = -1
        self._rx = []                      # list of (report_id, data)
        self._rx_lock = threading.Lock()
        self._fd_lock = threading.Lock()   # guards (re)opening fds
        self._stop = None
        self._thread = None

    # -- fd (re)open helpers ------------------------------------------------
    def _open_out(self):
        """(Re)open the device->host FIFO for reading. O_RDWR so we also hold a
        writer end: the read side then never hits EOF when Cortex Control's
        forwarder closes/reopens, so it keeps delivering across app restarts."""
        with self._fd_lock:
            if self._out_fd >= 0:
                try:
                    os.close(self._out_fd)
                except OSError:
                    pass
                self._out_fd = -1
            try:
                self._out_fd = os.open(self.out_path, os.O_RDWR | os.O_NONBLOCK)
                return True
            except OSError:
                self._out_fd = -1
                return False

    def _open_inject(self):
        """(Re)open the host->device inject FIFO for writing. Returns False if the
        interposer isn't reading yet (ENXIO) — caller retries."""
        with self._fd_lock:
            if self._inject_fd >= 0:
                try:
                    os.close(self._inject_fd)
                except OSError:
                    pass
                self._inject_fd = -1
            try:
                fd = os.open(self.inject_path, os.O_WRONLY | os.O_NONBLOCK)
            except OSError:
                self._inject_fd = -1
                return False
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)      # clear non-block for reliable writes
            fcntl.fcntl(fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)
            self._inject_fd = fd
            return True

    # -- lifecycle ----------------------------------------------------------
    def open(self, connect_timeout=5.0):
        if not (os.path.exists(self.inject_path) and os.path.exists(self.out_path)):
            raise BridgeError(
                "bridge FIFOs not found — launch the instrumented Cortex Control "
                "first (interceptor/run-bridge.sh).")
        # inject (writer): retry until the interposer is reading (ENXIO = no reader yet).
        deadline = time.time() + connect_timeout
        while time.time() < deadline and not self._open_inject():
            time.sleep(0.1)
        if self._inject_fd < 0:
            raise BridgeError("could not open inject FIFO (is Cortex Control running?)")
        # out (reader): O_RDWR never blocks and always succeeds if the path exists.
        if not self._open_out():
            raise BridgeError("could not open out FIFO (is Cortex Control running?)")
        self._start_reader()
        return self

    def _start_reader(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def _ensure_alive(self):
        """Restart the reader thread / reopen the out fd if either died."""
        if self._stop is None or self._stop.is_set():
            return
        if self._out_fd < 0:
            self._open_out()
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._reader, daemon=True)
            self._thread.start()

    def _reader(self):
        buf = b""
        while self._stop is not None and not self._stop.is_set():
            fd = self._out_fd
            if fd < 0:
                if not self._open_out():
                    self._stop.wait(0.1)
                    continue
                buf = b""
                fd = self._out_fd
            try:
                r, _, _ = select.select([fd], [], [], 0.2)
            except (OSError, ValueError):
                self._open_out()          # fd went bad — reopen, don't die
                buf = b""
                continue
            if not r:
                continue
            try:
                chunk = os.read(fd, 65536)
            except BlockingIOError:
                continue                  # O_RDWR: no data right now
            except OSError:
                self._open_out()
                buf = b""
                continue
            if not chunk:                 # shouldn't happen with O_RDWR, but be safe
                self._stop.wait(0.02)
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

    def set_report(self, report_id, data, include_id=False):
        buf = bytes(data)
        if include_id:
            buf = bytes([report_id]) + buf
        if len(buf) < 129:
            buf = buf + b"\x00" * (129 - len(buf))
        buf = buf[:129]
        for attempt in (0, 1):            # reopen + retry once if the pipe went stale
            if self._inject_fd < 0 and not self._reopen_inject_briefly():
                continue
            try:
                os.write(self._inject_fd, buf)
                return 0
            except OSError as e:
                if attempt == 0:
                    self._reopen_inject_briefly()
                    continue
                raise BridgeError(f"inject write failed ({e}); Cortex Control gone?")
        raise BridgeError("inject FIFO unavailable; is Cortex Control running?")

    def _reopen_inject_briefly(self, timeout=1.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._open_inject():
                return True
            time.sleep(0.05)
        return False

    def read_reports(self, timeout=0.1):
        self._ensure_alive()              # revive a dead reader before waiting
        if timeout:
            time.sleep(timeout)
        with self._rx_lock:
            out = self._rx[:]
            self._rx.clear()
        return out

    def reopen(self):
        """Force a fresh reader connection (both fds + reader thread). Cheap health
        recovery the transport can call without a full close/open."""
        self._open_out()
        self._reopen_inject_briefly()
        self._ensure_alive()

    def close(self):
        if self._stop:
            self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.5)   # let the reader exit its select first
            self._thread = None
        with self._fd_lock:
            for fd in (self._inject_fd, self._out_fd):
                if fd >= 0:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            self._inject_fd = self._out_fd = -1
