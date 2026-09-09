"""Per-scene bypass: the message sequence, pinned offline (no device needed).

Captured from Cortex Control's own right-click "Assign to Scenes" on a block's
bypass button, on a QC running CorOS 4.1.0:

    Grid UPDATE preset{chains{row:0, models{column:4,
                              params{index:7, scene_mode:true}}}}      assign
    Grid UPDATE preset{chains{row:0, models{column:4,
                              params{index:7, scene_mode:false}}}}     unassign

`index: 7` on a block whose read returns SEVEN param slots — the bypass control
sits one past the last readable param, and never appears in a read. A 23-slot
delay emits `index: 23`. The index was hardcoded to 4 before this, which is a
real param on anything but a 4-slot block (VOLUME on a Neural Capture, TONE CUT
on UK C30 TopBoost, LOW PASS on Tape Delay) — and that, not anything about
trails, is why per-scene bypass "silently no-op'd on delays".

Verified on hardware across six block types: amp (7 slots), reverb (7), capture
(7), cabsim (22), delay (23), IR loader (25). The cabsim is why the index must
come from the DEVICE read and not the catalog: ModelRepo declares 2 params for
model 12013, the device reports 22.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from qc_mcp import protocol as P       # noqa: E402
from qc_mcp.transport import QuadCortex, QCError   # noqa: E402


class _Recorder:
    """A QuadCortex that records what it would send, and walks scenes instantly."""
    def __init__(self, by_column):
        self.sent = []
        self.scenes = []
        self.by_column = by_column          # {column: param slots}; absent = empty slot

    def _preset(self):
        bp = P.message_class("Grid")().preset
        ch = bp.chains.add()
        for col in range(max(self.by_column) + 1):
            m = ch.models.add()
            n = self.by_column.get(col)
            if n is None:
                continue                    # hash stays 0: nothing on that square
            m.hash = 1011
            for i in range(n):
                m.params.add().index = i
        return bp


def _qc(by_column=None):
    qc = QuadCortex.__new__(QuadCortex)          # no device, no handshake
    rec = _Recorder(by_column or {0: 7})
    qc._request_id = 0
    qc.next_request_id = lambda: 1
    qc.send = lambda cmd, msg=None, **kw: rec.sent.append((cmd, msg))
    qc.get_current_preset = lambda *a, **k: rec._preset()
    qc.set_scene = lambda s: rec.scenes.append(s)
    qc._await_scene = lambda s, **k: True
    return qc, rec


def _grids(rec):
    return [m for c, m in rec.sent if c == "Grid"]


def test_bypass_param_index_is_one_past_the_last_readable_param():
    """A read gives N slots; the bypass lives at N. Hardcoding 4 wrote to a real
    knob on every block that does not happen to have four params."""
    for slots in (7, 22, 23, 25):    # amp/capture, cabsim, delay, IR loader
        qc, _ = _qc({0: slots})
        assert qc.bypass_param_index(0, 0) == slots, slots


def test_missing_block_is_an_error_not_a_guess():
    """Better to refuse than to write a bypass flag at a guessed index."""
    qc, _ = _qc({0: 7})
    try:
        qc.bypass_param_index(0, 5)
    except QCError:
        return
    raise AssertionError("expected QCError for an empty grid position")


def test_per_scene_bypass_assigns_then_writes_each_scene():
    qc, rec = _qc({4: 7})
    pattern = [True, False, True, False, False, True, True, False]
    qc.set_block_bypass(0, 4, scenes=pattern)

    grids = _grids(rec)
    assign = grids[0]
    p = assign.preset.chains[0].models[0].params[0]
    assert assign.preset.chains[0].row == 0
    assert assign.preset.chains[0].models[0].column == 4
    assert (p.index, p.scene_mode) == (7, True), (p.index, p.scene_mode)
    assert not p.param_values, "the assign carries no value"

    writes = grids[1:]
    assert len(writes) == 8, len(writes)
    for want, g in zip(pattern, writes):
        cb = g.preset.bypass[0].colBypass[0]
        assert g.preset.bypass[0].row == 0 and cb.column == 4
        assert len(cb.sceneBypass) == 1, "the app sends ONE flag, for the active scene"
        assert cb.sceneBypass[0].bypass is want
        assert not cb.sceneMode, "sceneMode is not writable — the param assign does it"

    # each scene made active before its write, and scene A restored afterwards
    assert rec.scenes == [0, 1, 2, 3, 4, 5, 6, 7, 0], rec.scenes


def test_single_scene_bypass_is_one_plain_message():
    """The common case must stay a single message — no read, no scene walk."""
    qc, rec = _qc({2: 7})
    qc.set_block_bypass(1, 2, bypassed=True)
    grids = _grids(rec)
    assert len(grids) == 1 and not rec.scenes
    cb = grids[0].preset.bypass[0].colBypass[0]
    assert grids[0].preset.bypass[0].row == 1 and cb.column == 2
    assert [x.bypass for x in cb.sceneBypass] == [True]


def test_explicit_bypass_param_skips_the_device_read():
    qc, rec = _qc({0: 7})
    qc.get_current_preset = lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("should not read the device when the index is given"))
    qc.set_block_bypass(0, 0, scenes=[True, False], bypass_param=23)
    assert _grids(rec)[0].preset.chains[0].models[0].params[0].index == 23


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
            passed += 1
    print(f"\n{passed}/{passed} passed")
