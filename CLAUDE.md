# CLAUDE.md

MCP server that controls a **Neural DSP Quad Cortex** over its reverse-engineered
internal **USB-HID / protobuf** protocol (not MIDI). See `PROTOCOL.md` for the wire
protocol and `docs/DIRECTORY.md` for the preset/capture/IR catalog + scenes.
Supports **CorOS 4.0 and 4.1** — the schema is picked per connection from the
device's firmware (PROTOCOL.md §12).

## Layout
- `src/qc_mcp/`
  - `iohid.py` — ctypes IOKit HID transport (input buffer must be report_size+1).
  - `bridge.py` — FIFO bridge: share Cortex Control's live session via the DYLD
    interposer (run MCP + the app at once). No handshake/heartbeat (the app owns them).
  - `protocol.py` — framing (128-byte reports, flag bits, 8-byte command trailer,
    gzip), `COMMANDS`, encode/decode, `Reassembler`; **version negotiation**
    (`generation`/`set_version`/`supports`/`require`) + a descriptor pool per CorOS
    generation from `descriptors/qc_descriptors-<gen>.pb`.
  - `transport.py` — `QuadCortex`: open/handshake/heartbeat, read state, edit grid
    (add/delete block, params, per-scene params, splits/mixers, routing, bypass,
    captures, IRs), recall/save, list_directory.
  - `catalog.py` — `ModelRepo.xml` parser + value taper (log/linear, `to_norm`/
    `to_display`). Data attribution: neuraldsp.com/device-list.
  - `preset.py` — `describe(bp)` ⇄ `build(spec)` + `apply_spec` (spec ⇄ BinaryPreset).
  - `directory.py` — structure/search the on-device catalog (presets/IRs/captures).
  - `server.py` — FastMCP server (~30 tools). `connect(mode=auto|bridge|direct)`:
    when nothing is running it RETURNS the mode options (relay the question to the
    user); `mode='bridge'` self-launches `interceptor/run-bridge.sh` (~20s cold) and
    joins; `mode='direct'` needs `quit_app=True` if Cortex Control holds the device.
    Other tools' `_conn()` still auto-detects a running bridge.
- `interceptor/` — DYLD interposer C + build/run scripts (capture + bridge). Logs and
  `catalog.json` are **gitignored** (contain library names / session ids).
- `tools/` — RE utilities; `tools/gui/` — GUI-automation harness + tests (below).
  After a CorOS update run all three: `interceptor/build.sh` (re-instrument the
  updated app), `tools/build_descriptors.py build <gen>` (new wire schema),
  `tools/dump_model_repo.py --diff` then without `--diff` (new device catalog).
- `.claude/skills/` — reusable reverse-engineering skills.

## Running
- `python3` alone lacks pyobjc; use `.venv/bin/python`. GUI tools auto-reexec into `.venv`.
- Tests: `.venv/bin/python tests/test_directory.py` and
  `tests/test_protocol_versions.py` (both offline, no device).
- Device/GUI tools need the instrumented Cortex Control running (bridge) — see
  `interceptor/run-bridge.sh`.

## GUI harness + tests (`tools/gui/`)
Drives Cortex Control (screenshot + click) and correlates the interposer protocol log.
Needs **Claude.app** granted Screen Recording + Accessibility (macOS TCC).
- `gui.py` — `bounds`/`home`/`shot`/`click`/`type`/`key`/`act`/`decode`. **`home`
  first** — clicks only map on the main Retina display (see `drive-gui-correlate-protocol`).
- `mine_log.py` / `dump_catalog.py` — decode captured traffic, build a catalog snapshot.
- `sweep_presets.py` — load every preset in a folder, check the decoder handles it.
- `roundtrip_test.py [--device]` — deep golden round-trip over real presets (every field).
- `e2e_test.py` — prompt → built chain → assert. `test_fender_scenes.py` — scenes demo.

## Gotchas (bite you if forgotten)
- **Read vs delta indexing**: in a full read, array *position* is the index; the id/
  column/index *fields* are 0. In edits, set the fields. `apply_spec` uses position.
- **Grid whole-preset UPDATE merges** (doesn't replace) — build incrementally / clear first.
  And **`clear_grid` needs working reads** (it deletes what it reads); if reads are dead it
  clears nothing and the next build merges onto stale state (ghost/duplicate blocks).
- **Bridge reads are reliable now** (were flaky). Fixed in `bridge.py`/`transport.py`:
  out FIFO is **O_RDWR** (reader never hits EOF when the app blinks) + **self-healing**
  reader thread; reads correlate on **`request_id`** (device echoes it; our ids use a high
  base to dodge the app's). Streamed telemetry (CPULoad) is `request_id=0` broadcast → take
  the **latest**, not the first buffered. If a session goes stale, `disconnect`→`connect`
  revives it (`get_current_preset` also auto-reconnects+retries once).
- **Saving = File CREATE, never RecallPreset SAVE.** The app saves via `File{action=CREATE,
  folder{key, files{index=<position>, name}}}` (cmd 4, **no** preset_payload — device commits
  its live working grid). *RecallPreset UPDATE reason=SAVE* **hangs the device** on an empty/
  Unsaved slot (needs a reboot). `save_preset`/`save_preset_as` now both use File CREATE.
  Success string ≠ commit: verify via `current_preset_position` + app header name w/o `*`.
- **`add_block` de-dupes**: re-adding a hash already on that row is a no-op — to move a
  block, delete first (or use a different hash). Verify every build (read `split_points` +
  screenshot); orphaned amps still draw CPU but make no sound.
- **Routing/parallel** (see `build-preset-routing` skill): `in=1`/`out=19`(Multi Out)/
  `out=16`(mix bus); split `{split_col,mix_col}` is **1→2** (nest for 3+ amps via a relay
  row). Split **after** shared blocks (`split_col` = first non-shared column). Post-merge
  FX go **after `mix_col`**. Stereo = **pan branch lanes** (LaneOutputControl PAN=idx 1;
  0=L,.5=C,1=R) — not the output row. Directory: the live listing (one `File` READ →
  device streams the whole catalog, ~12s) **works in bridge mode too** — on a fresh
  clone just call `directory_summary(refresh=True)`; it auto-saves the gitignored
  `interceptor/catalog.json` snapshot (fallback for interrupted reads; the tools return
  this hint when `source == "empty"`). `run-bridge.sh` now sets `QC_VERBOSE=1` so the
  frame log the GUI tools need is always written.
- **Param values are a oneof** (int/float/string); preserve the active field (`preset._pv`,
  `set_param_typed`) or string params (cab mic names, capture `file_name`, IR path) drop.
- **Per-scene param**: assign to scenes (`params{index, scene_mode:true}`, no values), then
  per scene set active scene + write a plain value — it lands on the active scene.
- **Per-scene bypass** = the same, on the block's **bypass param (index 4)** (1.0=bypassed).
  Verified on drives/amps; **silent no-op on Delay blocks** (trails-capable → different
  bypass path? pending a capture of the app's toggle) — scene the delay's **MIX** instead
  (0=off, preserves trails). And **scene switches must be confirmed** (Scene READ) before
  writing a scene value — a fixed sleep races the device and drops values (`_await_scene`).
- **Scene labels/colors = dedicated `SceneLabel`(23)/`SceneColor`(48) UPDATEs** `{index,
  label|color}` — a Grid UPDATE with preset-level `scene_labels[]` is a silent no-op.
  Preset **name** is set by the save (File CREATE), not settable on the live grid.
  Unlabeled scenes with data show "Undefined" on-device.
- **Captures**: block hash 14000(V1)/14001(V2) + param[5] `file_name`=`<64hex key><name>`;
  also list the key in the preset's `factory_/product_dependencies`.
- **Loading Downloads/Plugin presets** uses `key_in_downloads` (cloud_id) / plugin key, not
  folder+position. And **recalls REQUIRE `folder_key`** — a folderless SetlistPosition
  UPDATE is silently refused (device echoes the unchanged position back). `recall_preset`
  now defaults to the current folder and verifies the position actually moved.
- Value taper: `min>0 and max/min>=5` ⇒ power taper (k≈1.667), else linear.
- **One bridge reader at a time.** The out FIFO is a single stream: if the MCP
  server holds a bridge connection and a script opens another, they steal each
  other's frames and reads silently return `None` (telemetry still flows, so it
  looks like a dead session). `disconnect` the MCP before driving the device from
  a script.
- **CorOS version matters.** `connect`/`device_info` report `firmware` +
  `protocol_generation`; 4.1-only tools gate on `P.require(...)`. The device's
  human version is in `Version.zenos_git_hash` — `app_fw_version` is a build hash.
- **Device presets (4.1)**: `list_device_presets` / `load_device_preset`. Loading
  is a **Grid UPDATE with `update_type=MODEL_PRESET`**, not a ModelPreset write.
  *Saving* a user device preset is NOT reversed yet (as with stomp assignment).

## Conventions
- This is for interop/debugging on hardware you own + licensed software. Keep capture logs
  and the device's `catalog.json` out of git (they hold session ids / personal library
  names). Don't distribute re-signed app copies.
- On commits: end messages with the Co-Authored-By trailer; branch before committing to
  `main`; commit/push only when asked.
