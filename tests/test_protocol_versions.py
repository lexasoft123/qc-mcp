"""Offline checks for the CorOS version negotiation (no device needed).

The wire schema differs between CorOS generations — 4.1 reuses GlobalEQ field 5
for something new and adds ModelPreset/RemoteControl — so the MCP ships one
descriptor set per generation and picks from the connected firmware.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from qc_mcp import protocol as P  # noqa: E402


def test_parse_and_generation():
    assert P.parse_version("4.1.0") == (4, 1, 0)
    assert P.parse_version("") == ()
    assert P.generation("4.0.1") == "4.0"
    assert P.generation("4.1.0") == "4.1"
    # unknown-but-newer firmware uses the newest schema we ship
    assert P.generation("4.9.2") == P.LATEST_VERSION
    # a build hash isn't a version: fall back rather than mis-detect
    assert P.generation("d14e") == P.LATEST_VERSION


def test_every_declared_generation_has_a_descriptor_set():
    for version in P.PROTOCOL_VERSIONS:
        assert os.path.exists(P.descriptor_path(version)), version
        assert P.pool(version).FindMessageTypeByName(f"{P.PACKAGE}.GridMessage")


def test_new_commands_are_gated_to_their_generation():
    assert 71 not in P.commands("4.0") and 71 in P.commands("4.1")
    assert 72 not in P.commands("4.0") and 72 in P.commands("4.1")
    # ...and the 4.0 pool genuinely lacks the message, so a stale schema can't
    # silently encode something the device won't understand.
    try:
        P.message_class("ModelPreset", "4.0")
        raise AssertionError("4.0 pool should not define ModelPresetMessage")
    except KeyError:
        pass


def test_feature_gating_reports_the_needed_release():
    P.set_version("4.0.1")
    assert not P.supports("model_presets")
    assert "4.1" in P.require("model_presets", "Device presets")
    P.set_version("4.1.0")
    assert P.supports("model_presets") and P.require("model_presets") is None


def test_unknown_feature_key_raises_instead_of_passing():
    """A typo'd gate must not sail through: parse_version() returns () for a
    non-version string, and every version compares >= (), so the check would
    silently pass and fail later where the message is built."""
    P.set_version("4.0.1")
    for bogus in ("model_preset", "dual-footswitch", "typo"):
        try:
            P.supports(bogus)
            raise AssertionError(f"supports({bogus!r}) should raise")
        except KeyError:
            pass
    assert P.supports("4.0") and not P.supports("4.1")   # bare versions still work


def test_globaleq_field_5_differs_between_generations():
    """The one genuinely incompatible change — same field number, new meaning."""
    names = {}
    for version in ("4.0", "4.1"):
        desc = P.pool(version).FindMessageTypeByName(f"{P.PACKAGE}.GlobalEQMessage")
        names[version] = desc.fields_by_number[5].name
    assert names["4.0"] == "has_user_defaults"
    assert names["4.1"] == "model_preset_to_load"


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    ok = 0
    for fn in fns:
        try:
            fn(); ok += 1; print(f"PASS {fn.__name__}")
        except Exception:
            print(f"FAIL {fn.__name__}"); traceback.print_exc()
    P.set_version(P.LATEST_VERSION + ".0")
    print(f"\n{ok}/{len(fns)} passed")
    sys.exit(0 if ok == len(fns) else 1)
