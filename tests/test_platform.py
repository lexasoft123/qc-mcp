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
from qc_mcp import lockfile as L       # noqa: E402
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


class _SilentIO:
    """A backend nothing ever talks on."""
    def open(self): pass
    def close(self): pass
    def set_report(self, *a, **k): pass
    def read_reports(self, timeout=0.1): return []


def _shared_qc(live, firmware_on_try=1):
    """A share=True QuadCortex on a fake backend, with the wire-level steps
    stubbed so open() can be traced: `live` is what the session sniff sees,
    `firmware_on_try` which detect_version call first learns the firmware
    (2 = the session we joined was already dying)."""
    import qc_mcp.transport as T
    calls = []
    real = T.open_hid
    T.open_hid = lambda **kw: _SilentIO()
    try:
        qc = T.QuadCortex(share=True)
    finally:
        T.open_hid = real
    qc._session_live = lambda seconds=1.5: (calls.append("sniff"), live)[1]
    qc._handshake = lambda: calls.append("handshake")
    qc._start_heartbeat = lambda: calls.append("heartbeat")
    tries = []
    def detect():
        tries.append(1)
        qc.firmware = "4.1.0" if len(tries) >= firmware_on_try else None
        calls.append("detect")
    qc.detect_version = detect
    return qc, calls


def test_shared_handle_rides_a_live_session_instead_of_handshaking():
    """Windows: Cortex Control keeps its own session beside our shared handle.
    Our handshake - ResetCommsBuffers with a fresh session_id - makes the device
    drop the app's subscriptions, and Cortex Control shows "Device connection
    lost" seconds after we join (seen on a QC Mini, CorOS 4.1.0). So when the
    wire is already alive, ride that session like the macOS bridge does."""
    qc, calls = _shared_qc(live=True)
    qc.open()
    assert qc.riding, "a live wire means someone else owns the session"
    assert "handshake" not in calls and "heartbeat" not in calls, calls
    assert calls == ["sniff", "detect"], calls


def test_shared_handle_makes_its_own_session_when_the_wire_is_silent():
    """With the app shut nobody heartbeats the device, so the shared handle
    (which `auto` always takes on Windows) must still run the handshake."""
    qc, calls = _shared_qc(live=False)
    qc.open()
    assert not qc.riding
    assert calls == ["sniff", "heartbeat", "handshake", "detect"], calls


def test_shared_handle_falls_back_to_its_own_session_if_the_one_it_joined_dies():
    """The app's session lingers ~10 s after it quits: the sniff sees traffic
    but the first read gets nothing. Then it is ours to make."""
    qc, calls = _shared_qc(live=True, firmware_on_try=2)
    qc.open()
    assert not qc.riding and qc.firmware == "4.1.0"
    assert calls == ["sniff", "detect", "heartbeat", "handshake", "detect"], calls


def test_direct_and_bridge_modes_never_sniff():
    """Seizing the device means the session is ours by definition, and the
    bridge already rides the app's - neither should spend 1.5 s listening."""
    import qc_mcp.transport as T
    real = T.open_hid
    T.open_hid = lambda **kw: _SilentIO()
    try:
        qc = T.QuadCortex(share=False)
    finally:
        T.open_hid = real
    calls = []
    qc._session_live = lambda seconds=1.5: (calls.append("sniff"), True)[1]
    qc._handshake = lambda: calls.append("handshake")
    qc._start_heartbeat = lambda: calls.append("heartbeat")
    qc.detect_version = lambda: calls.append("detect")
    qc.open()
    assert calls == ["heartbeat", "handshake", "detect"], calls
    assert not qc.riding


def test_close_ends_only_a_session_we_own():
    """A session lingers ~10 s after its last KeepAlive; close() ends ours with
    Connection{connected:false} so the next shared open cannot mistake the
    corpse for Cortex Control. Riding the app's session it must NOT send it -
    that would knock the app off exactly like the handshake did."""
    import qc_mcp.transport as T
    for live, expect_conn in ((False, True), (True, False)):
        qc, calls = _shared_qc(live=live)
        sent = []
        qc.send = lambda cmd, msg=None, **kw: sent.append(cmd)
        import threading
        qc._start_heartbeat = lambda: setattr(qc, "_hb_stop", threading.Event()) or calls.append("heartbeat")
        qc.open()
        qc.close()
        assert ("Connection" in sent) is expect_conn, (live, sent)


def test_session_sniff_reads_the_wire():
    """_session_live is the real _collect on the backend: silence -> False."""
    import qc_mcp.transport as T
    real = T.open_hid
    T.open_hid = lambda **kw: _SilentIO()
    try:
        qc = T.QuadCortex(share=True)
    finally:
        T.open_hid = real
    assert qc._session_live(seconds=0) is False
    qc._collect = lambda seconds: qc._pending.append(("GlobalTempo", None, b"", b""))
    assert qc._session_live(seconds=0) is True
    assert qc._pending == [], "the sniffed frames must not leak into later reads"


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


# ── the session lock ────────────────────────────────────────────────────

def _code(fn):
    """A function's source with comments and docstrings removed.

    These checks are about what runs, and the first draft of them failed on
    their own explanations — the comment saying why GetExitCodeProcess is wrong
    read as a use of GetExitCodeProcess.
    """
    import io
    import tokenize
    src = inspect.getsource(fn)
    out = []
    prev_type = tokenize.INDENT
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        # a bare string right after a def/indent is a docstring
        if tok.type == tokenize.STRING and prev_type in (tokenize.INDENT, tokenize.NEWLINE):
            prev_type = tok.type
            continue
        if tok.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT):
            out.append(tok.string)
        prev_type = tok.type
    return " ".join(out)

def test_liveness_never_uses_os_kill_on_windows():
    """`os.kill(pid, 0)` is not a probe on Windows — it is a kill.

    Anything other than CTRL_C_EVENT/CTRL_BREAK_EVENT goes to TerminateProcess
    with the signal as the exit code, so the POSIX idiom for "does this process
    exist" would execute the owner of the device on every read of the lock. The
    win32 branch must not reach os.kill at all.
    """
    src = _code(L._alive_win32)
    assert "os.kill" not in src, "the Windows liveness check must not call os.kill"
    assert "OpenProcess" in src and "WaitForSingleObject" in src

    body = _code(L.alive)
    assert "win32" in body, "alive() must branch on the platform"
    assert body.index("win32") < body.index("os . kill"), \
        "the win32 branch has to come first, or Windows falls through to os.kill"


def test_win32_liveness_uses_wait_not_exit_code():
    """GetExitCodeProcess reports STILL_ACTIVE as 259, which is also a legal exit
    code: a process that exited with 259 would read as alive for ever."""
    src = _code(L._alive_win32)
    assert "GetExitCodeProcess" not in src
    assert "WAIT_TIMEOUT" in src


def test_lock_lives_beside_the_socket_on_both_platforms():
    """One directory holds the whole session, wherever that directory is."""
    for sock in ("/Users/x/Library/Application Support/qc-mcp/daemon.sock",
                 r"C:\Users\x\AppData\Local\qc-mcp\daemon.sock"):
        p = L.path(sock)
        assert p.endswith("session.json")
        assert os.path.dirname(p) == os.path.dirname(sock)


def test_lock_reads_and_writes_are_pure_stdlib():
    """No POSIX-only calls in the paths every platform runs: the lock is read on
    whichever machine holds the device, and a NameError there is a lie about
    ownership rather than an error anybody sees."""
    for fn in (L.read, L.read_raw, L.acquire, L.release, L.update, L._write):
        src = _code(fn)
        for posix_only in ("fcntl", "os.fork", "pwd", "grp", "os.getuid"):
            assert posix_only not in src, "%s uses %s" % (fn.__name__, posix_only)


def test_stale_records_need_no_cleanup_to_stop_lying():
    """The pid IS the liveness test, so nothing has to tidy up after a crash —
    which is what lets the file be advisory rather than an OS lock."""
    import json
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        sock = os.path.join(d, "daemon.sock")
        L._write(L.path(sock), {"pid": 999_999_999, "owner": "daemon", "mode": "direct"})
        assert L.read(sock) is None, "a dead owner still read as an owner"
        assert L.read_raw(sock)["pid"] == 999_999_999, "the record should still be inspectable"
        # ...and claiming it needs no --takeover
        rec = L.acquire("daemon", "bridge", sock)
        assert rec["pid"] == os.getpid()
        assert json.loads(open(L.path(sock)).read())["owner"] == "daemon"
        L.release(sock)


def test_neither_backend_writes_into_a_closed_device():
    """A write after close() must raise, on BOTH backends.

    macOS had no guard: close() sets `self.dev = None`, ctypes handed that NULL
    to IOHIDDeviceSetReport, and the process died with SIGSEGV
    (KERN_INVALID_ADDRESS at 0x18) — no traceback, no catchable exception. It
    fired on every Disconnect once transport.close() began sending a Connection
    frame of its own: the daemon closes twice on SIGTERM (the handler, then
    serve_forever's finally), and the second pass wrote to a released device.
    Windows raised RuntimeError here all along; this keeps the pair honest.
    """
    from qc_mcp import iohid                     # noqa: PLC0415

    for make in (lambda: iohid.IOHIDTransport(), lambda: winhid.WinHIDTransport()):
        t = make()
        t.close()                                # never opened: must be a no-op
        try:
            t.set_report(1, b"\x00" * 8)
            raise AssertionError(f"{type(t).__name__} wrote into a closed device")
        except RuntimeError:
            pass


def test_closing_twice_is_safe_on_both_backends():
    """close() is called twice on every daemon SIGTERM; it must not care."""
    from qc_mcp import iohid                     # noqa: PLC0415

    for t in (iohid.IOHIDTransport(), winhid.WinHIDTransport()):
        t.close()
        t.close()


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
