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

#: Backends that can seize the device on their own, per platform.
DIRECT_BACKENDS = {"darwin": "iohid", "win32": "winhid"}

#: Bridge mode needs the DYLD interposer in `interceptor/`, which is Mach-O only.
BRIDGE_PLATFORMS = ("darwin",)


def platform_name(platform=None):
    """A human label for error messages ('macOS', 'Windows', or the raw tag)."""
    p = platform or sys.platform
    return {"darwin": "macOS", "win32": "Windows"}.get(p, p)


def direct_supported(platform=None):
    return (platform or sys.platform) in DIRECT_BACKENDS


def bridge_supported(platform=None):
    return (platform or sys.platform) in BRIDGE_PLATFORMS


def open_hid(**kwargs):
    """Construct (don't open) the direct HID transport for this platform."""
    if sys.platform == "darwin":
        from .iohid import IOHIDTransport
        return IOHIDTransport(**kwargs)
    if sys.platform == "win32":
        from .winhid import WinHIDTransport
        return WinHIDTransport(**kwargs)
    raise RuntimeError(
        f"no HID backend for {sys.platform} — qc-mcp speaks to the Quad Cortex "
        "on macOS (IOKit) and Windows (hid.dll). Linux would need a hidraw "
        "backend implementing the same open/set_report/read_reports/close API.")
