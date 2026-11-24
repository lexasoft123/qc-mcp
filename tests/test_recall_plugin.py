"""Recalling a plugin preset: the message, pinned offline (no device needed).

The device loads a plugin bank's preset from SetlistPosition UPDATE with is_plugin,
key_in_plugin_folder = the file's key ("<folder>/<name>.pb") AND folder_key = its folder.
Without folder_key the recall is silently ignored (CorOS 4.1.1).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from qc_mcp.transport import QuadCortex   # noqa: E402

FOLDER = "/opt/neuraldsp/Plugins/Archetype: Petrucci X/User"
KEY = FOLDER + "/GiO Clean Bernard.pb"


def _qc():
    qc = QuadCortex.__new__(QuadCortex)          # no device, no handshake
    sent = []
    qc.next_request_id = lambda: 1
    qc.send = lambda cmd, msg=None, **kw: sent.append((cmd, msg))
    return qc, sent


def test_plugin_recall_carries_the_folder():
    qc, sent = _qc()
    qc.recall(plugin_key=KEY)
    cmd, m = sent[0]
    assert cmd == "SetlistPosition"
    assert m.is_plugin and m.key_in_plugin_folder == KEY
    assert m.folder_key == FOLDER


def test_plugin_recall_explicit_folder_wins():
    qc, sent = _qc()
    qc.recall(folder_key="/somewhere/else", plugin_key=KEY)
    assert sent[0][1].folder_key == "/somewhere/else"


def test_setlist_recall_unchanged():
    qc, sent = _qc()
    qc.recall("/media/p4/Presets/My Presets", 5)
    m = sent[0][1]
    assert (m.folder_key, m.position, m.is_plugin) == ("/media/p4/Presets/My Presets", 5, False)


def _verifying_qc(loaded_name, blocks):
    """recall_verified against a device that echoes the key and loads `loaded_name`."""
    from qc_mcp import protocol as P
    from qc_mcp import transport as T
    qc, sent = _qc()
    qc._io_lock = __import__("threading").RLock()
    pointer = {"folder_key": FOLDER, "position": None, "is_factory": False,
               "is_downloads": False, "downloads_key": "", "is_plugin": True,
               "plugin_key": KEY}
    qc._read_pointer_reply = lambda rid, timeout_s: dict(pointer)
    qc.get_setlist_position = lambda: dict(pointer)
    bp = P.message_class("Grid")().preset
    bp.name = loaded_name
    ch = bp.chains.add()
    for _ in range(blocks):
        ch.models.add().hash = 14000
    qc.get_current_preset = lambda: bp
    return qc, T.QCError


def test_plugin_recall_that_loads_an_empty_preset_is_refused():
    """A key that is no file in the folder: the device echoes it, loads an empty preset."""
    qc, err = _verifying_qc("", 0)
    try:
        qc.recall_verified(plugin_key=KEY)
    except err as e:
        assert "empty preset" in str(e), e
    else:
        raise AssertionError("an empty preset was taken as the recalled plugin preset")


def test_plugin_recall_of_a_real_preset_passes():
    qc, _err = _verifying_qc("GiO Clean Bernard", 3)
    bp, pointer = qc.recall_verified(plugin_key=KEY)
    assert bp.name == "GiO Clean Bernard" and pointer["plugin_key"] == KEY


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
            passed += 1
    print(f"\n{passed}/{passed} passed")
