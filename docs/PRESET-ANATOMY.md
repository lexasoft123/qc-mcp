# Anatomy of a preset (`BinaryPreset`) — what a faithful copy must carry

Why this exists: a "rebuild" of a preset verified only against `preset.describe()`
looked perfect (0 differences) and had silently dropped the preset's **MIDI out**.
`describe()` is a summary. Anything that recreates a preset from pieces must be
checked against **every** field below, on the raw message, before it is trusted —
and for most jobs the right tool is not a rebuild at all but the device's own
file ops (copy / save-over), which carry the whole file.

Schema: `BinaryPreset` as embedded in `File.preset_payload` / `RecallPreset.preset`
(37 top-level fields, CorOS 4.1 pool). Manual references are the Quad Cortex
User Manual (v3.2 PDF sections; the online 4.x manual agrees where checked).
"Write" says how the field is known to be written over the wire:

- **verified** — replayed from our own handle and read back identical;
- **echo only** — seen in device→app traffic, the app's write not captured yet;
- **unknown** — no capture; needs a session with Cortex Control + the interposer.

Read-side gotcha for all of it: in a whole read `row`, `column`, `Param.index`
and the bypass map's row/column are **0** — the array position is the index.

### Quad Cortex mini (online manual, neuraldsp.com/manual/quad-cortex-mini)

Same file format, same 8 scenes and same 10 MIDI-out sources; only the
presentation differs: footswitches **A–D on two Footswitch Pages (I / II)**
("hold B to swap pages", "each footswitch can be assigned to two different
actions simultaneously"), so scenes read **AI BI CI DI / AII BII CII DII**
(protocol indices 0–3 / 4–7) and the 8 footswitch MIDI-out sources are A–D ×
page I/II. Setlist positions are shown in banks of 4 (banks of 2 when Hybrid
includes Preset mode). Manual: "up to 10 User Setlists", 256 presets each,
3072 user presets total (the device refused a new setlist at 12 folders on the
test unit — count includes the built-in ones).

### Where the fields come from (CorOS history, neuraldsp.com/news → "CorOS Updates")

| CorOS | date | what it added that lives in a preset / the grid |
|---|---|---|
| 4.1.0 | 2026-08-26 | **Device Presets** ("stores the settings of an individual Virtual Device… recall parameter values, bypass states, and expression pedal assignments while preserving the Scene assignments"; 32 user ones) → `ModelPreset`(71), not part of the preset file. **Dual Footswitch Assignments** (a device's secondary function on its own switch) → `stomp_mode_assignments[].type = SECONDARY`. I/O and Global EQ presets (global). Favorites/Recents split per category (64 each). Cortex Control: multi-device, device/I-O/EQ preset management. |
| 4.0.1 | 2026-03-04 | fixes only (48V switch, numeric keypad vs Master Volume, Global EQ persistence; CC: Downloads presets missing capture blocks). |
| 4.0.0 | 2026-01-21 | **Quad Cortex mini** support (UI adapts per hardware: Footswitch Pages I/II, Gig View opposite-page preview). Custom device name (`Version.custom_name`). Hold timing 500–1000 ms (global). Scenes dropdown. |
| 3.3.1 | 2025-12-15 | fixes only (scene change could mute/crash on some user presets; Cortex Control drops preset renaming from Gig View). |
| 3.3.0 | 2025-11-26 | **Neural Capture V2** → block `14001`, larger files; 29 virtual devices. |

Everything older (Preset MIDI Out, expression bypass, scene labels/colours,
stomp momentary, setlists) predates these notes; the v3.2 manual already
documents them.

## Preset-level fields

| # | field | what it is (manual) | write |
|---|---|---|---|
| 2 | `name` | preset name; lives in the file, set by the save (`File` CREATE `files{name}`) | verified |
| 3 | `hash` | device bookkeeping (content hash) | n/a — device sets |
| 4 | `date` | save timestamp | n/a — device sets |
| 5 | `volume` | preset-level volume (1.0 seen) | unknown |
| 6 | `pan` | preset-level pan (0.5 seen) | unknown |
| 7 | `default_scene` | boot scene, 0–7 | verified: **the scene active at save time** (Grid UPDATE is a no-op) |
| 8 | `author_name` | shown as author | not writable: Grid UPDATE no-op, `File` CREATE ignores payload; an **Unsaved slot's live preset carries the device user**, a save from it is authored by them |
| 9 | `author_id` | Cortex Cloud user id | as above |
| 10 | `tempo` | preset tempo (BPM); see also 19 | unknown (Grid UPDATE unverified) |
| 11 | `chains[4]` | the grid, one per row — see below | mostly verified |
| 12 | `tags[]` | Cortex Cloud tags ("Blues SRV Hendrix") | unknown |
| 13 | `legacy_stomp_mode_stomp_data[]` | pre-4.x stomp data | n/a |
| 14 | `scene_tempo[]` | per-scene tempo (manual: Tempo & Metronome, PRESET mode) | unknown |
| 15 | `scene_labels[8]` | scene names | verified: `SceneLabel`(23) UPDATE per scene |
| 16 | `midi_messages[12]` | **Preset MIDI Out → ON PRESET LOAD**: up to 12 messages sent when the preset loads. `MidiMessageInfo{type, channel, param1, param2, param3}`; a PC is `type:3, channel, param3:<program>` (seen: ch1 pgm106, ch2 pgm70) | **unknown — this is what the rebuild lost** |
| 17 | `midi_messages_general[10]` | older per-source table (one slot per footswitch A–H + EXP1/2) | unknown |
| 18 | `bypass[4]` | bypass map per row: `colBypass[8]{sceneMode, sceneBypass[8]}` | verified: `_write_bypass` (all scenes), `_assign_bypass_to_scenes` + per-active-scene writes |
| 19 | `tempoProgramData[]` | the preset's **Tempo & Metronome** block (`Model{hash:25000, params[…8 scene values]}`) | unknown |
| 20–21 | `layout_code_1/2` | device bookkeeping | n/a |
| 22–24 | `created_version`, `modified_version`, `oldest_compatible_version` | CorOS versions | n/a — device sets |
| 25 | `cloud_id` | Cortex Cloud id when uploaded/downloaded | n/a |
| 26 | `description` | Cloud description | unknown |
| 27 | `stomp_mode_assignments[]` | Stomp mode: block → footswitch, `type` PRIMARY/SECONDARY (4.1 dual footswitch) | verified: Grid UPDATE with only this field |
| 28 | `model_update_notifications[]` | "model updated" flags per slot | n/a |
| 29–30 | `recompile_code_1/2` | device bookkeeping | n/a |
| 31 | `scene_colors[8]` | scene colours (ARGB uint32) | verified: `SceneColor`(48) UPDATE per scene |
| 32 | `stomp_labels{}` | custom footswitch labels (stomp mode) | unknown |
| 33 | `midi_messages_general_v2[120]` | **Preset MIDI Out → footswitches A–H (12 each) and EXP 1/2** = 10 sources × 12 | **unknown — lost by the rebuild too** |
| 34 | `single_stomp_labels{}` | labels for single-function switches | unknown |
| 35 | `stomp_is_momentary{}` | latching/momentary per footswitch | verified: its own Grid UPDATE (not alongside an assignment) |
| 36 | `side_chain_follow_exists` | device bookkeeping | n/a |
| 37 | `zsk_7182_applied` | device bookkeeping (migration flag) | n/a |

## Inside a `Chain` (one grid row)

| # | field | what it is | write |
|---|---|---|---|
| 1–2 | `in_portid`, `out_portid` | lane input/output routing (`1`=In 1 … `19`=Multi Out, `16`=mix bus) | verified: Grid UPDATE with the chain's ports |
| 5 | `models[8]` | the blocks — see `Model` | verified: **one block per Grid UPDATE**, all params + all 8 scene values + `scene_mode` in that one message (strings too) |
| 6 | `split_control_points[]` | split/mix columns (−1 = none) | verified |
| 7, 8, 12 | `splitter`, `mixer`, `combined_splitter` | the split/merge nodes' params | verified: **one value per message**; scene params = assign + write per active scene |
| 9 | `output_control` | Lane Output Control (VOLUME −40..+12 dB, PAN, MUTE, SOLO), scene-assignable | verified, same rule |
| 13 | `input_control` | Input Gate Control (NOISE REDUCTION, INPUT GAIN, BYPASS, …), scene-assignable | verified, same rule |
| 10, 11 | `splitBypass`, `mixBypass` | per-scene bypass of the split/merge | echo only |
| 14 | `row` | 0–3 | — |

## Inside a `Model` (a block)

| # | field | what it is | write |
|---|---|---|---|
| 1 | `hash` | model id (catalog) | verified |
| 2 | `params[]` | see `Param` | verified in the block message |
| 3 | `bypass_expression[]` | **Expression Bypass**: pedal, min/max (manual p.33) | unknown (sent inside the block message, never checked) |
| 4 | `expression_bypass_info[]` | Heel-Toe / Switch / Stop, invert, switch delay ms, latch emulation | unknown |
| 5 | `column` | 0–7 | verified |
| 6 | `notify_flag` | "model updated" | n/a |
| 7–8 | `sidechain_sink_flag`, `sidechain_source_flag` | sidechain links (compressors, gates) | unknown |

## Inside a `Param`

| # | field | what it is | write |
|---|---|---|---|
| 1–3 | `expression`, `expression_min`, `expression_max` | **Expression Pedal Assignment**: which EXP (−1/0 = none, else pedal), sweep range; a pedal-assigned param ignores scene data (manual p.35) | unknown — see PR #19 (jnskender) which adds it |
| 4 | `scene_mode` | param assigned to scenes | verified |
| 5 | `param_values[8]` | one value per scene (float / int / string oneof) | verified |
| 6 | `index` | param index (0 in reads) | — |
| 7 | `is_led` | LED-type param | n/a |
| 8–10 | `dynamic_steps/icons/metadata` | dynamic param metadata | n/a |

## What this means for tools

- **Copy, move, rename, re-author-by-save-over** should always go through the
  device's file operations (`File` COPY / CREATE), which carry the whole file.
- A **rebuild from pieces** is only acceptable once every "unknown" row above is
  either verified or proven absent in the source preset, and the result is
  diffed on the raw `BinaryPreset` (all fields), not on `describe()`.
- Next captures needed (Cortex Control + `interceptor-win`): editing Preset MIDI
  Out (on load, a footswitch, an expression pedal), assigning an expression pedal
  and expression bypass, the Tempo & Metronome panel, stomp labels, preset
  volume/pan, tags/description.
