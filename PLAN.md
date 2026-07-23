# Plan — preset-building capability (build multiamp-class presets)

## 🎯 GOAL
**Recreate an SRV-multiamp-like preset from a natural-language prompt.** i.e. Claude
takes a request ("dual-amp SRV rig: clean Fender + driven Dumble blended in parallel,
TS808 in front, spring reverb") and builds it on the device — picking amps/cabs from
the catalog, laying out parallel chains with splitters/mixers + routing, setting
params, and saving. The MCP must expose enough building primitives for this.

Sub-goal: build presets like **SRV multiamp** (4 parallel amp chains, splitters/mixers,
per-scene params, per-scene bypass, routing) from the MCP, and save them.

Strategy: model a full `BinaryPreset` in code (a builder), validate it offline by
round-tripping the real SRV multiamp, then add the transport to **apply + save** it.
Cortex Control edits incrementally then saves, so we support both an incremental API
and a whole-preset apply, and use whichever the device accepts.

## Phases

- **P0 — Preset model & builder** (`preset.py`, offline). Spec ⇄ BinaryPreset:
  chains, blocks (models w/ per-scene params), splitters/mixers,
  `split_control_points`, `in_portid`/`out_portid`, bypass (incl. per-scene),
  metadata. Validate: `describe(build(describe(srv))) == describe(srv)`.
- **P1 — Write & save transport**. `save_preset` (RecallPreset UPDATE reason=SAVE),
  `load_preset` (apply full BinaryPreset), `write_preset_file`
  (FileMessage CREATE preset_payload). Decide upload-vs-incremental from what works.
- **P2 — Per-scene parameters**. `set_param(..., scene=)` + set-all-scenes; scene-aware
  read. (Also closes the earlier scene-0 refinement.)
- **P3 — Splitters, mixers, routing**. place splitter/mixer, set split points + routing.
- **P4 — Bypass** (incl. per-scene) via Bypass/ColBypass/SceneBypass.
- **P5 — MCP tools + reproduce SRV multiamp** end-to-end, verify by read-back.

## Status
- **P0 — model/builder: DONE.** `preset.py` (describe/build/PresetBuilder). Round-trips
  SRV multiamp faithfully on all musical fields; unmodeled fields are QC-managed
  metadata (hash/date/versions/`layout_code`/`recompile_code`/MIDI/author).
- **P1 — write/save: DONE.** transport: `load_preset` (Grid UPDATE — **merges**, doesn't
  replace), `save_preset` (RecallPreset SAVE), `write_preset_file` (FileMessage),
  `delete_block` (Grid DELETE — works), `clear_grid`, `apply_spec`. Verified
  clear→rebuild restores a preset exactly.
- **Architecture decided: build INCREMENTALLY** (clear → add blocks → params →
  [splitters/routing/bypass] → save). Whole-preset upload only merges.
- **Key encoding fact:** in the full-preset READ, row = chain-array index, column =
  models-array index, param = params-array index (the `.row/.column/.index` *fields*
  are used only in delta edits).
- **P2 — per-scene params: PARTIAL.** Global params fully work. Per-scene values need a
  separate "enable scene-mode for this param" step (app's tap-hold→assign); sending 8
  values in one message, or switch+set, both leave the param global. TODO: find the
  scene-enable trigger.
- **P3 — splitters/mixers/routing: DONE.** `set_routing`, `add_splitter` (+split_points),
  `add_mixer`, `set_lane_param`. `apply_spec` now emits routing + splitters/mixers +
  blocks + ALL sub-block params (splitter/mixer/input_control/output_control).
- **✅ SRV multiamp STATIC content reproduced EXACTLY** (clear → apply_spec): all 4
  parallel chains, blocks/positions, routing (in/out portid), splitters/mixers +
  split points, and every block + splitter + mixer + input/output param — 0 mismatches.
- **P5/P6 — MCP tools: DONE (static).** `build_preset(spec)` (whole preset from a
  prompt), `add_block`, `remove_block`, `set_parameter` (display units), `clear_grid`,
  `save_preset`, plus existing find_devices/switch_*/get_*. 18 tools total.
- **REMAINING (scenes/stomps frontier):**
  - **P2 per-scene params** — needs the "enable scene-mode for a param" trigger (still
    unsolved; global params work).
  - **P4 per-scene bypass** — `BinaryPreset.bypass → ColBypass{sceneMode}`; mechanism TBD.
  - **Stomp assignments** — `stomp_mode_assignments{column, stomp_index}`,
    `stomp_is_momentary`; mechanism TBD.
  SRV uses these minimally (1 scene-param, 1 scene-bypass col, 1 stomp), so static
  reproduction is ~complete; scene/stomp assignment is the last feature area.
