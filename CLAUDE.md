# CLAUDE.md

MCP server that controls a **Neural DSP Quad Cortex** over its reverse-engineered
internal **USB-HID / protobuf** protocol (not MIDI). See `PROTOCOL.md` for the wire
protocol, `docs/DIRECTORY.md` for the preset/capture/IR catalog + scenes,
and `docs/COROS-4.1.md` for the 4.1 feature set (device presets, stomps,
Global EQ / I/O presets) with usage examples.
Supports **CorOS 4.0 and 4.1** — the schema is picked per connection from the
device's firmware (PROTOCOL.md §12) — on **macOS and Windows** (`docs/WINDOWS.md`).

## Layout
- `src/qc_mcp/`
  - `backend.py` — picks the HID transport for the OS (`open_hid`) and answers
    `direct_supported`/`bridge_supported`. All three backends below implement the
    same four methods: `open`/`set_report`/`read_reports`/`close`.
  - `iohid.py` — **macOS**: ctypes IOKit HID transport (input buffer must be
    report_size+1).
  - `winhid.py` — **Windows**: ctypes setupapi + hid.dll. Overlapped I/O; picks
    the collection with 129-byte reports; raises the driver's 32-report input
    queue to 512 (a directory dump overruns the default).
  - `bridge.py` — FIFO bridge: share Cortex Control's live session via the DYLD
    interposer (run MCP + the app at once). No handshake/heartbeat (the app owns them).
    **macOS only** — importing it elsewhere raises with that message.
  - `protocol.py` — framing (128-byte reports, flag bits, 8-byte command trailer,
    gzip), `COMMANDS`, encode/decode, `Reassembler`; **version negotiation**
    (`generation`/`set_version`/`supports`/`require`) + a descriptor pool per CorOS
    generation from `descriptors/qc_descriptors-<gen>.pb`.
  - `transport.py` — `QuadCortex`: open/handshake/heartbeat, read state, edit grid
    (add/delete block, params, per-scene params, splits/mixers, routing, bypass,
    captures, IRs), recall/save, list_directory.
  - `catalog.py` — `ModelRepo.xml` parser + value taper (log/linear, `to_norm`/
    `to_display`). `SYMBOLIC` resolves the ranges the XML leaves as names
    (`MIN_MIXER_DB` = -40, `MAX_MIXER_DB` = +12, calibrated against the app —
    only add a name once measured). Data attribution: neuraldsp.com/device-list.
  - `preset.py` — `describe(bp)` ⇄ `build(spec)` + `apply_spec` (spec ⇄ BinaryPreset).
  - `directory.py` — structure/search the on-device catalog (presets/IRs/captures).
    A file's slot is its **array position** (the `index` field is 0 in a whole
    read), so a setlist's 256 entries map straight to recall positions.
  - `leveling.py` — the preset-leveling bench Patchbay's Leveling view drives
    (`qc-mcp --leveling --socket …`, newline-JSON on stdio, attaches to the
    daemon like any other client). Reads/writes LaneOutputControl VOLUME in dB
    and streams `IOMeter`.
  - `server.py` — FastMCP server (~30 tools). `connect(mode=auto|bridge|direct)`:
    when nothing is running it RETURNS the mode options (relay the question to the
    user); `mode='bridge'` self-launches `interceptor/run-bridge.sh` (~20s cold) and
    joins; `mode='direct'` needs `quit_app=True` if Cortex Control holds the device.
    Other tools' `_conn()` still auto-detects a running bridge. Where bridge mode
    can't run, `auto` goes straight to direct instead of asking.
- `interceptor/` — DYLD interposer C + build/run scripts (capture + bridge). Logs and
  `catalog.json` are **gitignored** (contain library names / session ids).
- `tools/` — RE utilities (mostly macOS: they shell out to otool/codesign);
  `tools/win_hid_check.py` diagnoses a Windows setup (enumerate → open → round-trip,
  distinct exit codes per failure). `tools/gui/` — GUI-automation harness (below).
  After a CorOS update run all three: `interceptor/build.sh` (re-instrument the
  updated app), `tools/build_descriptors.py build <gen>` (new wire schema),
  `tools/dump_model_repo.py --diff` then without `--diff` (new device catalog).
- `.claude/skills/` — reusable reverse-engineering skills.

## Running
- `python3` alone lacks pyobjc; use `.venv/bin/python`. GUI tools auto-reexec into `.venv`.
- Tests (all offline, no device): `.venv/bin/python tests/test_directory.py`,
  `tests/test_protocol_versions.py`, `tests/test_tool_docs.py` (keeps the MCP
  self-describing — every gated feature must have a tool behind it),
  `tests/test_platform.py` (keeps the macOS and Windows backends interchangeable;
  it's the only check on `winhid.py` from a Mac).
- Device/GUI tools need the instrumented Cortex Control running (bridge) — see
  `interceptor/run-bridge.sh`.

## GUI harness + tests (`tools/gui/`)
Drives Cortex Control (screenshot + click) and correlates the interposer protocol log.
Needs **Claude.app** granted Screen Recording + Accessibility (macOS TCC).
**Capture and reads no longer touch the screen:** `shot` uses `screencapture -l
<winid>` (renders that window alone, occluded or parked off-screen), and `ax`
reads JUCE's accessibility tree — labelled controls, live values, exact frames,
no focus. Only clicking needs the screen (JUCE ignores AXPress and
CGEventPostToPid): `press "<name>"` borrows focus for ~1s and hands it back.
`park`/`home` move the window off every display and back.
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
- **The instrumented copy must keep the ORIGINAL's entitlements.** `build.sh`
  reads them off the source app now and adds only the four injection needs on
  top; it used to carry a hand-written "from the original app" list that had
  drifted and was silently dropping `automation.apple-events` and
  `scripting-targets`. It verifies nothing was lost, so the drift cannot come
  back quietly. Use **PlistBuddy, not plutil**, to add these keys: plutil reads
  `.` as a key-PATH separator, so `com.apple.security.cs.*` parses as five
  nested dicts and every insert fails with "Key path not found" — which strips
  exactly the entitlements injection depends on.
- **Captures**: block hash 14000(V1)/14001(V2) + param[5] `file_name`=`<64hex key><name>`;
  also list the key in the preset's `factory_/product_dependencies`.
- **Loading Downloads/Plugin presets** uses `key_in_downloads` (cloud_id) / plugin key, not
  folder+position. And **recalls REQUIRE `folder_key`** — a folderless SetlistPosition
  UPDATE is silently refused (device echoes the unchanged position back). `recall_preset`
  now defaults to the current folder and verifies the position actually moved.
- Value taper: `min>0 and max/min>=5` ⇒ power taper (k≈1.667), else linear. Ranges
  that are symbolic names in the XML need a calibrated entry in `catalog.SYMBOLIC`
  or they fall through unconverted (raw 0-1).
- **Lane output level** = `LaneOutputControl`(23000) param 0 VOLUME, **-40..+12 dB**
  linear (0 dB = 0.769230783). It lives in `Chain.output_control`, so it is stored
  in the preset — the right knob for balancing presets against each other. PAN is
  idx 1, shown as 50L..C..50R = `(nv-0.5)*100`.
- **`IOMeter`(5) streams at ~4 Hz** after a bare `IOMeter` CREATE, `request_id=0`
  (take the latest). Values are **linear amplitude 0..1, not dB**. One bridge-mode
  session produced no frames at all — treat "no reading" as a resting state.
- **One bridge reader at a time.** The out FIFO is a single stream: if the MCP
  server holds a bridge connection and a script opens another, they steal each
  other's frames and reads silently return `None` (telemetry still flows, so it
  looks like a dead session). `disconnect` the MCP before driving the device from
  a script.
- **CorOS version matters.** `connect`/`device_info` report `firmware` +
  `protocol_generation`; 4.1-only tools gate on `P.require(...)`. The device's
  human version is in `Version.zenos_git_hash` — `app_fw_version` is a build hash.
- **Device presets (4.1)**: `list_device_presets` / `load_device_preset` /
  `save_device_preset` / `delete_device_preset`. Loading is a **Grid UPDATE with
  `update_type=MODEL_PRESET`**, not a ModelPreset write. Saving needs the device
  to know which block you mean: `select_model_slot(row, col)` first (a
  `ModelPreset` with **no action field** + `loaded_row`/`loaded_column`) — without
  it every write is silently ignored. The device also **refuses a save whose
  params match an existing preset** ("Preset Conflict"); tweak something first.
- **Settings presets (4.1)**: Global EQ and I/O Settings are device presets on
  pseudo-models (`4004` Output Equalizer / `31000` IOSettings) applied through
  their OWN message (`GlobalEQ.model_preset_to_load` / `IOSettings.preset_to_load`).
  Both overwrite **global** state; a Global EQ load is reversible (read all 28
  params first, write them back), an I/O load is not — `load_settings_preset`
  snapshots and demands `confirm=True`.
- **Writing I/O settings**: send ONLY the fields you're changing. A full port
  record (every field, e.g. a protobuf `CopyFrom`) is silently rejected — that's
  why I/O writes look impossible. `input_type` is 3-position: 0=Instrument,
  0.5=Mic, 1.0=Line (the app only offers the first two).
- **Stomp assignments live on `Grid`**, not their own message: Grid UPDATE with
  `preset.stomp_mode_assignments[]{row, column, stomp_index, type}` (A-H = 0-7;
  `type` PRIMARY/SECONDARY = the 4.1 dual-footswitch). DELETE unassigns.
  **Latching/momentary must be its own Grid UPDATE** (`stomp_is_momentary` map) —
  sent alongside an assignment it's overwritten by the device's echo.
  A block holds one assignment **per kind** (verified: Vintage Digital on E
  PRIMARY + F SECONDARY at once). The device does NOT guard SECONDARY — it
  accepts it on blocks with no second function, silently wasting a switch.
  MCP: `assign_stomp` / `unassign_stomp`.

## Platform split (macOS vs Windows)
- **Direct mode and running-alongside-the-app work on both**, by different means:
  macOS injects a dylib and shares the app's session; **Windows just opens a
  second NON-exclusive handle** (`QuadCortex(share=True)`) because the HID stack
  copies every input report to every open handle, and Cortex Control opens with
  `FILE_SHARE_READ|WRITE`. Only `tools/gui/` is still macOS-only. Don't add a
  `sys.platform` test in the tools — ask `backend.bridge_supported()` /
  `direct_supported()` so there's one place to change.
- **Shared mode has two independent writers on one endpoint.** Single-report
  messages are atomic (~97% of the app's traffic), multi-report ones can
  interleave — `connect()` returns a `caution` saying so. Build/save presets in
  direct mode with the app quit.
- **Windows `WriteFile` returns ERROR_GEN_FAILURE(31) constantly on writes that
  DO land** (60 of them in a session that provably wrote) — it is the Windows
  `0xe0005000`, listed in `WinHIDTransport.BENIGN_WRITE_CODES`. Never judge a
  write by it; read the value back. And test writes with a *continuous* param —
  amp param 0 `INPUT` is a discrete selector that clamps and mimics a lost write.
- **`interceptor-win/` is capture-only**: the IAT hooks mirror both directions
  fine; injection is unverified (the earlier ERROR_GEN_FAILURE diagnosis was
  wrong, so it is simply open). `winbridge.py` is its tested-but-unused client.
- Windows quirks that look like protocol bugs: input reports are **padded** to 129
  bytes (the frame's own `chunkLen` is the truth); writes must be **exactly**
  `OutputReportByteLength` including the report id; the handle is overlapped so
  every read/write needs an `OVERLAPPED`.
- **`winhid.py` imports on any OS** (plain ctypes types, DLLs bound lazily in
  `_load()`) so `tests/test_platform.py` can check it from a Mac. Keep it that way.
- **A HID device held exclusively vanishes from enumeration** — Windows refuses
  even a zero-access probe open, so `enumerate_devices` recovers the ids from the
  interface path and marks it `busy`; without that, "quit Cortex Control" gets
  misreported as "no device found". Verified on hardware.
- **Windows-facing runtime strings are ASCII** (no em-dashes): a legacy console
  codepage renders them as `?`. Docstrings/comments are exempt.
- Verified on Win10 22H2 x64 + CorOS 4.1.0: reads, the 8336-capture directory
  stream, open/close cycles, and the device-busy error. **Writes not yet run from
  Windows** (same `set_report` path, so no untested Windows code — but untried).

## Conventions
- This is for interop/debugging on hardware you own + licensed software. Keep capture logs
  and the device's `catalog.json` out of git (they hold session ids / personal library
  names). Don't distribute re-signed app copies.
- On commits: end messages with the Co-Authored-By trailer; branch before committing to
  `main`; commit/push only when asked.
