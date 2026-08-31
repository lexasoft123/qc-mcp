"""Which HID backend talks to the device on this OS.

The transport is written against one small interface — `open()`, `set_report()`,
`read_reports()`, `close()` — and three things implement it:

  macOS    `iohid.IOHIDTransport`   IOKit HID via ctypes
  Windows  `winhid.WinHIDTransport` setupapi/hid.dll via ctypes
  either   `bridge.FifoBridge`      shares Cortex Control's live session
                                    (macOS only — it rides the DYLD interposer)

Both direct backends are imported lazily: `iohid` dlopens IOKit at import time
and `winhid` needs Win32 DLLs, so importing the wrong one is a hard error rather
than an unused module.
"""
from __future__ import annotations
import sys

class BridgeError(Exception):
    """Raised by either bridge backend. Lives here, not in `bridge`, so the
    Windows bridge can raise it without importing POSIX-only FIFO code."""


#: The device on USB. Neural DSP ships more than one model and they all speak
#: the same protocol - same HID interface (MI_05), same 128-byte reports, same
#: protobuf schema - so the only thing that differs between them is the USB
#: product id. Match the whole family: pinning a single id is what made a Quad
#: Cortex Mini report as "no device found".
QC_VID = 0x152A

#: product id -> model name. Both verified on hardware:
#:   0x880A  Quad Cortex       (Version.device_type QC)
#:   0x892F  Quad Cortex Mini  (Version.device_type ATMA) - Win10, CorOS 4.0.1
QC_PIDS = {0x880A: "Quad Cortex", 0x892F: "Quad Cortex Mini"}


def device_name(pid):
    """Model name for a product id we matched."""
    return QC_PIDS.get(pid) or f"Neural DSP device {pid:#06x}"


def device_ids(pid=None):
    """The product ids a transport should match: one if the caller pinned it,
    else the whole family."""
    return (pid,) if pid is not None else tuple(QC_PIDS)


def not_found_error(vid, pids):
    """The "nothing plugged in" message, worded the same on both platforms."""
    ids = "/".join(f"{p:#06x}" for p in pids)
    names = " or a ".join(device_name(p) for p in pids)
    return (f"no HID device {vid:#06x}:{ids} found - is a {names} plugged in "
            "over USB and powered on?")


#: Backends that can seize the device on their own, per platform.
DIRECT_BACKENDS = {"darwin": "iohid", "win32": "winhid"}

#: Bridge mode = the MCP and Cortex Control both controlling the device at once.
#: Both platforms can, by different means:
#:   macOS   a DYLD interposer shares the app's session (IOKit gives the device
#:           to a single owner, so there is no other way in).
#:   Windows nothing special -- the HID stack copies input reports to every open
#:           handle and accepts output reports from a second handle too, so the
#:           MCP just opens its own NON-exclusive handle (`QuadCortex(share=True)`).
#: Verified on Windows: a shared handle wrote an amp VOLUME and read the new
#: value back while Cortex Control stayed running.
BRIDGE_PLATFORMS = ("darwin", "win32")

#: Where each platform's interposer publishes its two endpoints.
BRIDGE_ENDPOINTS = {
    "darwin": ("/tmp/qc_inject", "/tmp/qc_in"),
    "win32": (r"\\.\pipe\qc_inject", r"\\.\pipe\qc_out"),
}


def platform_name(platform=None):
    """A human label for error messages ('macOS', 'Windows', or the raw tag)."""
    p = platform or sys.platform
    return {"darwin": "macOS", "win32": "Windows"}.get(p, p)


def direct_supported(platform=None):
    return (platform or sys.platform) in DIRECT_BACKENDS


def bridge_supported(platform=None):
    return (platform or sys.platform) in BRIDGE_PLATFORMS


def open_bridge(**kwargs):
    """Construct (don't open) the bridge transport that shares Cortex Control's
    session: FIFOs on macOS, named pipes on Windows."""
    if sys.platform == "darwin":
        from .bridge import FifoBridge
        return FifoBridge(**kwargs)
    if sys.platform == "win32":
        from .winbridge import WinBridge
        return WinBridge(**kwargs)
    raise BridgeError(
        f"bridge mode needs an interposer for {platform_name()}; there isn't one. "
        "Use direct mode.")


def open_hid(**kwargs):
    """Construct (don't open) the direct HID transport for this platform."""
    if sys.platform == "darwin":
        from .iohid import IOHIDTransport
        return IOHIDTransport(**kwargs)
    if sys.platform == "win32":
        from .winhid import WinHIDTransport
        return WinHIDTransport(**kwargs)
    raise RuntimeError(
        f"no HID backend for {sys.platform}: qc-mcp speaks to the Quad Cortex "
        "on macOS (IOKit) and Windows (hid.dll). Linux would need a hidraw "
        "backend implementing the same open/set_report/read_reports/close API.")
