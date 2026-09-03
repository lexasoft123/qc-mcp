"""Offline checks that the MCP is self-describing (no device needed).

A client that never opens this repo sees only two things: the server's
`instructions` and the tool docstrings. These tests keep that surface honest —
in particular, that a version-gated capability can't be advertised without a
tool behind it, which is how `midi_clock_readout` once shipped as a promise.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from qc_mcp import protocol as P    # noqa: E402
from qc_mcp import server as S      # noqa: E402

TOOLS = {t.name: t for t in asyncio.run(S.mcp.list_tools())}
DOCS = " ".join((t.description or "") for t in TOOLS.values())


def test_every_tool_is_documented():
    thin = [name for name, t in TOOLS.items() if len((t.description or "").split()) < 8]
    assert not thin, f"tools with no usable description: {thin}"


def test_write_tools_say_so():
    """A caller must be able to tell a read from a write before calling it."""
    writes = ["add_block", "remove_block", "clear_grid", "set_parameter",
              "save_preset_as", "recall_preset", "assign_stomp", "unassign_stomp",
              "load_device_preset", "save_device_preset", "delete_device_preset",
              "load_settings_preset", "set_io_port"]
    missing = [w for w in writes
               if "WRITE" not in (TOOLS[w].description or "").upper()]
    assert not missing, f"write tools not marked WRITE: {missing}"


def test_every_gated_feature_has_a_tool():
    """No capability advertised by device_info without something using it."""
    users = {
        "model_presets": ("list_device_presets", "load_device_preset",
                          "save_device_preset", "delete_device_preset",
                          "list_settings_presets", "load_settings_preset"),
        "dual_footswitch": ("assign_stomp",),
        "favorites_by_type": ("list_favorites",),
        "midi_clock_readout": ("get_tempo",),
    }
    assert set(users) == set(P.FEATURES), (
        f"FEATURES changed — every gated capability needs a tool: "
        f"{set(P.FEATURES) ^ set(users)}")
    for feature, tools in users.items():
        present = [t for t in tools if t in TOOLS]
        assert present, f"{feature} is advertised but no tool implements it"


def test_destructive_global_writes_are_flagged():
    """Global settings survive a preset reload — callers must be warned."""
    for name in ("load_settings_preset", "set_io_port"):
        text = (TOOLS[name].description or "").lower()
        assert "global" in text or "hardware" in text, \
            f"{name} does not say it changes global/hardware state"
    assert "confirm" in (TOOLS["load_settings_preset"].description or "").lower()


def test_instructions_cover_the_safety_rules():
    text = (S.mcp.instructions or "").lower()
    for rule in ("slot safety", "list_empty_slots", "verify", "confirm"):
        assert rule in text, f"instructions never mention {rule!r}"


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
