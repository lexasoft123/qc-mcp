# Quad Cortex ↔ Cortex Control — reverse-engineered control protocol

Status: **fully reverse-engineered and working** — transport, framing, session,
and the message layer for presets, scenes, modes, the signal-chain grid, hardware
I/O, and the device catalog. Read and write both work against a live device.

Everything here was derived by inspecting a connected Quad Cortex and the
`Cortex Control.app` binary on macOS — no Neural DSP source or docs. Covers
**CorOS 4.0.1 and 4.1.0**; where the two differ the release is called out, and
§12 explains how the MCP negotiates between them. Unofficial; not affiliated
with Neural DSP.

---

## 1. Transport — USB-HID

- Device: **Neural DSP Quad Cortex**, USB `VID 0x152A` / `PID 0x880A`, composite
  (class 0xEF/IAD): USB-audio interfaces + a **HID interface** (interface 5).
- Control is **not** MIDI, not raw bulk. Cortex Control links `IOKit` and uses the
  **IOHIDManager** API (`IOHIDDeviceSetReport` for host→device, an input-report
  callback for device→host). We replicate this via ctypes in `iohid.py`.
- HID report descriptor (interface 5):
  - **Report ID 1** — Input, **128 bytes** (device → host)
  - **Report ID 2** — Output, **128 bytes** (host → device)
- Access is exclusive: the device is opened with **seize**
  (`IOHIDOptionsTypeSeizeDevice`). Only one app at a time — **Cortex Control must be
  quit** while the MCP is connected, and vice-versa.
- macOS quirk: `IOHIDDeviceSetReport` returns a **benign `0xe0005000`** on this
  device — the official app ignores it too, and the data is still sent. Not an error.
- The input-report callback buffer **must** be 129 bytes (report id + 128); a
  128-byte buffer silently truncates the last payload byte of every full chunk.

---

## 2. Framing

### 2a. HID report chunking
Each 128-byte report buffer (the report-id byte is included by the app / hidapi):
```
byte 0      : report id   (0x02 = host→QC output, 0x01 = QC→host input)
byte 1      : chunkLen    (0..126 payload bytes carried in this report)
byte 2      : flags       0x40=FIRST  0x00=MIDDLE  0x80=LAST  0xC0=SINGLE
byte 3..    : payload      (rest zero-padded to 128)
```
Reassemble a logical message by concatenating `payload` across FIRST..LAST.

### 2b. Message layout
```
message = <protobuf payload>  +  <8-byte trailer>
trailer = [command : uint16 LE]   // CortexMessageType.Enum
          [reserved: uint32 = 0]
          [hash    : uint16]       // payload_hash; 0 on host→QC requests
```
Large payloads gzip the **entire** protobuf before the trailer (`1f 8b 08 …`); the
receiver auto-detects by magic. Sub-fields (e.g. a `BinaryPreset`) can themselves be
a gzip blob inside a protobuf `bytes` field.

Implemented in `protocol.py`: `encode_message`, `decode_message` (auto-gunzip),
`message_to_reports`, `Reassembler`.

---

## 3. Session lifecycle — the critical part

The QC answers **identity** requests (Version) from any session, but it only
**streams state, answers most READs, and pushes the current preset** once the client
looks like Cortex Control:

1. `ResetCommsBuffers` UPDATE with a `session_id`.
2. `Version` READ, then `Version` UPDATE announcing `cortex_control_version = "4.0.1"`.
3. `Connection` UPDATE `connected = true`.
4. A burst of state READs (ModelRepo, ModuleStats, IOSettings, Mode, GlobalTempo,
   Scene, SetlistPosition, …).
5. **A steady `KeepAlive` heartbeat** — `{action: UPDATE, is_online: true}` roughly
   every 200 ms. An *empty* KeepAlive does **not** count; without a proper heartbeat
   the QC treats the session as inactive and stays silent.

With the heartbeat running, `RecallPreset` READ returns the full current preset —
exactly what Cortex Control gets on boot. `transport.py` runs the heartbeat on a
background thread and serializes all sends behind a lock (concurrent sends from the
heartbeat + main thread will corrupt frames otherwise).

**Writes** (grid edits, scene/preset/mode/volume) land regardless of the heartbeat;
**reads** require it.

---

## 4. Command map — `CortexMessageType.Enum`

The trailer's `command` selects the payload message type. 71 values in CorOS 4.0,
**73 in 4.1** (`71 ModelPreset`, `72 RemoteControl`). The ones the MCP uses:

| id | message | id | message |
|----|---------|----|---------|
| 1  | Grid (add/move/param blocks) | 26 | CPULoad |
| 2  | SetlistPosition (recall preset) | 32 | KeepAlive |
| 3  | IOSettings | 33 | GlobalTempo |
| 6  | Tuner | 48 | SceneColor |
| 10 | Version | 49 | Connection |
| 12 | GridMove | 51 | ModelRepo (device catalog) |
| 13 | Scene | 52 | ResetCommsBuffers |
| 14 | Mode | 15 | RecallPreset |
| 17 | MasterVolume | 22/23 | SceneCopy / SceneLabel |
| 20 | RecentsFavorites | 71 | ModelPreset *(4.1)* |

Full list in `proto/ProductionAutomation.proto` → `CortexMessageType`. Every message
has field 1 = `action` (`MessageAction.Enum`: CREATE=0, UPDATE=1, DELETE=2, READ=3,
MOVE=4, COPY=5, SWAP=8) and field 2 = `request_id`, so **the same message reads and
writes** — send READ to query, UPDATE to change.

---

## 5. The signal-chain grid

### Data model (`Preset.proto`)
```
BinaryPreset { name, tempo, volume, pan, default_scene,
               chains[], bypass[], scene_labels[], scene_colors[], … }
Chain  { in_portid, out_portid, row,
         models[],           // the fx blocks in this lane
         input_control[],    // the lane input block  (Input Gate Control)
         output_control[],   // the lane output block (Lane Output Control)
         splitter[], mixer[], combined_splitter[], … }
Model  { hash, column, params[], bypass_expression[], … }   // one block
Param  { index, param_values[], scene_mode, expression, … } // param_values = per-scene
ParamValue { int_value | float_value | string_value }
```
The grid is **4 rows × 8 columns**. `Chain` = a row; `Model.column` = the slot (0–7);
`Model.hash` = the device id (== catalog id, §9). `hash = 0` = empty slot. Rows 1&3 =
Path A, rows 2&4 = Path B (parallel), joined via Splitter/Mixer.

### Editing (verified against live traffic)
- **Add / replace a block** — `Grid`(1) UPDATE:
  `preset{ chains{ row:R, models{ hash:H, column:C } } }`.
  Adds overwrite whatever occupies `(row, column)`.
- **Set a parameter** — `Grid`(1) UPDATE:
  `preset{ chains{ row:R, models{ column:C, params{ index:I, param_values{ float_value:V } } } } }`.
- **Move a block** — `GridMove`(12); its `grid{ rows{ modelIds[] } }` is a full
  post-move layout snapshot (handy for reconstructing state).
- **Delete a block** — `Grid`(1) `action=DELETE` with `chains{ row, models{ column } }`.
- **Parallel split / mixer** — controlled entirely by `Chain.split_control_points
  { split, mix }`: `split` = column of the split, `mix` = column of the merge, `-1`
  = none. Add a split → `(C, -1)`; add the mixer/merge → `(-1, M)`; **remove the
  whole split → `(-1, -1)`**. The QC auto‑manages the splitter/mixer block objects;
  DELETE / hash=0 do **not** remove them — only the split points do.
- **Lane routing** — `Chain.in_portid` / `out_portid`. `out_portid = 19` is the main
  output for a single chain; `16` is a parallel mix bus. Clean single‑chain default:
  row0 `in=1, out=19, scp=(-1,-1)`, rows 1–3 `in=0, out=0`.
- **Read vs edit indexing:** in a full‑preset READ, a block's row = chain‑array
  index, column = models‑array index, and a param's index = params‑array index (the
  `.row`/`.column`/`.index` *fields* are 0 there; they are only meaningful in delta
  edits, where you set them explicitly).
- The device echoes edits back (and emits `UndoRedo`).

`transport.py`: `add_block`, `delete_block`, `set_param`, `set_split_points`,
`set_routing`, `set_lane_param`, `clear_grid`, `apply_spec`, `get_current_preset`.
Whole‑preset building: `preset.PresetBuilder` / `apply_spec` (a `Grid` UPDATE with a
full preset only *merges* blocks, so build incrementally then `save_preset`).

### Parameter values — normalized, with a taper
Stored parameter values are normalized 0..1. Display↔normalized conversion
(calibrated by capturing Cortex Control):
- **linear** for dB / % / level params (`min ≤ 0` or a small range),
- **power taper** for frequency / time / ratio params (`min > 0` and `max/min ≥ 5`):
  `display = min + (max-min)·nv^1.667`, i.e. `nv = ((display-min)/(max-min))^0.6`.

Implemented in `catalog.to_norm` / `to_display`. (A naive linear conversion put a
"220 ms" delay at ~25 ms — always use the taper.)

---

## 6. Presets & setlists

- Recall = `SetlistPosition`(2) UPDATE. **`folder_key` is REQUIRED** for user/factory
  recalls — a folderless UPDATE is silently refused, and the device *answers your
  request_id with the unchanged position* (that echo-mismatch is the failure signal;
  verified live). **Addressing differs by preset source:**
  - **My Presets / Factory:** `folder_key` + `position` (0-based) [+ `is_factory`].
    User setlists live under `/media/p4/Presets/My Presets`.
  - **Downloads (cloud):** `is_downloads:true` + `key_in_downloads:<cloud_id UUID>` —
    folder+position do **not** work for Downloads.
  - **Plugin banks:** `is_plugin:true` + `key_in_plugin_folder`.
  - `transport.recall(folder_key, position, is_factory, downloads_key, plugin_key)`.
- On recall the QC pushes the full `RecallPreset` (new preset).
- Capacity: setlists ≤ 256 presets, up to 10 user setlists, 3072 presets total.
- `transport.recall`, MCP `switch_preset` / `recall_preset`.

### Saving (decoded from Cortex Control's own Save, 2026-07)

- **Save = `File`(4) `action=CREATE`** with `folder{key, files[{index:<position>,
  name:<preset name>}]}` and **no `preset_payload`** — the device commits its live
  working grid to `<folder>/<name>.pb` at `<position>` (~65-byte request). The reply
  `File{UPDATE}` carries the assigned path, author, a fresh UUID, and fw version;
  the device then pushes `RecallPreset` + `Scene` + `SetlistPosition` +
  `RecentsFavorites` + `PresetDirty:false`.
- **Do NOT save via `RecallPreset`(15) UPDATE `reason=SAVE`** — on a slot with no
  preset file yet ("Unsaved") it **hangs the device** (hard reboot required) and
  commits nothing.
- `transport.write_preset_file(folder_key, position, name)`; MCP `save_preset`
  (current slot) / `save_preset_as` (any slot; refuses to clobber a different-named
  occupant unless `overwrite=True`).

### 6a. DIRECTORY — catalog listing, captures & IRs

The full DIRECTORY (presets + neural captures + IRs) is reversed in **docs/DIRECTORY.md**.

- **Listing** = `File`(4) READ (empty) → the device streams one `File{UPDATE}` **per
  folder**. `File.type`: `0 = Presets`, `1 = IRs` (keys `CIR_…`), `2 = Captures` (keys
  are 64-hex content hashes). Each folder carries `files[{key, index, name, author,
  coros_version, instrument, is_readonly, date_ms_since_epoch}]`; `index` is the
  `SetlistPosition` position. `transport.list_directory` collects the stream;
  `qc_mcp/directory.py` structures + searches it. MCP: `directory_summary`,
  `search_directory`.
- **Recents / Favorites** = `RecentsFavorites`(20) READ `{is_favorites}` →
  `items[{name, folder_key, folder_name, is_factory, is_plugin}]`.
  `transport.list_recents_favorites`, MCP `list_favorites`.
- **Current pointer** = `SetlistPosition` READ. `transport.get_setlist_position`,
  MCP `current_preset_position`.
- **Multi-select** = `BulkOperation`(57) `{is_multiselection_active}` (batch export/
  delete/move; destructive payloads intentionally not exercised).
- Captures/IRs are *used* by referencing their file `key` as a grid block model hash
  (same path as any device model). Cloud/Downloads listing is HTTPS, off the USB wire.

---

## 7. Scenes

- `Scene`(13) UPDATE `{ selected_scene }`, 0–7 = A–H. 8 scenes per preset.
- Scenes store **per-block parameter values and bypass states**: `Param.param_values`
  is an array indexed by scene, and `Param.scene_mode` marks a param as scene-varying.
  `BinaryPreset.default_scene` is the boot scene.
- `transport.set_scene`, MCP `switch_scene`. Verified live (A→C→A).
- **Labels & colors** (captured from Cortex Control's scene rename, 2026-07): dedicated
  per-scene commands, NOT preset fields — `SceneLabel`(23) UPDATE `{index, label}` and
  `SceneColor`(48) UPDATE `{index, color}` (ARGB int; the app auto-sends a color with
  each label). A `Grid` UPDATE carrying `BinaryPreset.scene_labels[]` is a **silent
  no-op** (confirmed live). Labeled state reflects into `BinaryPreset.scene_labels[]`
  on read; a scene holding data but no label displays "Undefined" on the device.
  `transport.set_scene_label` / `set_scene_color`, MCP `set_preset_meta`.
- **Refinement:** `set_param`/param display currently target scene 0; to be fully
  scene-accurate they should target the active/default scene.

---

## 8. Performance modes

- `Mode`(14) `{ action, request_id, mode:uint, available_modes{ modes:uint[] },
  atma_page:uint }`. Fully captured (2026-07-24):
  - **Read current mode:** `Mode` READ → reply `{ mode, available_modes{modes[]} }`
    — `mode` = active id, `available_modes.modes` = the footswitch cycle. MCP `get_mode`.
  - **Switch mode:** `Mode` UPDATE `{ mode:id }`; device echoes (same request_id).
    Must be in `available_modes` or it's refused. MCP `switch_mode`, `transport.set_mode`.
  - **Set the cycle** (Modes Configuration): `Mode` UPDATE
    `{ available_modes{ modes:[...] } }`. MCP `set_mode_cycle`, `transport.set_mode_cycle`.
- **Id space `0–8` — fully mapped, every id verified live.** `mode`/`modes` are raw uints.
  - **Base:** `0 = Preset`, `1 = Scene`, `2 = Stomp`.
  - **Hybrids `3–8`** = a **top row A–D / bottom row E–H** pairing of two base modes
    (a hybrid always combines exactly two — one per footswitch row). The row order is
    part of the id; the app's ⇅ swap toggles a pairing's two ids. Formula
    **`id = 3 + 2·top + bottom_rank`** (top = base id of the top-row mode; bottom_rank
    = 0/1 for which of the other two base modes is on the bottom, lower id first):

    | id | top A–D / bottom E–H | | id | top / bottom |
    |----|----|----|----|----|
    | 3 | Preset / Scene | | 6 | Scene / Stomp |
    | 4 | Preset / Stomp | | 7 | Stomp / Preset |
    | 5 | Scene / Preset  | | 8 | Stomp / Scene |
  - The hybrid ids are **not** a bitmask of the base ids.
- **`available_modes` (the cycle)** can hold **1..N** entries (Modes Configuration lets
  you remove modes down to a single one, or add several). Reordering within the cycle
  and its length are both free.
- **Behavior:** setting `available_modes` so it no longer contains the currently-active
  mode makes the device **fall back to `mode 0` (Preset)** (device emitted `mode: 0`
  right after the cycle dropped the active hybrid). So change the cycle first, then
  `switch_mode`.
- Manual (CorOS 4.0): three base modes — Preset / Scene / Stomp — plus Hybrid combos
  (drag one mode onto another); on-device cycling = BANK DOWN + TEMPO; MIDI CC#47
  switches modes. Ids for Scene/Stomp remain to be captured.
- **Hybrid = one mode per footswitch row** (confirmed live from the owner's Modes
  Configuration screen): the top row A–D gets one mode, the bottom E–H the other, order
  swappable. The observed cycle is **PRESET ⟷ SCENE+STOMP hybrid** (top A–D = Scene,
  bottom E–H = Stomp), and available_modes was `{0, 6}` → **`6` = the Scene+Stomp
  hybrid**, `0` = Preset. Only one switch row is left for presets, which is why Hybrid
  halves the bank size. Whether other top/bottom pairings get distinct ids is uncaptured.
- On the hardware, BANK DOWN + TEMPO cycles modes.
- `transport.set_mode`, MCP `switch_mode` ('preset' / 'hybrid' / raw id).

---

## 9. Hardware I/O settings

`IOSettings`(3) → `PortSettings`:
```
InputPortSettings  { input_port_id, level, input_zmode (impedance),
                     input_type (0=instrument, 1=line/mic), ground_lift, plugged }
OutputPortSettings { output_port_id, level, ground_lift, mute, plugged }
HeadphonesSettings { level, hp_feed[], plugged }
USBPortSettings    { level, hp_select, dry_wet, plugged }
ExpPortSettings, MIDIPortSettings
IOSettingsMessage  { …, xlr1_2_linked, out3_4_linked }   // output/input pairing
```
Physical ports (observed ids): IN 1 = instrument, IN 2 = mic/line (Capture), IN 4/5 =
FX returns; OUT 1/2 = main XLR L/R, plus 3/4 and sends; HP; USB (8 in / 8 out); EXP 1/2.
MCP `get_io_settings`.

### Grid input/output blocks (per lane)
- **Input block** — `Input Gate Control` (#28000): `NOISE REDUCTION` (0–100),
  `INPUT GAIN` (±24 dB), `BYPASS`, `GAIN REDUCTION` (meter). In `Chain.input_control`;
  `Chain.in_portid` picks the physical input.
- **Output block** — `Lane Output Control` (#23000): `VOLUME` (dB), `PAN` (0–1),
  `MUTE`, `SOLO`. In `Chain.output_control`; `Chain.out_portid` picks the destination.
- **Splitters** #10000–10004 (AB / Crossover / Balance), **Mixer** #11000.

---

## 10. Device catalog — `ModelRepo`

- `ModelRepo`(51) READ returns a gzip+tar containing **`ModelRepo.xml`** — the full
  catalog: **633 devices on CorOS 4.1** (was 533 on 4.0). Each `<Model>` has `id`,
  `name`, a `tm="Based on …®"` real-gear reference, and full `<Parameter>` schema.
- **A block's `Model.hash` equals the catalog `id`.** So any block resolves to name +
  emulated gear + parameter names/ranges.
- Bundled snapshot: `src/qc_mcp/ModelRepo.xml`. `catalog.py`: `lookup`, `name_of`,
  `find`, `categories`. MCP `find_devices`.
- **The catalog is firmware data, so refresh it after a CorOS update:**
  `tools/dump_model_repo.py [--diff]` reads it from the connected device and
  rewrites the snapshot. 4.1 added 100 models — the eight new native devices
  (Multivoicer, Glitch, Ring Modulator, Arpeggio Delay, Crystal Delay, Vintage
  Digital, Douglas Shining Comp, Plugin Parametric-4) plus the X-updated plugin
  device sets (Petrucci, John Mayer, Rabea, Misha Mansoor, Tim Henson) — and
  renamed several: `30001` Mono Synth → **Overlord Synth**, and the Darkglass®
  models to Douglas (`3000` B3K → Douglas MT 3K, `21001`/`33001` 210C → 210
  Douglas Ceramic, and so on). Plugin models appear in the catalog on every unit;
  using one still needs that plugin's licence.

---

## 11. Reverse-engineering tooling

- `interceptor/` — a **DYLD interposer** (`interpose.c`) injected into a re-signed copy
  of Cortex Control. It hooks `IOHIDDeviceSetReport` + the input-report callback and
  can log every HID frame (set `QC_VERBOSE=1`), so real host↔device traffic can be
  captured while operating the app. `build.sh` copies the app, adds
  `disable-library-validation` / `allow-dyld-environment-variables` entitlements, and
  re-signs ad-hoc without the hardened-runtime flag so `DYLD_INSERT_LIBRARIES` works.
- **Bridge mode** (share the session so the app + MCP run simultaneously): the
  interposer exposes two FIFOs — `QC_INJECT` (MCP → device: 129-byte reports) and
  `QC_OUT` (device → MCP: `[uint16 LE len][report]` mirror of every input report).
  Injected frames are **queued and flushed only at a message boundary** (after a
  LAST/SINGLE report, from inside the app's own `SetReport`) so they never interleave
  the app's multi-chunk messages; the interposer **ignores SIGPIPE** so an MCP
  disconnect can't kill the app. MCP side: `bridge.py` (`FifoBridge`, a drop-in for
  the IOKit transport) + `QuadCortex(bridge=True)` (no handshake/heartbeat — the app
  provides them). Launch with `interceptor/run-bridge.sh`; enable in the server with
  `QC_BRIDGE=1`.
- `tools/` — `extract_protos.py` (recover protobuf descriptors from the binary),
  `build_descriptors.py` (`build`/`list`/`diff` the per-generation descriptor sets
  the MCP ships), `dump_model_repo.py` (refresh the device catalog from a
  connected unit, `--diff` to see what a firmware update added),
  `analyze_log.py` / `verify_frames.py` (reassemble + decode captured logs),
  `xref_dis.py` (Mach-O + capstone string-xref disassembler), `probe_usb.py`,
  `hid_capture.py`, `midi_monitor.py`.
- **After a CorOS update**, re-run `interceptor/build.sh` as well: the
  instrumented app is a *copy*, so updating Cortex Control leaves the bridge
  running the old binary against new firmware.

---

## 11a. Official-manual cross-check (neuraldsp.com/manual/quad-cortex)

Checked 2026-07-24 against this document; the manual **confirms** the grid model
(4×8, split→parallel→mix, outputs routable to rows), bypass≠CPU, Global EQ/Input
Gate auto-disable, 256/setlist · 10 setlists · 3072 presets, per-scene params+bypass,
and scene names/colors. Additions/corrections from the manual:

- **Path A/B law:** rows 1&3 = "Path A", rows 2&4 = "Path B"; **splitters only route
  Path A → Path B** — the reason branch rows reject their own splits (see the
  build-preset-routing skill).
- **Side-chaining** (unexplored by this protocol work): SOURCE/TRIGGER on capable
  blocks, up to **two side-chain devices per pair of rows**.
- **Global EQ** is assignable to one or both output pairs (1/2, 3/4).
- **Deleting a User Setlist permanently deletes all presets in it** — never automate
  setlist deletion.
- **Device variants:** Quad Cortex mini (own manual) differs materially — 4 scenes
  (A–D), 4-preset banks, and **bypass is NOT scene-assignable** on the mini. Facts
  in this document were verified on a full-size unit ("QC MAX"); don't assume they
  transfer to the mini. `VersionMessage.device_type` distinguishes them:
  `QC = 0`, `ATMA = 1` (the mini).

Re-checked against the **4.1.0** manual 2026-08-27, which confirms the reversed
4.1 behaviour: Virtual Device Presets cover "most virtual devices, I/O Settings,
and Global EQ" with **32 user presets each** and factory presets for most devices;
Favorites/Recent holds **64 items per category** (Presets, Neural Captures,
Impulse Responses), oldest evicted first. The manual does **not** document Dual
Footswitch Assignments or the eight new devices — those are release-notes only.

---

## 12. Protocol versioning — CorOS 4.0 vs 4.1

The wire schema is **not frozen across CorOS releases**, so the MCP ships one
descriptor set per generation in `src/qc_mcp/descriptors/qc_descriptors-<gen>.pb`
and picks one per connection:

1. `QuadCortex.open()` calls `detect_version()`, which READs `Version`(10).
2. The human CorOS version is in **`zenos_git_hash`** ("4.1.0") — *not* in
   `app_fw_version`, which holds a build hash ("d14e"). `protocol.generation()`
   maps it to the newest generation ≤ that version (unknown-newer firmware falls
   through to the newest schema we ship, since changes are usually additive).
3. `protocol.pool(version)` caches a descriptor pool per generation, and
   `message_class(cmd, version)` encodes against the right one.
4. Version-gated capabilities live in `protocol.FEATURES`; a tool calls
   `P.require("model_presets")` and returns "needs CorOS 4.1" instead of sending
   a message an older device would ignore.

The handshake also **mirrors the device's own version back** as
`cortex_control_version` rather than announcing a hardcoded release.

Rebuild a generation with `tools/build_descriptors.py build <gen>` (extracts from
the installed Cortex Control), and see exactly what moved with
`tools/build_descriptors.py diff 4.0 4.1`.

### What 4.1 changed on the wire

Mostly additive, with **one genuinely incompatible field**:

| change | detail |
|---|---|
| **`GlobalEQMessage` field 5** | `has_user_defaults` (bool) → `model_preset_to_load` (ModelPresetID); field 6 `default_parameter_action` removed. **The only field-number reuse** — a 4.0 pool talking to 4.1 would mis-encode it. |
| new commands | `71 ModelPreset`, `72 RemoteControl` |
| `StompModeAssignment.type` | `PRIMARY`/`SECONDARY` — the Dual Footswitch Assignments feature |
| `RecentsFavoritesMessage.type` | `PRESET`/`IMPULSE_RESPONSE`/`NEURAL_CAPTURE`, plus `RecentsFavoritesItem.product_key` |
| `GridMessage` | `+model_preset_to_load`, `+control_is_auditioning`, `UpdateType.MODEL_PRESET` |
| device-preset hooks | `IOSettingsMessage.preset_to_load`, `NeuralCaptureMessage.model_ab_preset`, `NeuralCapture2Message.cabsim_model_preset` |
| `FileMessage` | `+omit_factory_content`, `+user_content_estimate` (backup scoping) |
| `GeneralSettingsMessage` | `+external_midi_clock_tempo`, `+external_midi_clock_out_of_range`; `cloud_endpoint` moved to `VersionMessage` field 19 |
| `SOC2ARMCommsDiagnosticsMessage` | four USB-audio gap/reset counters |

## 12a. Device presets — `ModelPreset`(71), CorOS 4.1

*(For how to actually use these from the MCP, see [docs/COROS-4.1.md](docs/COROS-4.1.md).)*

Per-device saved settings ("save your favourite amp/drive/reverb settings and
recall them in any rig"). 4.1.0 ships **2751 factory presets across 602 models**;
the manual caps **user** presets at 32 per device. I/O Settings and Global EQ get
presets too — I/O Settings rides a pseudo-model, catalog hash **31000
"IOSettings"** (category `IOSettings Internal`, new in 4.1), and the message has
matching hooks (`IOSettingsMessage.preset_to_load`,
`GlobalEQMessage.model_preset_to_load`).

- **List:** `ModelPreset`(71) READ returns the *entire* index in one (gzipped)
  reply — `presets[] {id{value, is_factory, hash}, name, is_default}`, where
  `id.hash` is the **model hash the preset belongs to**, so filter by it for one
  block's presets. The device also broadcasts this index unprompted on connect.
- **Load — verified 2026-08-27:** this is *not* a `ModelPreset` write. The device
  takes a **`Grid`(1) UPDATE** with `update_type = MODEL_PRESET(1)`,
  `model_preset_to_load{value, is_factory, hash}`, and a one-block grid naming
  the target (`preset.chains[]{row}` + `models[]{hash, column}`). The block's
  parameters are replaced in place; read it back to confirm. Loading ids 1–4 onto
  a Myth Drive gave four distinct GAIN/TREBLE/LEVEL sets.
- **Select the slot first — this is the trick.** The device tracks exactly *one*
  active grid slot for device-preset writes; the app's own resource file says the
  loaded-preset field "is correct only for the open panel / selected grid slot".
  Cortex Control sends this the instant a block editor opens:

      ModelPreset { request_id, loaded_row, loaded_column }      # no action field

  Note **no `action`** — i.e. CREATE(0), which here means *subscribe*. The device
  answers with `loaded_preset_id`, the preset that block currently holds:
  `""` = its settings match no preset, `"SpecialFactoryModelPresetID"` = factory
  defaults. Skip this and every write below is silently ignored.
- **Save** a user device preset (32 max per device) — again with no action field,
  and note there is **no parameter payload and no `create_from_*`**: the device
  snapshots the selected block itself.

      ModelPreset { request_id,
                    presets[0] { id{ is_factory:false, hash:<model> },
                                 name, is_default } }

  The reply echoes the created preset with its assigned `id.value` ("1", "2", …),
  followed by a second message updating `loaded_preset_id` for the slot.
  **The device refuses a save whose parameters already match an existing preset
  for that model** — the app shows "Preset Conflict: … already stored in an
  existing Preset", and the menu item greys out. Change a parameter first.
- **Delete:** `ModelPreset{action=DELETE, presets[0].id{value, is_factory:false,
  hash}}`. Factory presets can't be removed.
- MCP: `list_device_presets`, `load_device_preset`, `save_device_preset`,
  `delete_device_preset`.

### Global EQ and I/O Settings presets

The manual's "most virtual devices, **I/O Settings, and Global EQ**" is literal:
both are ordinary entries in the same `ModelPreset` index, on pseudo-models —
**Global EQ = catalog hash `4004` "Output Equalizer"** (its 28 params line up 1:1
with `GlobalEQMessage.parameters` indices 0-27: five bands of
GAIN/FREQ/Q/TYPE/BYPASS, then OUTPUT and two ASSIGN_EQ slots; 23 factory presets)
and **I/O Settings = `31000`** (one factory preset, "Neural DSP® Default").

They are *applied* through their own message, not the Grid:

    GlobalEQ   { action=UPDATE, model_preset_to_load{value, is_factory, hash=4004} }
    IOSettings { action=UPDATE, preset_to_load     {value, is_factory, hash=31000} }

**Both overwrite global state that every preset sees.** Capture the current
values first — `GlobalEQ` READ returns all 28 parameters and they can be written
straight back, which makes a Global EQ load fully reversible. An I/O Settings
load is *not* reversible from the message alone: it resets input levels,
impedance and type, so snapshot them first (`load_settings_preset` returns the
previous values and requires `confirm=True`).

MCP: `list_settings_presets`, `load_settings_preset`, `set_io_port`.

### Writing hardware I/O settings

`IOSettings` UPDATE accepts `settings{in_port{…}}` / `out_port{…}` — but **only
with the fields you are changing set**. A full port record (every field, e.g. a
protobuf `CopyFrom` of one the device just sent) is *silently rejected*, which is
what makes I/O writes look impossible. Confirmed by writing the same port three
ways: `{port_id, level}` and `{port_id, level, plugged}` both landed; the full
record did nothing.

`input_type` is a **3-position normalized control**, not a boolean:
`0.0` Instrument · `0.5` Mic · `1.0` Line. Cortex Control's panel only exposes
Instrument/Mic, so a port set to Line from the unit's own screen can be read and
restored over the protocol but not through the app.

## 12b. Footswitch (stomp) assignments — `Grid`(1)

Binding a block to a footswitch turned out to live on the **Grid** message, not a
message of its own — a Grid UPDATE whose preset carries *only* the assignment:

    Grid { action=UPDATE, request_id,
           preset { stomp_mode_assignments[0] { row, column, stomp_index, type } } }

`stomp_index` 0-7 = footswitches A-H; `type` is the CorOS 4.1 Dual Footswitch
field (`PRIMARY`=0 bypass, `SECONDARY`=1 the device's second function, on the
devices that have one). A block holds **one assignment per kind** — verified on a
Vintage Digital reverb holding E as PRIMARY and F as SECONDARY simultaneously;
assigning the same kind again moves it. **Unassign is the same message with
`action=DELETE`**, which also clears the switch's momentary flag.

The device does **not** guard SECONDARY: it accepts the assignment on any block,
including ones with no second function (tested on a Myth Drive), where the
footswitch is simply consumed and does nothing. Only assign it to devices that
have a second function.

**Latching vs momentary must be a separate message.** The preset's
`stomp_is_momentary` map (`{stomp_index: bool}`) is writable by Grid UPDATE, but
only on its own: the device answers an assignment with its own
`stomp_is_momentary` echo, which overwrites a flag sent in the same message.

MCP: `assign_stomp`, `unassign_stomp`; `get_current_preset` reports
`stomp_assignments`.

## 12c. Still unreversed

**`RemoteControl`(72)** — `RemoteControlMouse` (PRESS/RELEASE/MOVE/TAP/DRAG),
`RemoteControlScreenshot {payload, x, y, w, h}`, `RemoteControlGraphicsTree` —
i.e. driving the QC's own screen remotely. A bare READ draws no reply, so it
presumably needs an enable/subscribe step; unreversed, and no MCP tool yet.

---

## 13. Repo layout

```
src/qc_mcp/
  iohid.py        ctypes IOKit HID transport (seize, reader thread, run loop)
  protocol.py     framing: descriptor pool, encode/decode, chunk/reassemble, gzip
  transport.py    session: handshake, heartbeat, reads/writes, grid edits
  catalog.py      ModelRepo.xml → hash↔name/gear/params
  server.py       MCP server (FastMCP tools)
  descriptors/    qc_descriptors-<gen>.pb — one wire schema per CorOS generation
  ModelRepo.xml   bundled device catalog snapshot
proto/            recovered Preset.proto + ProductionAutomation.proto + ModelRepo.xml
tools/            RE utilities
interceptor/      DYLD interposer for live HID capture
PROTOCOL.md       this file
```
