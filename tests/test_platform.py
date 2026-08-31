"""Offline checks that the platform split holds (no device, and no Windows needed).

qc-mcp talks to the Quad Cortex through one small interface — open / set_report /
read_reports / close — with an IOKit backend on macOS and a hid.dll one on
Windows. These tests keep the two honest against each other, since CI and the
author's machine only ever exercise one of them.
"""
import ctypes
import inspect
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from qc_mcp import backend as B        # noqa: E402
from qc_mcp import winhid              # noqa: E402
from qc_mcp import server as S         # noqa: E402

API = ("open", "set_report", "read_reports", "close")


def _backends():
    """Every backend importable here. winhid is the only one that loads anywhere;
    iohid dlopens IOKit and bridge needs POSIX fcntl, so each is gated to where it
    runs — which means the cross-backend checks below are strongest on macOS."""
    from qc_mcp.winhid import WinHIDTransport
    out = [WinHIDTransport]
    if sys.platform == "darwin":
        from qc_mcp.iohid import IOHIDTransport
        out.append(IOHIDTransport)
    if sys.platform != "win32":        # bridge.py needs POSIX fcntl/select
        from qc_mcp.bridge import FifoBridge
        out.append(FifoBridge)
    else:
        from qc_mcp.winbridge import WinBridge
        out.append(WinBridge)
    return out


def test_every_backend_implements_the_same_api():
    for cls in _backends():
        for name in API:
            assert callable(getattr(cls, name, None)), f"{cls.__name__} lacks {name}()"


def test_backend_signatures_match():
    """A caller must not have to know which backend it holds."""
    for cls in _backends():
        set_report = inspect.signature(cls.set_report)
        assert list(set_report.parameters) == ["self", "report_id", "data", "include_id"], \
            f"{cls.__name__}.set_report signature drifted: {set_report}"
        read = inspect.signature(cls.read_reports)
        assert list(read.parameters) == ["self", "timeout"], \
            f"{cls.__name__}.read_reports signature drifted: {read}"


def test_platform_support_matrix():
    assert B.direct_supported("darwin") and B.direct_supported("win32")
    assert not B.direct_supported("linux")
    # Both can run alongside the app, by different means (interposer / shared
    # handle); neither mechanism exists elsewhere.
    assert B.bridge_supported("darwin") and B.bridge_supported("win32")
    assert not B.bridge_supported("linux")
    # Every enabled bridge platform needs endpoints; the reverse need not hold —
    # win32 keeps its pipe names for the interposer even though its bridge is off.
    assert set(B.BRIDGE_PLATFORMS) <= set(B.BRIDGE_ENDPOINTS)
    assert B.platform_name("win32") == "Windows"


def test_shared_mode_asks_for_a_non_seizing_handle():
    """share=True must NOT seize: that is the whole point on Windows, where the
    app keeps its own handle open."""
    import qc_mcp.transport as T
    seen = {}

    def fake_open_hid(**kw):
        seen.update(kw)
        raise RuntimeError("stop here - we only care about the arguments")

    real = T.open_hid
    try:
        T.open_hid = fake_open_hid
        for share, want_seize in ((True, False), (False, True)):
            seen.clear()
            try:
                T.QuadCortex(share=share)
            except RuntimeError:
                pass
            assert seen.get("seize") is want_seize, \
                f"share={share} should pass seize={want_seize}, got {seen}"
    finally:
        T.open_hid = real


def test_windows_auto_shares_even_with_cortex_control_shut():
    """On Windows `auto` must take a NON-exclusive handle whether or not Cortex
    Control is up. Gating that on "the app is already running" is a macOS-shaped
    precondition, and it cost a whole session: the daemon seized the device, and
    a handle held exclusively cannot be shared afterwards, so Cortex Control
    opened to "no device" for as long as the daemon lived."""
    import qc_mcp.daemon as D
    import qc_mcp.transport as T

    opened = {}

    class FakeQC:
        def __init__(self, bridge=False, share=False):
            opened.update(bridge=bridge, share=share)
        def open(self, handshake=True):
            raise RuntimeError("stop here - we only care about the mode choice")

    class WinSys:
        """Only daemon.py is told it is on Windows. Assigning to `D.sys.platform`
        would set it on the real sys module, i.e. for every module in the
        process - including any that reads or caches it while serve() runs."""
        platform = "win32"

        def __getattr__(self, name):
            return getattr(sys, name)

    real_qc, real_sys = T.QuadCortex, D.sys
    try:
        T.QuadCortex = FakeQC
        D.sys = WinSys()
        import qc_mcp.server as S
        for cortex_up in (False, True):
            opened.clear()
            real_bridge, S._bridge_running = S._bridge_running, lambda: cortex_up
            try:
                try:
                    D.serve("unused.sock", mode="auto")
                except RuntimeError:
                    pass
            finally:
                S._bridge_running = real_bridge
            assert opened == {"bridge": False, "share": True}, \
                f"Cortex Control running={cortex_up} should still share, got {opened}"
    finally:
        T.QuadCortex, D.sys = real_qc, real_sys


def test_open_hid_picks_the_backend_for_the_platform():
    real = sys.platform
    try:
        if real == "darwin":
            assert type(B.open_hid(seize=True)).__name__ == "IOHIDTransport"
        sys.platform = "sunos5"
        try:
            B.open_hid()
        except RuntimeError as e:
            assert "no HID backend" in str(e)
        else:
            raise AssertionError("open_hid must refuse an unknown platform")
    finally:
        sys.platform = real


def test_winhid_imports_off_windows_but_refuses_to_open():
    """Importable everywhere (these tests need it); only the Win32 calls are gated."""
    if sys.platform == "win32":
        return
    try:
        winhid.WinHIDTransport().open()
    except RuntimeError as e:
        assert "Windows-only" in str(e)
    else:
        raise AssertionError("winhid.open() must refuse a non-Windows host")


def test_win32_struct_layouts():
    """Wrong sizes here fail as opaque ERROR_INVALID_PARAMETER on real hardware."""
    assert ctypes.sizeof(winhid.HIDP_CAPS) == 64
    assert ctypes.sizeof(winhid.HIDD_ATTRIBUTES) == 12   # 10 fields, padded to 12
    # SetupAPI validates cbSize against the *C* declaration, not our buffer.
    assert winhid._DETAIL_CBSIZE == (8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6)
    assert winhid.SP_DEVICE_INTERFACE_DETAIL_DATA_W.DevicePath.offset == 4


def test_refused_writes_are_counted_not_swallowed():
    """A refused HID write must leave a trace. It is NOT fatal — Windows returns
    ERROR_GEN_FAILURE(31) even on handles whose writes do land — but silently
    dropping it is how a connection can look healthy while every write vanishes."""
    import threading
    import qc_mcp.transport as T

    class Refusing:
        BENIGN_WRITE_CODES = frozenset({0})

        def set_report(self, report_id, data, include_id=False):
            return 31                      # ERROR_GEN_FAILURE

    qc = T.QuadCortex.__new__(T.QuadCortex)
    qc.io = Refusing()
    qc._send_lock = threading.Lock()
    qc.write_errors = 0
    qc.last_write_error = None
    qc.send("Version", proto_bytes=b"")    # must not raise
    assert qc.write_errors == 1 and qc.last_write_error == 31

    class BenignOnMac(Refusing):
        BENIGN_WRITE_CODES = frozenset({0, 0xe0005000})

        def set_report(self, report_id, data, include_id=False):
            return 0xe0005000              # IOKit's harmless code

    qc.io = BenignOnMac()
    qc.send("Version", proto_bytes=b"")
    assert qc.write_errors == 1, "IOKit's benign code must not be counted"


def test_device_ids_recoverable_from_the_interface_path():
    """A device held exclusively refuses even a zero-access probe, so the ids have
    to come from the path — otherwise "quit Cortex Control" misreports as "no
    device found", which is exactly what happened on the first hardware run."""
    path = r"\\?\hid#vid_152a&pid_880a&mi_05#7&a6b02d1&0&0000#{4d1e55b2-f16f-11cf}"
    assert winhid._ids_from_path(path) == (0x152A, 0x880A)
    assert winhid._ids_from_path(r"\\?\HID#VID_152A&PID_880A#x") == (0x152A, 0x880A)
    assert winhid._ids_from_path(r"\\?\usb#something-else") == (None, None)


def test_every_model_in_the_family_is_matched_by_default():
    """The Mini is a different USB product (0x892F) running the same protocol.
    Pinning 0x880A is what made a plugged-in Mini report "no device found"; both
    transports must accept the whole family unless a caller pins one id."""
    transports = [winhid.WinHIDTransport]
    if sys.platform == "darwin":                  # iohid dlopens IOKit on import
        from qc_mcp.iohid import IOHIDTransport
        transports.append(IOHIDTransport)

    assert set(B.QC_PIDS) == {0x880A, 0x892F}
    for transport in transports:
        assert set(transport().pids) == set(B.QC_PIDS), transport
        assert transport(pid=0x880A).pids == (0x880A,), transport
        # the id we actually opened is only known after open()
        assert transport().pid is None, transport
    # the "not plugged in" message names every model it looked for
    msg = B.not_found_error(B.QC_VID, B.device_ids())
    assert "0x880a" in msg and "0x892f" in msg and "Mini" in msg
    assert msg.isascii(), "Windows-facing runtime strings must be ASCII"


def test_enumerate_filters_on_the_whole_family():
    """enumerate_devices takes one product id or a set of them; the vendor is
    shared with other USB-audio gear, so an unfiltered vendor match is wrong."""
    probed = [
        {"path": r"\\?\hid#vid_152a&pid_880a&mi_05#a", "vid": 0x152A, "pid": 0x880A},
        {"path": r"\\?\hid#vid_152a&pid_892f&mi_05#b", "vid": 0x152A, "pid": 0x892F},
        {"path": r"\\?\hid#vid_152a&pid_0001&mi_00#c", "vid": 0x152A, "pid": 0x0001},
    ]
    real_paths, real_probe, real_load = (
        winhid._interface_paths, winhid._probe, winhid._load)
    winhid._interface_paths = lambda: [d["path"] for d in probed]
    winhid._probe = lambda path: next(dict(d) for d in probed if d["path"] == path)
    winhid._load = lambda: None
    try:
        pids = lambda got: sorted(d["pid"] for d in got)          # noqa: E731
        assert pids(winhid.enumerate_devices(0x152A, B.QC_PIDS)) == [0x880A, 0x892F]
        assert pids(winhid.enumerate_devices(0x152A, 0x892F)) == [0x892F]
        assert pids(winhid.enumerate_devices(0x152A)) == [0x0001, 0x880A, 0x892F]
    finally:
        winhid._interface_paths, winhid._probe, winhid._load = (
            real_paths, real_probe, real_load)


def test_output_reports_are_padded_to_the_exact_report_length():
    """Windows rejects a write that isn't exactly OutputReportByteLength bytes."""
    sent = {}

    class FakeK32:
        def CreateEventW(self, *a):
            return 1

        def WriteFile(self, handle, buf, length, written, ovl):
            sent["buf"], sent["len"] = bytes(buf), length
            written._obj.value = length
            return 1

        def CloseHandle(self, *a):
            return 1

    io = winhid.WinHIDTransport()
    io._h = 1
    io._out_len = 129
    real, winhid._k32 = winhid._k32, FakeK32()
    try:
        assert io.set_report(0x02, b"\x05\xc0hello") == 0
        assert sent["len"] == 129 and len(sent["buf"]) == 129
        assert sent["buf"][:7] == b"\x05\xc0hello"
        assert set(sent["buf"][7:]) == {0}, "tail must be zero-padded"
        # include_id prepends the report id, and the total stays 129.
        io.set_report(0x02, b"\x01\xc0x", include_id=True)
        assert sent["buf"][:4] == b"\x02\x01\xc0x" and sent["len"] == 129
        # An over-long buffer is truncated, never sent long.
        io.set_report(0x02, b"\xaa" * 300)
        assert sent["len"] == 129
    finally:
        winhid._k32 = real


def test_server_hides_bridge_mode_where_it_cannot_run():
    real = B.bridge_supported
    try:
        B.bridge_supported = lambda platform=None: False
        assert S._bridge_endpoints() is False, "no bridge endpoints where bridge mode cannot run"
        assert S._bridge_running() is False
        assert S._launch_bridge() == S._NO_BRIDGE
    finally:
        B.bridge_supported = real


def test_connect_docstring_states_the_platform_limits():
    """A client only ever sees the docstring, so it has to say how each platform
    actually runs alongside the app — they are not the same mechanism."""
    doc = S.connect.__doc__ or ""
    assert "macOS" in doc and "Windows" in doc
    assert "interposer" in doc, "must explain the macOS mechanism"
    assert "NON-exclusive" in doc, "must explain the Windows mechanism"


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    ok = 0
    for fn in fns:
        try:
            fn(); ok += 1; print(f"PASS {fn.__name__}")
        except Exception:
            print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{ok}/{len(fns)} passed")
    sys.exit(0 if ok == len(fns) else 1)
