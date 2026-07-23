# DIRECTORY view — protocol & catalog

Reverse-engineered the Cortex Control **DIRECTORY** view (preset catalog + neural
captures + IRs). Method: mine the interposer capture log (`interceptor/hid_log.txt`)
for the already-captured operations and decode them — fully non-destructive, nothing
sent to the device. Tools: `tools/gui/mine_log.py` (decode messages by command),
`tools/gui/dump_catalog.py` (build a catalog snapshot), `tools/gui/gui.py` (drive the
GUI when a live action is needed).

## Commands (from `qc_mcp.protocol.COMMANDS`)

| Command | id | Dir | Role |
|---|---|---|---|
| `File` | 4 | IN (stream) | **Directory listing.** A single `File{action:READ}` (empty) makes the device stream one `File{action:UPDATE}` **per folder** for presets, IRs, and captures. |
| `SetlistPosition` | 2 | OUT/IN | **Loaded-preset pointer.** READ → current `{folder_key, position, is_factory}`. **UPDATE {folder_key, position, is_factory} loads a preset.** |
| `RecallPreset` | 15 | OUT/IN | Full preset state stream (name, author, `chains{models}`). READ → current preset; pushed on change. |
| `RecentsFavorites` | 20 | OUT/IN | READ `{is_favorites}` → recents (false) or favorites (true): `items[{name, folder_key, folder_name, is_factory, is_plugin}]`. |
| `BulkOperation` | 57 | OUT/IN | Multi-select mode: `{is_multiselection_active}` (for batch export/delete/move). |
| `PresetDirty` | — | IN | The unsaved-changes `*` flag on the current preset. |
| `CloudProduct` / `ProcessDownloadsQueue` / `CloudLogin` | — | | Cloud presets / downloads queue / account (app↔Neural DSP servers; the device coordinates but the catalog fetch itself is HTTPS, not on the USB wire). |
| `PinnedModels` / `NewModels` / `DefaultParameters` / `CompilerInhibitedModules` | — | | Model-catalog metadata pushed on connect. |

## File message = the catalog

```
File{ action:UPDATE, type:<0|1|2>,
      folder{ key, name, is_factory, is_downloads, is_user_default,
              files[ { key, index, name, author, author_id,
                       coros_version, instrument, is_readonly,
                       date_ms_since_epoch } ] } }
```

`File.type` segments the three catalogs:

| type | catalog | notes |
|---|---|---|
| 0 | **Presets** | `My Presets`, `Factory Library`, cloud **Downloads**, and per-plugin banks (Archetype: Cory Wong X, Nolly, …). File keys are `*.pb` paths. |
| 1 | **IRs** (impulse responses) | `IRs Library`, `My IRs`, factory `impulse_responses` (588). File keys prefixed `CIR_`. |
| 2 | **Captures** (neural captures) | `Captures Library` (aggregate), `Factory Captures V1/V2`, `My Captures`, organised by amp/pedal folders. File keys are **64-hex content hashes**. |

**Folder key conventions:** user folders use short ids `"<n>_q"` / `"<n>_f"` or a path
under `/media/p4/Presets/...`; factory folders live under `/opt/neuraldsp/...`; cloud/
download folders are `cloud-<type>-<n>`. `files[].index` is the **position** used to
load a preset via `SetlistPosition`. `instrument`: 0 = guitar, 1 = bass (seen on both
presets and captures).

### Typical snapshot shape (orders of magnitude, from a test device)
- **Presets:** ~200+ folders / ~2000+ files (My Presets + Factory Library, each up to 256,
  plus cloud Downloads and per-plugin banks).
- **IRs:** a handful of folders / hundreds of files.
- **Captures:** ~150+ folders / thousands of file entries — a "Captures Library"
  aggregate plus Factory Captures V1/V2 and any user "My Captures".

A full snapshot of the connected device is written to `interceptor/catalog.json` by
`dump_catalog.py` (gitignored — it contains your library's names/ids).

## How operations map

- **List the catalog** — `File` READ → collect the `File` UPDATE stream until it goes
  quiet → `directory.structure_directory()`. See `transport.list_directory()`.
- **Search** — `directory.search(catalog, query, category)` (substring over file +
  folder names). MCP: `search_directory`.
- **Load a preset** — `SetlistPosition` UPDATE `{folder_key, position, is_factory}`.
  See `transport.recall()` / MCP `switch_preset` / `recall_preset`. A search hit gives
  `setlist_key`+`position` directly.
- **Current preset pointer** — `SetlistPosition` READ. `transport.get_setlist_position()`.
- **Recents / Favorites** — `RecentsFavorites` READ `{is_favorites}`.
  `transport.list_recents_favorites()` / MCP `list_favorites`.
- **Use a capture / IR** — reference its file `key` (content hash) as a block model in
  the grid (same mechanism as any device model hash; a Capture/NAM block loads the
  capture by hash). IRs load into a Cab/IR-loader block by their `CIR_` key.

## Neural Captures — kinds, V1 vs V2, and how they load into blocks

Captures are **not only amps.** A capture models whatever device sits between the QC's
Capture Out and Return — an amp, a combo, or a pedal — and is tagged so it loads into the
matching block category.

**Capture creation modes** (what you point the QC at when capturing):
- **Amp** — an amp head (through a load box).
- **Pedal** — a single pedal (drive / fuzz / comp / boost); models its full frequency
  response + saturation.
- **Full Chain / Full Rig** — an amp + pedal(s) captured as one unit.

**Stored kind** — each capture file (`File` type 2, `ProductData`) carries two ints that
place it in a block category (decoded from this device's library):

| `device` | kind | loads into block |
|---|---|---|
| 0 | (legacy / unspecified) | Amp |
| 1 | **Amp** (head) | Amp |
| 2 | **Combo Amp** | Amp (combo) |
| 3 | (rare; seen with bass) | Bass Amp |
| 5 | **Pedal** (generic drive) | Overdrive/Drive |
| 6 | **Fuzz** | Fuzz |
| 7 | **Compressor** | Compressor |
| 8 | **Overdrive** | Overdrive |

`instrument`: **1 = guitar, 2 = bass** (0 = unspecified/legacy). Also present: `tags`
(list, e.g. `["overdrive","boost"]`), `gain`, `author`, `cloud_id`,
`extraMetadata`. So in Cortex Control a capture appears **inline in the model list of
its matching block category** (e.g. an amp capture named "1959 …" sits near the top of
the AMP list, sorted with the models), not in a separate captures pane.

**V1 vs V2** (two distinct on-device capture engines *and* grid block types — the
"Neural Capture" catalog category holds `14000`, `14001 "Capture 2"`, plus the capture
tools `20000 NC_Recorder / 20001 NC_Trainer / 20002 NC_Refiner`):

| | **Neural Capture V1** | **Neural Capture V2** |
|---|---|---|
| Where it's computed | On the QC/Nano itself, **offline**, in a few minutes | Via **Cortex Control + Cortex Cloud** (needs internet), slower |
| Fidelity | Great for amps & transparent ODs | Higher-resolution; **better dynamics/touch** for fuzz, compressors, responsive tube amps |
| Cost | Small, low CPU | **Larger files**, more storage |
| Factory sets on this device | Factory Captures **V1** (1393) | Factory Captures **V2** (669) |

A capture must be loaded into the block type matching its version + kind.

### How a capture binds to a block (confirmed by loading real presets)

Loading Downloads presets that use captures (Sultans of Swing, Brothers In Arms, Money
for Nothing, ZW SEAL LIVE) revealed the full mechanism. It has **two parts**:

1. **The grid block carries the capture identity itself** — a Neural Capture block is
   `Model{ hash: 14000 (V1) | 14001 (V2), params[…] }`. `hash` is only the generic block
   type; the specific capture lives in a **string param**. The block's params (by array
   position, `.index` is 0 in reads) are:

   | pos | param | value in Sultans |
   |---|---|---|
   | 0 | GAIN | 0.59 |
   | 1 | BASS | 0.38 |
   | 2 | MID | 0.50 |
   | 3 | TREBLE | 0.53 |
   | 4 | VOLUME | 0.50 |
   | 5 | **`file_name`** (string) | `"<64-hex key>" + "<name>"` |
   | 6 | (aux, 0.0) | |

   So a capture block is effectively a mini-amp: a 3-band EQ + gain/volume **on top of**
   the capture, with **param[5] `file_name` = the capture's 64-hex key concatenated with
   its display name** (e.g. `"<64-hex key>" + "<capture name>"`). GAIN/BASS/MID/TREBLE/
   VOLUME are per-scene; `file_name` is not.

2. **The preset FILE also lists the capture as a dependency** (`ProductData`), so the
   device/cloud knows what must be present:
   - `factory_dependencies: [{ hash }]` — **factory** captures/IRs (already on device), by
     64-hex key only.
   - `product_dependencies: [{ hash, name, cloud_id, product_type: "neural_capture" }]` —
     **cloud/user** captures, with full metadata.
   - The `hash` == the `file_name` key == the capture's catalog `key` (type 2). Verified
     by loading community presets: a preset depending on a factory Amp capture lists it in
     `factory_dependencies` (hash only); one depending on a factory Pedal capture likewise;
     a preset depending on a **cloud** capture lists it in `product_dependencies` with the
     full `{hash, name, cloud_id}`. Every dependency hash resolved to a real catalog entry.

3. **On load** the app syncs the capture to the device via **`ProductForward`** (29 —
   encrypted/binary payload). A **factory** capture is already on-device so the preset
   loads instantly (Sultans → active immediately). A **cloud** capture that isn't present
   must be pulled from **Cortex Cloud** first — observed: double-clicking Brothers In Arms
   fired only `ProductForward` (capture sync) and did **not** switch the active preset,
   because its cloud capture had to be fetched.

To place a capture programmatically: add a Neural Capture block (14000 V1 / 14001 V2),
set param[5] `file_name = key+name`, and add the key to `factory_dependencies` (factory)
or `product_dependencies` (cloud). Multiple capture blocks map to their deps in order.
Same pattern applies to **IRs** (type-1 `CIR_…` keys).

## Testing — prompt→chain (e2e) and deep round-trip fidelity

- `tools/gui/e2e_test.py` — natural-language prompt → built chain → read back → assert
  block categories in order (catalog resolution + placement + capture load). 5/5.
- `tools/gui/roundtrip_test.py` — loads REAL corner-case presets (multi-lane, splits,
  mixers, stereo/dual cabs, FX loop, bass, captures) and deep-diffs golden vs rebuilt
  across **every** field: per-block per-scene param values, **expression-pedal
  assignments** (expr/min/max), routing (in/out portids), splitters/mixers, cab mic
  settings, reverb params, volume, pan, scenes, bypass map.
  - **Offline** (`describe`→`build`): **8/8 identical** — the model is fully faithful.
  - **`--device`** (rebuild onto device via `apply_spec` → read back): verifies the wire
    path. Blocks, routing, mixers, and **string params (cab mics, capture file_name, IR
    paths) all survive** (verified on Sultans: 5/5 string params exact). The **only**
    diffs are `scene_mode`/per-scene values — isolating **per-scene parameter writes** as
    the last gap. Confirmed root cause: a per-scene write (`set_param_scenes`, and even
    `set_scene`+`set_param` per scene) only sets the active/global value — the device
    needs a scene-enable trigger the MCP doesn't send yet (the "scenes frontier").

**Two fidelity bugs this round-trip caught + fixed:** `ParamValue` is a oneof
(int/float/string); the old `[float,int,string]` spec format couldn't distinguish
`string_value=""` from `float 0.0`, and `apply_spec` read only `v[0]` (float) — so
**string params (cab mic/IR names, capture file_name, IR paths) were silently dropped**
on rebuild. Fixed: `preset._pv` now records only the active oneof field (others `None`),
`build` sets exactly that field, and `transport.set_param_typed` preserves type in
`apply_spec`.

## Per-scene parameters — SOLVED (the "scenes frontier")

Reversed from Cortex Control's right-click **"Assign to Scenes"**. A param is made
scene-varying in two steps, both plain `Grid` UPDATEs:
1. **Assign**: `params{index, scene_mode:true}` with **no** `param_values` — enables
   scene mode on the param (`transport.assign_param_to_scenes`).
2. **Per-scene value**: set the active scene (`Scene` UPDATE), then write a plain
   `params{index, param_values{float}}` — once assigned, a plain write lands on the
   **active** scene. Repeat per scene A–H, restore A.

`transport.set_param_scenes` now does this and is verified: 8 distinct values
`[0.1…0.8]` round-trip, and the Fender test shows VOLUME clean=2.0 / lead=8.5 across
scenes with the A|B|C|D scene badge. `apply_spec` routes scene-varying params here, so
the device round-trip now reproduces per-scene values too.

**Per-scene bypass — also SOLVED.** Right-clicking a block's **bypass/power button** (not
the block body) shows "Assign to Scenes", which emits `params{index:4, scene_mode:true}`
— i.e. a block's **bypass is param index 4**, and per-scene bypass is just the per-scene
param mechanism on it (**1.0 = bypassed, 0.0 = active**). `set_block_bypass(..., scenes=
[...], bypass_param=4)` routes through `set_param_scenes`. Verified visually: Klon off in
scene A, on in B. Test: `tools/gui/test_fender_scenes.py` (Fender Deluxe clean/crunch/lead
+ per-scene Klon bypass + Klon lead-gain) — reliable read-back needs `QC_BRIDGE=0`.

## Block editing — captures, IRs, cabinet mics, bypass (verified live)

**Load a capture into a block** (`transport.set_capture` / MCP `add_capture`): place a
Neural Capture block (14000 V1 / 14001 V2), then set param[5] `file_name` (string) =
`<64-hex key><name>`. Verified live: `add_capture(0,0,key,"CA John's 2")` placed the
block and the editor showed "Neural Capture CA John's 2" loaded.

**Load an IR into a block** (`transport.set_ir` / MCP `add_ir`): place an IR-loader
block (29001 mono / 29002 stereo), then set param[2] `IR PATH` (string) = the `CIR_…`
key and param[22] `IR NAME` = display name (two separate string params).

**Cabinet parameters** — a Cab block (12xxx mono / 32xxx stereo) is a **dual-mic** model.
Params repeat for **Mic A** (positions 0–7) and **Mic B** (8–15), then shared (16–18):
`bypass, ir selector (mic/IR, string), LEVEL, PAN|BALANCE, DISTANCE, POSITION, phi
(angle), GRID MODE` ×2, then `HPF, LPF, OUTPUT VOLUME`. So mic **position** (idx 5/13),
**distance** (4/12), **angle/phi** (6/14) and **type** (`ir selector` 1/9) are set with
`set_parameter(row,col,idx,value)` (0–1 normalized). IR-loader block (29xxx) params:
`MUTE, INVERT, IR PATH, LEVEL, HI PASS, LOW PASS, PAN, DELAY` ×2, then `ROOM MIX, PRE
DELAY, REV HI/LOW PASS, SIZE, GLOBAL OUTPUT`.

**Block bypass** (`transport.set_block_bypass` / MCP `set_block_bypass`) — captured live
message: `Grid` UPDATE `preset.bypass[{ row, colBypass{ column, sceneBypass{bypass} } }]`.
`sceneBypass` is **per-scene** (A–H); the app sends one entry for the current scene, or
pass 8 bools for explicit per-scene bypass. Verified live (bypass + un-bypass an amp).

## Coverage sweep — all 43 Downloads presets (via `tools/gui/sweep_presets.py`)

Loaded every Downloads preset over the bridge and ran each grid through the MCP decoder
to check nothing is unhandled. Result:

- **43/43 loaded, 0 decode errors** — `server._preset_summary` handled every preset.
- **0 unknown model hashes** — the catalog resolves every block used across all 43
  community presets. Categories seen: Amp, Cabsim (M/ST), Overdrive, Compressor, EQ,
  Delay, Reverb, Modulation, Pitch, Filter, Wah, **FX Loop, Loopers, IRLoaders, Utility**,
  Neural Capture.
- **34/43 use captures** — far more than the 4 that the *File dependency listing*
  suggested. The dependency manifest in the listing is incomplete/short-form; the real
  capture usage is only visible in the loaded grid (the `file_name` param). **Trust the
  loaded grid, not the listing's dependency fields, for capture usage.**

### Gap found: preset **loading** addressing (now fixed)
Downloads/Plugin presets do **not** load by `folder_key`+`position` — the sweep proved
`recall("cloud-0-1", pos)` is silently ignored. They load by:
- Downloads: `is_downloads` + `key_in_downloads` = the preset's **`cloud_id`** (UUID).
- Plugins: `is_plugin` + `key_in_plugin_folder`.
Fixed in `transport.recall(..., downloads_key=, plugin_key=)` and MCP `switch_preset`
(`downloads_cloud_id=`); `search_directory` now returns `downloads_cloud_id` per hit.
Also: `get_current_preset` (a READ) returns empty in **bridge** mode — read the grid the
device **pushes** on recall instead (what `switch_preset`/the sweep now do).

### Still-open (present in preset data, not yet exposed by MCP)
These fields appear in Downloads presets and decode fine, but no MCP tool reads/edits
them: **`bypass`** (per-block bypass map), **`midi_messages`/`midi_messages_general`**
(per-preset MIDI out), **`stomp_mode_assignments`** (footswitch/stomp assignments),
**`side_chain_follow_exists`**. Wire these up when needed.

## Not yet done / open
- **Cloud/Downloads** listing is HTTPS (not on the USB wire) — only the device-side
  download queue (`ProcessDownloadsQueue`) and resulting `File` entries are visible.
- **BulkOperation** payload for actual batch delete/move/export (only the mode toggle
  was captured; destructive, intentionally not exercised).
- **Create/rename/delete** a preset file: `File` CREATE exists (`write_preset_file`);
  rename/delete not yet reversed (destructive — capture live when needed).
