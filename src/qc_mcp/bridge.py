"""FIFO bridge transport: share Cortex Control's live HID session instead of
seizing the device, so the app and the MCP can run at the same time.

Requires the instrumented Cortex Control (interceptor/) to be running — its
injected dylib exposes two FIFOs:
  QC_INJECT (/tmp/qc_inject) : we write 129-byte HID reports -> sent to the QC
  QC_OUT    (/tmp/qc_in)     : every device->host report, [uint16 LE len][report]

Drop-in for iohid.IOHIDTransport (same open/close/set_report/read_reports API), so
transport.QuadCortex can use it. No handshake/heartbeat needed here — Cortex Control
already maintains the session.
"""
from __future__ import annotations
import os
import select
import threading
import time

INJECT_PATH = os.environ.get("QC_INJECT", "/tmp/qc_inject")
OUT_PATH = os.environ.get("QC_OUT", "/tmp/qc_in")


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
        self._stop = None
        self._thread = None

    def open(self, connect_timeout=5.0):
        if not (os.path.exists(self.inject_path) and os.path.exists(self.out_path)):
            raise BridgeError(
                "bridge FIFOs not found — launch the instrumented Cortex Control "
                "first (interceptor/run-bridge.sh).")
        # inject: writer side. Non-blocking retry until the interposer is reading
        # (ENXIO = no reader yet), then switch to blocking writes.
        deadline = time.time() + connect_timeout
        while time.time() < deadline:
            try:
                self._inject_fd = os.open(self.inject_path, os.O_WRONLY | os.O_NONBLOCK)
                break
            except OSError:
                time.sleep(0.1)
        if self._inject_fd < 0:
            raise BridgeError("could not open inject FIFO (is Cortex Control running?)")
        fl = os.O_WRONLY  # clear non-block for reliable writes
        import fcntl
        flags = fcntl.fcntl(self._inject_fd, fcntl.F_GETFL)
        fcntl.fcntl(self._inject_fd, fcntl.F_SETFL, flags & ~os.O_NONBLOCK)

        # out: reader side. Non-blocking; a reader open makes the interposer's
        # non-blocking writer open succeed on its next retry.
        self._out_fd = os.open(self.out_path, os.O_RDONLY | os.O_NONBLOCK)

        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()
        return self

    def _reader(self):
        buf = b""
        while not self._stop.is_set():
            fd = self._out_fd
            if fd < 0:
                break
            try:
                r, _, _ = select.select([fd], [], [], 0.2)
            except (OSError, ValueError):
                break            # fd closed on shutdown
            if not r:
                continue
            try:
                chunk = os.read(fd, 8192)
            except OSError:
                continue
            if not chunk:      # no writer right now (app not forwarding) — transient
                time.sleep(0.05)
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

    def set_report(self, report_id, data, include_id=False):
        buf = bytes(data)
        if include_id:
            buf = bytes([report_id]) + buf
        if len(buf) < 129:
            buf = buf + b"\x00" * (129 - len(buf))
        try:
            os.write(self._inject_fd, buf[:129])
            return 0
        except OSError as e:
            raise BridgeError(f"inject write failed ({e}); Cortex Control gone?")

    def read_reports(self, timeout=0.1):
        if timeout:
            time.sleep(timeout)
        with self._rx_lock:
            out = self._rx[:]
            self._rx.clear()
        return out

    def close(self):
        if self._stop:
            self._stop.set()
        if self._thread:
            self._thread.join(timeout=0.5)   # let the reader exit its select first
            self._thread = None
        for fd in (self._inject_fd, self._out_fd):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self._inject_fd = self._out_fd = -1
