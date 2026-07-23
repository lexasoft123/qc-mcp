"""Direct IOKit IOHIDManager transport (ctypes), replicating how Cortex Control
talks to the Quad Cortex. Avoids hidapi's report-buffer quirks and lets us
control open options + input-report callbacks exactly like the app."""
from __future__ import annotations
import ctypes
import ctypes.util
import struct
import threading
import time

CF = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))
IOKit = ctypes.CDLL(ctypes.util.find_library("IOKit"))

VoidP = ctypes.c_void_p
CFIndex = ctypes.c_long

# CoreFoundation
CF.CFRelease.argtypes = [VoidP]
CF.CFStringCreateWithCString.restype = VoidP
CF.CFStringCreateWithCString.argtypes = [VoidP, ctypes.c_char_p, ctypes.c_uint32]
CF.CFNumberCreate.restype = VoidP
CF.CFNumberCreate.argtypes = [VoidP, ctypes.c_int, VoidP]
CF.CFNumberGetValue.restype = ctypes.c_bool
CF.CFNumberGetValue.argtypes = [VoidP, ctypes.c_int, VoidP]
CF.CFDictionaryCreate.restype = VoidP
CF.CFDictionaryCreate.argtypes = [VoidP, VoidP, VoidP, CFIndex, VoidP, VoidP]
CF.CFSetGetCount.restype = CFIndex
CF.CFSetGetCount.argtypes = [VoidP]
CF.CFSetGetValues.argtypes = [VoidP, VoidP]
CF.CFRunLoopGetCurrent.restype = VoidP
CF.CFRunLoopRun.argtypes = []
CF.CFRunLoopStop.argtypes = [VoidP]
CF.CFRunLoopRunInMode.restype = ctypes.c_int32
CF.CFRunLoopRunInMode.argtypes = [VoidP, ctypes.c_double, ctypes.c_bool]

kCFStringEncodingUTF8 = 0x08000100
kCFNumberSInt32Type = 3
kCFTypeDictionaryKeyCallBacks = VoidP.in_dll(CF, "kCFTypeDictionaryKeyCallBacks")
kCFTypeDictionaryValueCallBacks = VoidP.in_dll(CF, "kCFTypeDictionaryValueCallBacks")
kCFRunLoopDefaultMode = VoidP.in_dll(CF, "kCFRunLoopDefaultMode")

# IOKit HID
IOKit.IOHIDManagerCreate.restype = VoidP
IOKit.IOHIDManagerCreate.argtypes = [VoidP, ctypes.c_uint32]
IOKit.IOHIDManagerSetDeviceMatching.argtypes = [VoidP, VoidP]
IOKit.IOHIDManagerOpen.restype = ctypes.c_int
IOKit.IOHIDManagerOpen.argtypes = [VoidP, ctypes.c_uint32]
IOKit.IOHIDManagerCopyDevices.restype = VoidP
IOKit.IOHIDManagerCopyDevices.argtypes = [VoidP]
IOKit.IOHIDDeviceGetProperty.restype = VoidP
IOKit.IOHIDDeviceGetProperty.argtypes = [VoidP, VoidP]
IOKit.IOHIDDeviceOpen.restype = ctypes.c_int
IOKit.IOHIDDeviceOpen.argtypes = [VoidP, ctypes.c_uint32]
IOKit.IOHIDDeviceClose.restype = ctypes.c_int
IOKit.IOHIDDeviceClose.argtypes = [VoidP, ctypes.c_uint32]
IOKit.IOHIDDeviceSetReport.restype = ctypes.c_int
IOKit.IOHIDDeviceSetReport.argtypes = [VoidP, ctypes.c_int, CFIndex,
                                       ctypes.c_char_p, CFIndex]
IOKit.IOHIDDeviceScheduleWithRunLoop.argtypes = [VoidP, VoidP, VoidP]
IOKit.IOHIDDeviceRegisterInputReportCallback.argtypes = [
    VoidP, ctypes.c_char_p, CFIndex, VoidP, VoidP]

kIOHIDReportTypeOutput = 1
kIOHIDOptionsTypeNone = 0
kIOHIDOptionsTypeSeizeDevice = 1

INPUT_CB = ctypes.CFUNCTYPE(None, VoidP, ctypes.c_int, VoidP, ctypes.c_int,
                            ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint8),
                            CFIndex)


def _cfstr(s):
    return CF.CFStringCreateWithCString(None, s.encode(), kCFStringEncodingUTF8)


def _cfnum(n):
    v = ctypes.c_int32(n)
    return CF.CFNumberCreate(None, kCFNumberSInt32Type, ctypes.byref(v))


class IOHIDTransport:
    def __init__(self, vid=0x152A, pid=0x880A, report_size=128, seize=False):
        self.vid, self.pid = vid, pid
        self.report_size = report_size
        self.seize = seize
        self.mgr = None
        self.dev = None
        self._in_buf = (ctypes.c_uint8 * (report_size + 1))()
        self._cb_ref = None
        self._rx_lock = threading.Lock()
        self._rx = []   # list of (report_id, data) captured by the reader thread
        self._thread = None
        self._runloop = None
        self._ready = threading.Event()

    def open(self):
        self.mgr = IOKit.IOHIDManagerCreate(None, 0)
        keys = (VoidP * 2)(_cfstr("VendorID"), _cfstr("ProductID"))
        vals = (VoidP * 2)(_cfnum(self.vid), _cfnum(self.pid))
        match = CF.CFDictionaryCreate(
            None, ctypes.cast(keys, VoidP), ctypes.cast(vals, VoidP), 2,
            ctypes.byref(kCFTypeDictionaryKeyCallBacks),
            ctypes.byref(kCFTypeDictionaryValueCallBacks))
        IOKit.IOHIDManagerSetDeviceMatching(self.mgr, match)
        if IOKit.IOHIDManagerOpen(self.mgr, 0) != 0:
            raise RuntimeError("IOHIDManagerOpen failed")
        devset = IOKit.IOHIDManagerCopyDevices(self.mgr)
        if not devset:
            raise RuntimeError("no matching HID device")
        n = CF.CFSetGetCount(devset)
        arr = (VoidP * n)()
        CF.CFSetGetValues(devset, ctypes.cast(arr, VoidP))
        self.dev = arr[0]
        opts = kIOHIDOptionsTypeSeizeDevice if self.seize else kIOHIDOptionsTypeNone
        r = IOKit.IOHIDDeviceOpen(self.dev, opts)
        if r != 0:
            raise RuntimeError(f"IOHIDDeviceOpen failed 0x{r & 0xffffffff:08x}")

        def _cb(context, result, sender, rtype, report_id, report_ptr, length):
            data = bytes(bytearray(report_ptr[i] for i in range(length)))
            with self._rx_lock:
                self._rx.append((report_id, data))
        self._cb_ref = INPUT_CB(_cb)

        # Dedicated reader thread runs a CFRunLoop so input reports are never
        # dropped during large device streams.
        def _reader():
            self._runloop = CF.CFRunLoopGetCurrent()
            # buffer must hold the full input report INCLUDING the report-id
            # byte (129 = report id + 128 data); a short buffer truncates chunks.
            IOKit.IOHIDDeviceRegisterInputReportCallback(
                self.dev, ctypes.cast(self._in_buf, ctypes.c_char_p),
                self.report_size + 1, self._cb_ref, None)
            IOKit.IOHIDDeviceScheduleWithRunLoop(
                self.dev, self._runloop, kCFRunLoopDefaultMode)
            self._ready.set()
            CF.CFRunLoopRun()
        self._thread = threading.Thread(target=_reader, daemon=True)
        self._thread.start()
        self._ready.wait(2.0)
        return self

    def set_report(self, report_id, data, include_id=False):
        """data = the chunk bytes [chunkLen][flags][payload] (report_size long)."""
        buf = bytes(data)
        if include_id:
            buf = bytes([report_id]) + buf
        r = IOKit.IOHIDDeviceSetReport(self.dev, kIOHIDReportTypeOutput,
                                       report_id, buf, len(buf))
        return r

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
        if self._runloop:
            CF.CFRunLoopStop(self._runloop)
            self._runloop = None
        if self.dev:
            IOKit.IOHIDDeviceClose(self.dev, 0)
            self.dev = None
