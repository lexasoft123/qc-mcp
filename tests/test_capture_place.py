"""Placing a capture: the message, pinned offline (no device needed).

Captured from Cortex Control 4.1.0 on CorOS 4.1.1 (a V1 placed, a V2 placed, then the
V1's capture changed): each is ONE Grid UPDATE with the capture model (14000 / 14001)
and its param 5 holding the capture's key. Sent that way the capture loads. The model
placed alone first, then param 5 in a second message, leaves the block bypassed with
nothing loaded - what set_capture used to send.
"""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from qc_mcp.transport import QuadCortex   # noqa: E402

KEY = "d7591effff1ba7d85bb458c5615a1c305a587957a4e28fff68a0df0d45a9809f"


def _qc():
    qc = QuadCortex.__new__(QuadCortex)          # no device, no handshake
    sent = []
    qc._io_lock = threading.RLock()
    qc.next_request_id = lambda: 1
    qc.send = lambda cmd, msg=None, **kw: sent.append((cmd, msg))
    qc._pending = []
    qc._collect = lambda _t: None                # no echo: add_block gives up and returns None
    return qc, sent


def test_one_message_with_model_and_capture():
    qc, sent = _qc()
    qc.set_capture(1, 3, KEY, "CA John's 2", version="v1")
    assert [c for c, _ in sent] == ["Grid"]
    m = sent[0][1].preset.chains[0]
    assert m.row == 1
    model = m.models[0]
    assert (model.hash, model.column) == (14000, 3)
    assert [p.index for p in model.params] == [5]
    assert model.params[0].param_values[0].string_value == KEY + "CA John's 2"


def test_v2_picks_14001():
    qc, sent = _qc()
    qc.set_capture(0, 0, KEY, version="v2")
    assert sent[0][1].preset.chains[0].models[0].hash == 14001
    assert sent[0][1].preset.chains[0].models[0].params[0].param_values[0].string_value == KEY


def test_add_block_alone_sends_no_params():
    qc, sent = _qc()
    qc.add_block(1011, row=0, column=2)
    model = sent[0][1].preset.chains[0].models[0]
    assert (model.hash, model.column, len(model.params)) == (1011, 2, 0)


def test_add_block_params_float_and_string():
    qc, sent = _qc()
    qc.add_block(1011, row=0, column=0, params={2: 0.5, 5: "x"})
    ps = {p.index: p.param_values[0] for p in sent[0][1].preset.chains[0].models[0].params}
    assert ps[2].float_value == 0.5 and ps[5].string_value == "x"


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
            passed += 1
    print(f"\n{passed}/{passed} passed")
