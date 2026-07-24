---
name: review-preset
description: Comprehensively review a Quad Cortex preset for correctness, tone, efficiency, and faithfulness — signal integrity/routing, I/O, gain-staging/clipping, stereo, CPU, model/cab choices, parameter sanity, scenes, and metadata. Use to audit a preset you (or someone) built before saving/shipping, to diagnose "it sounds wrong/quiet/harsh", or as the final gate of a build. Produces a grouped findings list with severities and concrete fixes.
---

# Review a Quad Cortex preset

A structured audit across every design aspect. **Ground it in the live device**, don't
eyeball — pull the real state first, then walk the checklist, then report findings by
severity with concrete fixes.

## Gather the ground truth
- `get_current_preset` — blocks, per-chain `in_port`/`out_port`/`split_points`, block
  params (display units), and per-lane `input_block`/`output_block` (VOLUME/PAN/MUTE/SOLO).
- `cpu_load(detail=True)` — `total_percent`, `by_core_weight`, per-block cost + core.
- `get_io_settings` — physical in/out config.
- **Screenshot the grid** (`tools/gui/gui.py home && shot`) — the only reliable way to see
  the actual wiring (orphaned lanes, merges). Read-vs-delta: in a full read, row = chain
  index, column = models-array index.
- If scenes matter, `switch_scene(0..7)` and re-read to inspect per-scene values.

## The checklist (each item = a potential finding)

### 1. Signal integrity & routing  — *critical*
- Every block reachable: no **orphaned lanes** (in=0 with no split feeding them) — they
  draw CPU but make no sound. Confirm on the screenshot.
- Splits/merges correct: shared blocks are **before** the split; each parallel amp is fed
  and merges back; no signal loops or a lane feeding another lane's input.
- Post-merge FX (reverb/delay meant to be shared) sit **after** `mix_col`, not on one lane.

### 2. I/O  — *critical*
- Input taps the right source (In 1 = guitar). Output routed to a real destination (Multi
  Out / Out 1/2 as intended) — not left on an unassigned route that's silent at the mains.
- Input Gate / Global EQ present as intended (they auto-disable on CPU overload).

### 3. Gain-staging & clipping  — *major*
- **Parallel lanes SUM** — N amps at unity clip. Each parallel lane's output VOLUME should
  be reduced (~−20·log₁₀N; ≈1/3 for three). Check the **output meter under playing**.
- Amp OUTPUT / lane VOLUME level-matched so no single amp dominates the blend.
- Drive/boost stack isn't producing unintended mush (see §6).

### 4. Stereo & panning  — *major*
- If stereo intended: are lanes actually **panned** (branch amps L/R, output row centered)?
  All-centered = mono blend. Reverb/FX stereo (ST) if width is wanted.
- Pan not overdone ("not 100%") unless deliberate.

### 5. CPU / DSP efficiency  — *major*
- `total_percent` under ~90% with headroom (leave room for Global EQ, which drops first).
- `by_core_weight` **balanced** — not one core maxed. Heaviest blocks (amps) split across.
- No waste: **duplicated blocks** across parallel lanes (share before a split instead),
  stereo (ST) where mono (M) would do, bypassed-but-not-deleted blocks (bypass ≠ CPU save).

### 6. Blocks, models & tone  — *major*
- Right models for the intent (`based_on` matches the target gear); sensible **cab/speaker
  pairings** (or intentional mismatches).
- **Amps dialed**, not left at defaults: gain/volume/EQ voiced for the part; amps in a blend
  voiced to complement, not clash.
- **Drives:** correct count active — a transparent boost (Klon) is low-gain/high-output;
  don't leave multiple ODs hard-on stacking gain unless intended. Consider making drives
  **scene-switched** rather than always-on.
- FX (reverb/delay/mod) mix/feedback/time musical, not default extremes.

### 7. Scenes  — *major (for performance presets)*
- Does the preset need a **tone palette** (clean/crunch/lead/heavy/ambient) and does it have
  them? A single static tone is often the biggest gap.
- Scene deltas are coherent: lead = boost on + **volume bump** + more time-FX; drives
  bypassed per-scene where they shouldn't sound; signal chain **identical** across scenes.
- Scenes named/colored; `default_scene` sensible.

### 8. Metadata & housekeeping  — *minor*
- Preset **named**, tempo set if tempo-synced FX are used, scene labels filled.
- Saved to the intended slot (and not silently overwriting another preset) — target
  an **empty slot** (`list_empty_slots`), never the user's named presets.
- **Save actually committed**: a save-tool success string is not proof. Verify the app
  header shows the preset name **without the `*` dirty marker** (screenshot), and/or the
  slot occupant via the directory. Saves go through File CREATE (see PROTOCOL.md §6);
  RecallPreset SAVE hangs on an Unsaved slot.

### 9. Faithfulness to intent  — *major*
- Matches the brief / researched rig (right number of amps, chain order, always-on pedals,
  era-correct gear, tone character). Cross-check against the source (see research-guitar-rig).

## Report format
Group findings by **severity** — *critical* (broken/silent), *major* (tone/usability/
efficiency), *minor* (polish) — each with the concrete fix and, where useful, the measured
value. Lead with what's right, then the fixes ranked. End with a one-line verdict and the
single must-fix. Re-run the relevant checks after fixes to confirm.

## Common real problems (seen in practice)
- Orphaned amp lane (split is 1→2; the 3rd amp never got fed) — screenshot catches it.
- A shared block placed **after** the split, so branches miss it (e.g. the TS only hit one amp).
- All amps centered → mono; or all at unity → the sum clips.
- Duplicated pedals across lanes wasting 15-25% CPU; one core maxed while the other idles.
- Single static tone with every drive on — no scenes, too much stacked gain.
- Output left on an unassigned/"Multi Out" route → silent at the mains.
- Unnamed/unsaved, or a build that **merged onto stale state** (ghost/duplicate blocks) —
  always verify the read matches intent.
