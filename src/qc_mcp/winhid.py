"""Windows HID transport (ctypes over setupapi/hid/kernel32) — the Win32 sibling
of `iohid.IOHIDTransport`.

Same `open`/`set_report`/`read_reports`/`close` API, so `transport.QuadCortex`
neither knows nor cares which backend it got (`hid.open_hid()` picks).

Things that differ from IOKit and bite if forgotten:
- **Windows pads** every input report out to `InputReportByteLength`, so a read
  always returns 129 bytes even for a 5-byte frame. Harmless: the frame carries
  its own `[chunkLen]` and `P.Reassembler` slices to it.
- **The driver's input queue is 32 reports per open handle** and silently drops
  the overflow. A directory listing streams thousands of reports back-to-back,
  which overruns it — `HidD_SetNumInputBuffers` raises it to 512. This is the
  Windows analogue of the macOS dedicated-CFRunLoop reader thread.
- **Every I/O is overlapped** (the handle is opened `FILE_FLAG_OVERLAPPED`), so
  reads can be cancelled at close instead of wedging a daemon thread on a device
  that never speaks again.
- **"seize" = `dwShareMode` 0.** If Cortex Control holds the device the open
  fails with ERROR_ACCESS_DENIED — exactly the case `connect()` reports back as
  "quit the app or use bridge mode".

Importable on any OS (the structs use plain ctypes types); only `open()` and
`enumerate_devices()` need to be on Windows.
"""
from __future__ import annotations
import ctypes
import re
import sys
import threading
import time

# Plain ctypes aliases rather than ctypes.wintypes: wintypes only imports on
# Windows, and keeping this module importable everywhere lets the offline tests
# check that both backends expose the same API.
DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16
USHORT = ctypes.c_uint16
ULONG = ctypes.c_uint32
BOOL = ctypes.c_int
BYTE = ctypes.c_ubyte
HANDLE = ctypes.c_void_p
LPCWSTR = ctypes.c_wchar_p

INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000

DIGCF_PRESENT = 0x02
DIGCF_DEVICEINTERFACE = 0x10

ERROR_ACCESS_DENIED = 5
ERROR_SHARING_VIOLATION = 32
ERROR_IO_PENDING = 997
ERROR_OPERATION_ABORTED = 995   # what a read cancelled by close() comes back as
ERROR_NO_MORE_ITEMS = 259

WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102

HIDP_STATUS_SUCCESS = 0x00110000


class GUID(ctypes.Structure):
    _fields_ = [("Data1", ULONG), ("Data2", WORD), ("Data3", WORD),
                ("Data4", BYTE * 8)]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Size", ULONG), ("VendorID", USHORT),
                ("ProductID", USHORT), ("VersionNumber", USHORT)]


class HIDP_CAPS(ctypes.Structure):
    """64 bytes; we only read the report lengths but the driver fills it all."""
    _fields_ = [("Usage", USHORT), ("UsagePage", USHORT),
                ("InputReportByteLength", USHORT),
                ("OutputReportByteLength", USHORT),
                ("FeatureReportByteLength", USHORT),
                ("Reserved", USHORT * 17),
                ("NumberLinkCollectionNodes", USHORT),
                ("NumberInputButtonCaps", USHORT),
                ("NumberInputValueCaps", USHORT),
                ("NumberInputDataIndices", USHORT),
                ("NumberOutputButtonCaps", USHORT),
                ("NumberOutputValueCaps", USHORT),
                ("NumberOutputDataIndices", USHORT),
                ("NumberFeatureButtonCaps", USHORT),
                ("NumberFeatureValueCaps", USHORT),
                ("NumberFeatureDataIndices", USHORT)]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", DWORD), ("Reserved", ctypes.POINTER(ULONG))]


class SP_DEVICE_INTERFACE_DETAIL_DATA_W(ctypes.Structure):
    # cbSize is the size of the *C* declaration ({DWORD; WCHAR[1]}), not of this
    # over-allocated Python view: 8 on 64-bit, 6 on 32-bit. SetupAPI validates it
    # literally and fails with ERROR_INVALID_USER_BUFFER if it disagrees.
    _fields_ = [("cbSize", DWORD), ("DevicePath", ctypes.c_wchar * 512)]


class OVERLAPPED(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                ("Offset", DWORD), ("OffsetHigh", DWORD), ("hEvent", HANDLE)]


_DETAIL_CBSIZE = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6

_k32 = _setupapi = _hid = None


def _load():
    """Bind the Win32 DLLs (deferred so this module imports on any OS)."""
    global _k32, _setupapi, _hid
    if _k32 is not None:
        return
    if sys.platform != "win32":
        raise RuntimeError("winhid is Windows-only "
                           f"(running on {sys.platform}); use qc_mcp.iohid on macOS")
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    _hid = ctypes.WinDLL("hid", use_last_error=True)

    _k32.CreateFileW.restype = HANDLE
    _k32.CreateFileW.argtypes = [LPCWSTR, DWORD, DWORD, ctypes.c_void_p,
                                 DWORD, DWORD, HANDLE]
    _k32.CreateEventW.restype = HANDLE
    _k32.CreateEventW.argtypes = [ctypes.c_void_p, BOOL, BOOL, LPCWSTR]
    _k32.ReadFile.restype = BOOL
    _k32.ReadFile.argtypes = [HANDLE, ctypes.c_void_p, DWORD,
                              ctypes.POINTER(DWORD), ctypes.c_void_p]
    _k32.WriteFile.restype = BOOL
    _k32.WriteFile.argtypes = [HANDLE, ctypes.c_void_p, DWORD,
                               ctypes.POINTER(DWORD), ctypes.c_void_p]
    _k32.GetOverlappedResult.restype = BOOL
    _k32.GetOverlappedResult.argtypes = [HANDLE, ctypes.c_void_p,
                                         ctypes.POINTER(DWORD), BOOL]
    _k32.WaitForSingleObject.restype = DWORD
    _k32.WaitForSingleObject.argtypes = [HANDLE, DWORD]
    _k32.CancelIoEx.restype = BOOL
    _k32.CancelIoEx.argtypes = [HANDLE, ctypes.c_void_p]
    _k32.CloseHandle.restype = BOOL
    _k32.CloseHandle.argtypes = [HANDLE]

    _setupapi.SetupDiGetClassDevsW.restype = HANDLE
    _setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), LPCWSTR,
                                               HANDLE, DWORD]
    _setupapi.SetupDiEnumDeviceInterfaces.restype = BOOL
    _setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
        HANDLE, ctypes.c_void_p, ctypes.POINTER(GUID), DWORD,
        ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
    _setupapi.SetupDiGetDeviceInterfaceDetailW.restype = BOOL
    _setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
        HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
        ctypes.POINTER(SP_DEVICE_INTERFACE_DETAIL_DATA_W), DWORD,
        ctypes.POINTER(DWORD), ctypes.c_void_p]
    _setupapi.SetupDiDestroyDeviceInfoList.restype = BOOL
    _setupapi.SetupDiDestroyDeviceInfoList.argtypes = [HANDLE]

    _hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(GUID)]
    _hid.HidD_GetAttributes.restype = BOOL
    _hid.HidD_GetAttributes.argtypes = [HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
    _hid.HidD_GetPreparsedData.restype = BOOL
    _hid.HidD_GetPreparsedData.argtypes = [HANDLE, ctypes.POINTER(ctypes.c_void_p)]
    _hid.HidD_FreePreparsedData.restype = BOOL
    _hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
    _hid.HidP_GetCaps.restype = ULONG
    _hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
    _hid.HidD_SetNumInputBuffers.restype = BOOL
    _hid.HidD_SetNumInputBuffers.argtypes = [HANDLE, ULONG]
    _hid.HidD_GetProductString.restype = BOOL
    _hid.HidD_GetProductString.argtypes = [HANDLE, ctypes.c_void_p, ULONG]
    _hid.HidD_GetSerialNumberString.restype = BOOL
    _hid.HidD_GetSerialNumberString.argtypes = [HANDLE, ctypes.c_void_p, ULONG]


def _err():
    return ctypes.get_last_error()


def _interface_paths():
    """Every present HID interface path, via SetupAPI."""
    _load()
    guid = GUID()
    _hid.HidD_GetHidGuid(ctypes.byref(guid))
    info = _setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
    if info == INVALID_HANDLE_VALUE:
        raise OSError(f"SetupDiGetClassDevs failed ({_err()})")
    paths = []
    try:
        idx = 0
        while True:
            iface = SP_DEVICE_INTERFACE_DATA()
            iface.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            if not _setupapi.SetupDiEnumDeviceInterfaces(
                    info, None, ctypes.byref(guid), idx, ctypes.byref(iface)):
                break                      # ERROR_NO_MORE_ITEMS: done
            idx += 1
            detail = SP_DEVICE_INTERFACE_DETAIL_DATA_W()
            detail.cbSize = _DETAIL_CBSIZE
            need = DWORD(0)
            if _setupapi.SetupDiGetDeviceInterfaceDetailW(
                    info, ctypes.byref(iface), ctypes.byref(detail),
                    ctypes.sizeof(detail), ctypes.byref(need), None):
                paths.append(detail.DevicePath)
    finally:
        _setupapi.SetupDiDestroyDeviceInfoList(info)
    return paths


#: Windows encodes the ids in the interface path: \\?\hid#vid_152a&pid_880a&mi_05#...
_PATH_IDS = re.compile(r"vid_([0-9a-f]{4})&pid_([0-9a-f]{4})", re.IGNORECASE)


def _ids_from_path(path):
    m = _PATH_IDS.search(path)
    return (int(m.group(1), 16), int(m.group(2), 16)) if m else (None, None)


def _string_prop(handle, fn):
    buf = ctypes.create_unicode_buffer(256)
    if fn(handle, ctypes.byref(buf), ctypes.sizeof(buf)):
        return buf.value
    return ""


def _probe(path):
    """Open a path read-only-shared just to read its ids and report lengths."""
    h = _k32.CreateFileW(path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                         OPEN_EXISTING, 0, None)
    if not h or h == INVALID_HANDLE_VALUE:
        return None
    try:
        attrs = HIDD_ATTRIBUTES()
        attrs.Size = ctypes.sizeof(attrs)
        if not _hid.HidD_GetAttributes(h, ctypes.byref(attrs)):
            return None
        pre = ctypes.c_void_p()
        caps = HIDP_CAPS()
        if _hid.HidD_GetPreparsedData(h, ctypes.byref(pre)):
            try:
                if _hid.HidP_GetCaps(pre, ctypes.byref(caps)) != HIDP_STATUS_SUCCESS:
                    caps = HIDP_CAPS()
            finally:
                _hid.HidD_FreePreparsedData(pre)
        return {"path": path, "vid": attrs.VendorID, "pid": attrs.ProductID,
                "version": attrs.VersionNumber,
                "input_len": caps.InputReportByteLength,
                "output_len": caps.OutputReportByteLength,
                "usage_page": caps.UsagePage, "usage": caps.Usage,
                "product": _string_prop(h, _hid.HidD_GetProductString),
                "serial": _string_prop(h, _hid.HidD_GetSerialNumberString)}
    finally:
        _k32.CloseHandle(h)


def enumerate_devices(vid=None, pid=None):
    """Matching HID interfaces as dicts (path/vid/pid/report lengths/strings).

    A Quad Cortex publishes more than one collection; the caller picks by report
    length. Used by `open()` and by `tools/win_hid_check.py`.
    """
    _load()
    out = []
    for path in _interface_paths():
        info = _probe(path)
        if info:
            info["busy"] = False
        else:
            # A device held exclusively by another process (Cortex Control) refuses
            # even a zero-access open, so probing it tells us nothing. Dropping it
            # here is how "quit the app" used to get misreported as "not plugged
            # in" — instead, recover the ids Windows puts in the path and mark it
            # busy, so open() reaches CreateFile and returns the honest error.
            pv, pp = _ids_from_path(path)
            if pv is None:
                continue                    # not a real HID collection
            info = {"path": path, "vid": pv, "pid": pp, "version": 0,
                    "input_len": 0, "output_len": 0, "usage_page": 0, "usage": 0,
                    "product": "", "serial": "", "busy": True}
        if vid is not None and info["vid"] != vid:
            continue
        if pid is not None and info["pid"] != pid:
            continue
        out.append(info)
    return out


class WinHIDTransport:
    """Drop-in for `iohid.IOHIDTransport` on Windows."""

    def __init__(self, vid=0x152A, pid=0x880A, report_size=128, seize=False):
        self.vid, self.pid = vid, pid
        self.report_size = report_size
        self.seize = seize
        self.path = None
        self.exclusive = False
        self._h = None
        self._in_len = report_size + 1
        self._out_len = report_size + 1
        self._rx_lock = threading.Lock()
        self._rx = []          # (report_id, data) captured by the reader thread
        self._thread = None
        self._stop = threading.Event()

    # -- lifecycle --
    def open(self):
        _load()
        cands = enumerate_devices(self.vid, self.pid)
        if not cands:
            raise RuntimeError(
                f"no HID device {self.vid:#06x}:{self.pid:#06x} found - is the "
                "Quad Cortex plugged in over USB and powered on?")
        # The QC exposes several collections; the protocol one is the collection
        # whose reports are report_size+1 long. Fall back to the first match so a
        # device that reports odd caps — or one we couldn't probe because it's
        # busy (input_len 0) — still gets a chance to produce a real error.
        want = self.report_size + 1
        chosen = next((c for c in cands if c["input_len"] == want), cands[0])
        self.path = chosen["path"]
        self._in_len = chosen["input_len"] or want
        self._out_len = chosen["output_len"] or want

        share = 0 if self.seize else (FILE_SHARE_READ | FILE_SHARE_WRITE)
        h = _k32.CreateFileW(self.path, GENERIC_READ | GENERIC_WRITE, share,
                             None, OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
        if not h or h == INVALID_HANDLE_VALUE:
            code = _err()
            if code in (ERROR_ACCESS_DENIED, ERROR_SHARING_VIOLATION) and self.seize:
                raise RuntimeError(
                    "the Quad Cortex is already open exclusively - quit Cortex "
                    "Control (or anything else holding it) and reconnect "
                    f"[CreateFile error {code}]")
            raise RuntimeError(f"CreateFile on {self.path} failed ({code})")
        self._h = h
        self.exclusive = share == 0
        # 32 is the default and the catalog dump blows straight through it.
        _hid.HidD_SetNumInputBuffers(self._h, 512)

        self._stop.clear()
        # The reader holds its own copy of the handle: close() joins it before
        # CloseHandle, so it can't read a handle the main thread just nulled.
        self._thread = threading.Thread(target=self._reader, args=(h,), daemon=True)
        self._thread.start()
        return self

    def _reader(self, h):
        buf = ctypes.create_string_buffer(self._in_len)
        ovl = OVERLAPPED()
        ovl.hEvent = _k32.CreateEventW(None, 1, 0, None)   # manual-reset
        if not ovl.hEvent:
            return
        try:
            while not self._stop.is_set():
                nread = DWORD(0)
                ok = _k32.ReadFile(h, buf, self._in_len,
                                   ctypes.byref(nread), ctypes.byref(ovl))
                if not ok:
                    code = _err()
                    if code != ERROR_IO_PENDING:
                        break                       # unplugged / handle closed
                    if not self._await(h, ovl, nread):
                        break
                n = nread.value
                if n:
                    data = buf.raw[:n]
                    with self._rx_lock:
                        self._rx.append((data[0], data))
        finally:
            _k32.CancelIoEx(h, ctypes.byref(ovl))
            _k32.CloseHandle(ovl.hEvent)

    def _await(self, h, ovl, nread):
        """Wait out one pending read, polling so close() is honoured promptly."""
        while not self._stop.is_set():
            w = _k32.WaitForSingleObject(ovl.hEvent, 200)
            if w == WAIT_OBJECT_0:
                return bool(_k32.GetOverlappedResult(
                    h, ctypes.byref(ovl), ctypes.byref(nread), 0))
            if w != WAIT_TIMEOUT:
                return False
        _k32.CancelIoEx(h, ctypes.byref(ovl))
        return False

    # -- io --
    def set_report(self, report_id, data, include_id=False):
        """data = the chunk bytes [chunkLen][flags][payload] (report_size long).

        Returns 0 on success, else the Win32 error — like IOKit's IOReturn, and
        like it, nothing upstream treats a non-zero as fatal.
        """
        if self._h is None:
            raise RuntimeError("write on a closed device")
        buf = bytes(data)
        if include_id:
            buf = bytes([report_id]) + buf
        # Windows demands exactly OutputReportByteLength bytes, id included.
        buf = buf[:self._out_len].ljust(self._out_len, b"\x00")
        ovl = OVERLAPPED()
        ovl.hEvent = _k32.CreateEventW(None, 1, 0, None)
        try:
            written = DWORD(0)
            ok = _k32.WriteFile(self._h, buf, len(buf),
                                ctypes.byref(written), ctypes.byref(ovl))
            if not ok:
                code = _err()
                if code != ERROR_IO_PENDING:
                    return code
                if _k32.WaitForSingleObject(ovl.hEvent, 2000) != WAIT_OBJECT_0:
                    _k32.CancelIoEx(self._h, ctypes.byref(ovl))
                    return WAIT_TIMEOUT
                if not _k32.GetOverlappedResult(self._h, ctypes.byref(ovl),
                                                ctypes.byref(written), 0):
                    return _err()
            return 0
        finally:
            _k32.CloseHandle(ovl.hEvent)

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
        h, self._h = self._h, None
        if h:
            _k32.CancelIoEx(h, None)        # unblock the reader's pending read
            if self._thread:
                self._thread.join(timeout=1.5)
            _k32.CloseHandle(h)
        self._thread = None
