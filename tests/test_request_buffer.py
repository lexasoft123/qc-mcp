"""Offline tests for QuadCortex.request()'s reply buffer and _await_scene's retry.

`request()` consumes its reply from the shared `_pending` buffer and must leave
everything else exactly once. It used to rebuild the buffer as
`keep + [t for t in _pending if t is not (cmd, obj, raw, pb)]` — a fresh tuple is
never `is` an element, so the WHOLE buffer was re-appended after `keep` and it
doubled on every reply. Scene-by-scene writes (two requests per scene) then
slowed to a halt on a QC Mini: 1.4 s, 12 s, then over five minutes per param.
No device needed.
"""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from qc_mcp import protocol as P  # noqa: E402
from qc_mcp import transport as T  # noqa: E402

SCENE = P.NAME_TO_CMD["Scene"]
GRID = P.NAME_TO_CMD["Grid"]


def _qc():
    qc = T.QuadCortex.__new__(T.QuadCortex)       # no device, no handshake
    qc._io_lock = threading.RLock()
    qc._pending = []
    qc._rid = 1000
    qc.next_request_id = lambda: qc._rid
    qc.sent = []
    qc.send = lambda cmd, msg=None, raw=None: qc.sent.append((cmd, msg))
    return qc


def _scene_reply(rid, scene):
    obj = P.message_class("Scene")(action=P.ACTION["UPDATE"], request_id=rid,
                                   selected_scene=scene)
    pb = obj.SerializeToString()
    return (SCENE, obj, b"raw", pb)


def _echo(n):
    """An unrelated frame (a Grid echo) that is not the reply anybody waits for."""
    obj = P.message_class("Grid")(action=P.ACTION["UPDATE"], request_id=n)
    return (GRID, obj, b"raw", obj.SerializeToString() + b"xx")


def test_reply_is_consumed_and_the_rest_kept_once():
    qc = _qc()
    unrelated = [_echo(i) for i in range(5)]

    def collect(_s):
        if not any(t[0] == SCENE for t in qc._pending):
            qc._pending.extend(unrelated[:2] + [_scene_reply(qc._rid, 3)] + unrelated[2:])
    qc._collect = collect
    msg = P.message_class("Scene")(action=P.ACTION["READ"], request_id=qc._rid)
    _cmd, obj, _raw = qc.request("Scene", msg, timeout_ms=500)
    assert obj.selected_scene == 3
    assert qc._pending == unrelated, [t[1].request_id for t in qc._pending]


def test_buffer_does_not_grow_over_many_requests():
    qc = _qc()
    qc._pending = [_echo(i) for i in range(3)]       # stale frames already queued

    def collect(_s):
        if not any(t[0] == SCENE for t in qc._pending):
            qc._pending.append(_scene_reply(qc._rid, 0))
    qc._collect = collect
    for i in range(20):
        qc._rid = 2000 + i
        msg = P.message_class("Scene")(action=P.ACTION["READ"], request_id=qc._rid)
        qc.request("Scene", msg, timeout_ms=500)
    assert len(qc._pending) == 3, len(qc._pending)   # was 3 * 2**20 before the fix


def test_await_scene_retries_a_slow_read_instead_of_raising():
    qc = _qc()
    calls = []

    def read_state(cmd, timeout_ms=600):
        calls.append(cmd)
        if len(calls) < 3:
            raise T.QCError("no response to Scene within %dms" % timeout_ms)
        return P.message_class("Scene")(selected_scene=2)
    qc.read_state = read_state
    assert qc._await_scene(2, timeout_s=2.0) is True
    assert len(calls) == 3, calls


def test_await_scene_gives_up_with_false_not_an_exception():
    qc = _qc()

    def read_state(cmd, timeout_ms=600):
        raise T.QCError("no response")
    qc.read_state = read_state
    assert qc._await_scene(1, timeout_s=0.3) is False


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    ok = 0
    for name, fn in fns:
        try:
            fn()
            print("  ok   %s" % name)
            ok += 1
        except AssertionError as e:
            print("  FAIL %s: %s" % (name, e))
        except Exception as e:
            print("  ERR  %s: %s: %s" % (name, type(e).__name__, e))
    print("%d/%d passed" % (ok, len(fns)))
    sys.exit(0 if ok == len(fns) else 1)
