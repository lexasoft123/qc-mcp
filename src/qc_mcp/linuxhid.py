"""Linux HID transport over hidraw — the Linux sibling of `iohid.IOHIDTransport`
and `winhid.WinHIDTransport`.

Same `open`/`set_report`/`read_reports`/`close` API, so `transport.QuadCortex`
neither knows nor cares which backend it got (`backend.open_hid()` picks).

How hidraw differs from the other two:
- **Numbered reports carry their id.** The protocol interface declares report 1
  (input, 128 bytes) and report 2 (output, 128 bytes), so a `read()` returns
  `[id][128 bytes]` and a `write()` must start with the id — exactly the
  `include_id=True` buffer `transport.send` already builds.
- **No exclusive open.** hidraw has no seize flag, and Cortex Control does not
  exist on Linux, so the only other holder can be a second qc-mcp. `seize=True`
  takes a non-blocking `flock` on the node so two of ours can't interleave
  frames; `lockfile.py` is the real guard against that.
- **Permissions.** The node is root-only until a udev rule hands it to the
  logged-in user (`docs/LINUX.md`); open() says so instead of a bare EACCES.
- **The kernel buffers input** (64 reports per open file), so a directory dump
  needs a reader that drains promptly — the same reason the macOS backend runs
  its own run loop and the Windows one raises its queue to 512.

Importable on any OS; only `open()` and `enumerate_devices()` need Linux.
"""
from __future__ import annotations
import errno
import fcntl
import os
import select
import threading
import time

from .backend import QC_VID, device_ids, not_found_error

SYSFS_HIDRAW = "/sys/class/hidraw"


def _uevent(node):
    """Parse /sys/class/hidraw/<node>/device/uevent into a dict."""
    out = {}
    try:
        with open(os.path.join(SYSFS_HIDRAW, node, "device", "uevent")) as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                out[k] = v
    except OSError:
        pass
    return out


def _ids(hid_id):
    """HID_ID=0003:0000152A:0000892F -> (0x152A, 0x892F)."""
    parts = hid_id.split(":")
    if len(parts) != 3:
        return None, None
    try:
        return int(parts[1], 16), int(parts[2], 16)
    except ValueError:
        return None, None


def _report_sizes(node):
    """(input_len, output_len) of a hidraw node, from its report descriptor."""
    try:
        with open(os.path.join(SYSFS_HIDRAW, node, "device",
                               "report_descriptor"), "rb") as f:
            return parse_report_sizes(f.read())
    except OSError:
        return 0, 0


def parse_report_sizes(desc):
    """(input_len, output_len) in bytes per report, id byte included. Only
    handles what a vendor collection like the QC's declares (Report ID / Report
    Size / Report Count / Input / Output); anything it can't read leaves the
    lengths at 0 and open() falls back on report_size+1."""
    size = count = 0
    has_id = False
    lens = {"in": 0, "out": 0}
    i = 0
    while i < len(desc):
        prefix = desc[i]
        n = (0, 1, 2, 4)[prefix & 0x03]
        val = int.from_bytes(desc[i + 1:i + 1 + n], "little")
        tag = prefix & 0xFC
        if tag == 0x84:                     # Report ID
            has_id = True
        elif tag == 0x74:                   # Report Size (bits)
            size = val
        elif tag == 0x94:                   # Report Count
            count = val
        elif tag == 0x80:                   # Input
            lens["in"] = max(lens["in"], size * count // 8)
        elif tag == 0x90:                   # Output
            lens["out"] = max(lens["out"], size * count // 8)
        i += 1 + n
    extra = 1 if has_id else 0
    return (lens["in"] + extra if lens["in"] else 0,
            lens["out"] + extra if lens["out"] else 0)


def enumerate_devices(vid=None, pid=None):
    """Matching hidraw nodes as dicts (path/vid/pid/report lengths/name).

    `pid` is one product id or an iterable of them (the model family); None
    means don't filter. Used by `open()`.
    """
    wanted = None if pid is None else ({pid} if isinstance(pid, int) else set(pid))
    try:
        nodes = sorted(os.listdir(SYSFS_HIDRAW))
    except OSError:
        return []
    out = []
    for node in nodes:
        ev = _uevent(node)
        v, p = _ids(ev.get("HID_ID", ""))
        if v is None:
            continue
        if vid is not None and v != vid:
            continue
        if wanted is not None and p not in wanted:
            continue
        in_len, out_len = _report_sizes(node)
        path = os.path.join("/dev", node)
        out.append({"path": path, "vid": v, "pid": p,
                    "input_len": in_len, "output_len": out_len,
                    "product": ev.get("HID_NAME", ""),
                    "phys": ev.get("HID_PHYS", ""),
                    "readable": os.access(path, os.R_OK | os.W_OK)})
    return out


def _permission_error(path):
    return (f"no permission to open {path} - hidraw nodes are root-only by "
            "default. Install the udev rule in docs/LINUX.md, then unplug and "
            "replug the Quad Cortex.")


class LinuxHIDTransport:
    """Drop-in for `iohid.IOHIDTransport` on Linux."""

    #: hidraw write() either sends the whole report or fails with an errno; no
    #: noise codes have been seen on this device.
    BENIGN_WRITE_CODES = frozenset({0})

    def __init__(self, vid=QC_VID, pid=None, report_size=128, seize=False):
        self.vid = vid
        #: product ids to accept - the whole family unless the caller pinned one
        self.pids = device_ids(pid)
        #: which one we actually opened; filled in by open()
        self.pid = None
        self.report_size = report_size
        self.seize = seize
        self.path = None
        self.exclusive = False
        self._fd = None
        self._in_len = report_size + 1
        self._out_len = report_size + 1
        self._rx_lock = threading.Lock()
        self._rx = []          # (report_id, data) captured by the reader thread
        self._thread = None
        self._stop = threading.Event()

    # -- lifecycle --
    def open(self):
        cands = enumerate_devices(self.vid, self.pids)
        if not cands:
            raise RuntimeError(not_found_error(self.vid, self.pids))
        # The protocol collection is the one with report_size+1 reports. Rank by
        # self.pids order so two attached models resolve the same way every run,
        # as iohid and winhid do.
        want = self.report_size + 1
        cands.sort(key=lambda c: self.pids.index(c["pid"]))
        chosen = next((c for c in cands if c["input_len"] == want), cands[0])
        self.path = chosen["path"]
        self.pid = chosen["pid"]
        self._in_len = chosen["input_len"] or want
        self._out_len = chosen["output_len"] or want

        try:
            fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        except PermissionError:
            raise RuntimeError(_permission_error(self.path)) from None
        except OSError as e:
            raise RuntimeError(f"open {self.path} failed: {e}") from None
        if self.seize:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                raise RuntimeError(
                    f"the Quad Cortex ({self.path}) is already held by another "
                    "qc-mcp process - stop it and reconnect") from None
            self.exclusive = True
        self._fd = fd

        self._stop.clear()
        # The reader holds its own copy of the fd: close() joins it before
        # os.close, so it can't read a descriptor the main thread just reused.
        self._thread = threading.Thread(target=self._reader, args=(fd,), daemon=True)
        self._thread.start()
        return self

    def _reader(self, fd):
        while not self._stop.is_set():
            try:
                ready, _, _ = select.select([fd], [], [], 0.2)
            except (OSError, ValueError):
                break
            if not ready:
                continue
            # Drain everything queued before going back to select: a directory
            # listing arrives as thousands of back-to-back reports.
            while True:
                try:
                    data = os.read(fd, self._in_len)
                except BlockingIOError:
                    break
                except OSError:
                    return                  # unplugged / fd closed
                if not data:
                    return
                with self._rx_lock:
                    self._rx.append((data[0], data))

    # -- io --
    def set_report(self, report_id, data, include_id=False):
        """data = the chunk bytes [chunkLen][flags][payload] (report_size long).

        Returns 0 on success, else the errno — like IOKit's IOReturn, and like
        it, nothing upstream treats a non-zero as fatal.
        """
        if self._fd is None:
            raise RuntimeError("write on a closed device")
        # hidraw always wants [id][payload]: `data` never carries the id (the
        # callers pass it separately, as IOKit's SetReport takes it), so
        # `include_id` changes nothing here. Pad or trim to the declared length.
        buf = bytes([report_id]) + bytes(data)
        buf = buf[:self._out_len].ljust(self._out_len, b"\x00")
        deadline = time.monotonic() + 2.0
        while True:
            try:
                os.write(self._fd, buf)
                return 0
            except BlockingIOError:
                if time.monotonic() > deadline:
                    return errno.ETIMEDOUT
                select.select([], [self._fd], [], 0.05)
            except OSError as e:
                return e.errno or errno.EIO

    def read_reports(self, timeout=0.1):
        """Return (and clear) reports captured by the reader thread. `timeout`
        lets callers wait a little for more data to arrive."""
        if timeout:
            time.sleep(timeout)
        with self._rx_lock:
            out = self._rx[:]
            self._rx.clear()
        return out

    def close(self):
        self._stop.set()
        fd, self._fd = self._fd, None
        if self._thread:
            self._thread.join(timeout=1.5)
            self._thread = None
        if fd is not None:
            try:
                os.close(fd)                # releases the flock too
            except OSError:
                pass
