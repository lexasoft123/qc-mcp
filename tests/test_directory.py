"""Offline unit tests for qc_mcp.directory — structuring + search of the DIRECTORY
catalog. Uses lightweight fakes mimicking decoded File protobuf messages, so it needs
no device, no log, and no protobuf."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from qc_mcp import directory  # noqa: E402


class _F:
    def __init__(self, index, name, key, author="", coros_version="",
                 instrument=0, is_readonly=False, date_ms_since_epoch=0):
        self.index = index; self.name = name; self.key = key
        self.author = author; self.coros_version = coros_version
        self.instrument = instrument; self.is_readonly = is_readonly
        self.date_ms_since_epoch = date_ms_since_epoch


class _Folder:
    def __init__(self, key, name, files, is_factory=False, is_downloads=False,
                 is_user_default=False):
        self.key = key; self.name = name; self.files = files
        self.is_factory = is_factory; self.is_downloads = is_downloads
        self.is_user_default = is_user_default


class _Msg:
    def __init__(self, type, folder):
        self.type = type; self.folder = folder

    def HasField(self, name):
        return name == "folder" and self.folder is not None


def _catalog():
    msgs = [
        _Msg(0, _Folder("/media/p4/Presets/My Presets", "My Presets",
                        [_F(0, "Clean Tone", "a.pb", author="TestUser"),
                         _F(1, "Lead Tone", "b.pb", author="TestUser")])),
        _Msg(0, _Folder("/opt/neuraldsp/Factory Library", "Factory Library",
                        [_F(0, "Factory Clean", "c.pb")], is_factory=True)),
        _Msg(2, _Folder("1_q", "My Captures",
                        [_F(0, "Test Amp Capture", "deadbeef", instrument=1)])),
        _Msg(1, _Folder("local_ir_root", "IRs Library",
                        [_F(0, "YA MES 212 V30 Mix 13", "CIR_abc")])),
    ]
    return directory.structure_directory(msgs)


def test_structure_segments_by_type():
    cat = _catalog()
    assert {"presets", "irs", "captures"} <= set(cat)
    assert len(cat["presets"]) == 2
    assert len(cat["captures"]) == 1
    assert len(cat["irs"]) == 1


def test_user_folders_sort_before_factory():
    cat = _catalog()
    # is_factory False sorts first
    assert cat["presets"][0]["name"] == "My Presets"
    assert cat["presets"][-1]["is_factory"] is True


def test_counts():
    c = directory.counts(_catalog())
    assert c["presets"] == {"folders": 2, "files": 3}
    assert c["captures"] == {"folders": 1, "files": 1}
    assert c["irs"] == {"folders": 1, "files": 1}


def test_search_preset_gives_load_pointer():
    cat = _catalog()
    hits = directory.search(cat, "Clean Tone", "presets")
    assert len(hits) == 1
    h = hits[0]
    assert h["folder_key"] == "/media/p4/Presets/My Presets"
    assert h["index"] == 0          # -> SetlistPosition position
    assert h["is_factory"] is False


def test_position_comes_from_array_order_not_the_index_field():
    """A whole-folder READ leaves every `index` field 0 — the slot is the file's
    place in the array. A setlist is a fixed 256-slot table (empty slots carry a
    blank name), so this is what makes a recall land on the right preset."""
    msgs = [_Msg(0, _Folder("/media/p4/Presets/My Presets", "My Presets",
                            [_F(0, "", "s0.pb"),          # empty slot 0
                             _F(0, "Second", "s1.pb"),    # device sends index=0…
                             _F(0, "Third", "s2.pb")]))]  # …for every one of them
    files = directory.structure_directory(msgs)["presets"][0]["files"]
    assert [f["index"] for f in files] == [0, 1, 2]
    hits = directory.search({"presets": [{"key": "k", "name": "n", "is_factory": False,
                                          "files": files}]}, "Third", "presets")
    assert hits[0]["index"] == 2


def test_search_capture_by_folder_name_and_key():
    cat = _catalog()
    hits = directory.search(cat, "Test Amp", "captures")
    assert hits and hits[0]["key"] == "deadbeef"
    assert hits[0]["instrument"] == 1


def test_search_all_categories_and_limit():
    cat = _catalog()
    assert len(directory.search(cat, "", limit=3)) == 3       # 6 files, capped at 3
    assert {h["category"] for h in directory.search(cat, "")} == {"presets", "irs", "captures"}


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
