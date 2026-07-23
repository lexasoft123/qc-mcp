# CLAUDE.md

MCP server that controls a **Neural DSP Quad Cortex** over its reverse-engineered
internal **USB-HID / protobuf** protocol (not MIDI). See `PROTOCOL.md` for the wire
protocol and `docs/DIRECTORY.md` for the preset/capture/IR catalog + scenes.

## Layout
- `src/qc_mcp/`
  - `iohid.py` — ctypes IOKit HID transport (input buffer must be report_size+1).
  - `bridge.py` — FIFO bridge: share Cortex Control's live session via the DYLD
    interposer (run MCP + the app at once). No handshake/heartbeat (the app owns them).
  - `protocol.py` — framing (128-byte reports, flag bits, 8-byte command trailer,
    gzip), `COMMANDS`, encode/decode, `Reassembler`; loads the protobuf descriptor pool.
  - `transport.py` — `QuadCortex`: open/handshake/heartbeat, read state, edit grid
    (add/delete block, params, per-scene params, splits/mixers, routing, bypass,
    captures, IRs), recall/save, list_directory.
  - `catalog.py` — `ModelRepo.xml` parser + value taper (log/linear, `to_norm`/
    `to_display`). Data attribution: neuraldsp.com/device-list.
  - `preset.py` — `describe(bp)` ⇄ `build(spec)` + `apply_spec` (spec ⇄ BinaryPreset).
  - `directory.py` — structure/search the on-device catalog (presets/IRs/captures).
  - `server.py` — FastMCP server (~29 tools). `_conn()` auto-detects bridge mode.
- `interceptor/` — DYLD interposer C + build/run scripts (capture + bridge). Logs and
  `catalog.json` are **gitignored** (contain library names / session ids).
- `tools/` — RE utilities; `tools/gui/` — GUI-automation harness + tests (below).
- `.claude/skills/` — reusable reverse-engineering skills.

## Running
- `python3` alone lacks pyobjc; use `.venv/bin/python`. GUI tools auto-reexec into `.venv`.
- Tests: `.venv/bin/python tests/test_directory.py` (offline, no device).
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
- **Bridge `get_current_preset` (a READ) is flaky/empty** after multi-block builds — the
  device *pushes* the full grid on **recall**; listen for that, or use `QC_BRIDGE=0`
  (direct mode, CC quit) for reliable read-back in tests.
- **Param values are a oneof** (int/float/string); preserve the active field (`preset._pv`,
  `set_param_typed`) or string params (cab mic names, capture `file_name`, IR path) drop.
- **Per-scene param**: assign to scenes (`params{index, scene_mode:true}`, no values), then
  per scene set active scene + write a plain value — it lands on the active scene.
- **Per-scene bypass** = the same, on the block's **bypass param (index 4)** (1.0=bypassed).
- **Captures**: block hash 14000(V1)/14001(V2) + param[5] `file_name`=`<64hex key><name>`;
  also list the key in the preset's `factory_/product_dependencies`.
- **Loading Downloads/Plugin presets** uses `key_in_downloads` (cloud_id) / plugin key, not
  folder+position.
- Value taper: `min>0 and max/min>=5` ⇒ power taper (k≈1.667), else linear.

## Conventions
- This is for interop/debugging on hardware you own + licensed software. Keep capture logs
  and the device's `catalog.json` out of git (they hold session ids / personal library
  names). Don't distribute re-signed app copies.
- On commits: end messages with the Co-Authored-By trailer; branch before committing to
  `main`; commit/push only when asked.
