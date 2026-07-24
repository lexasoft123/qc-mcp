---
name: build-preset-routing
description: Build Quad Cortex presets with correct grid routing, I/O, and multi-amp/parallel topologies — and verify them. Use when creating or editing presets (especially multi-amp, parallel, or shared-front rigs), wiring inputs/outputs, sharing one pedal chain across parallel amps, or whenever a build "doesn't take" / reads come back empty. Encodes the routing rules, I/O conventions, and the reliability tricks that make building actually work over the bridge.
---

# Build QC presets: routing, I/O, and parallel topologies

The grid is **4 rows × 8 columns**, fed by two DSP cores (see `optimize-preset-cpu` /
`docs/CPU.md`). Building reliably needs three things right: the **routing model**, the
**I/O conventions**, and the **read/verify loop**. Get any wrong and you ship silent or
mis-wired presets — every failure below was hit for real.

## 0. Always verify — never trust the write
`build_preset`'s own trailing read is unreliable (it fires amid the edit-echo storm).
After any build/edit, **read back with `get_current_preset`** (now includes
`split_points` per chain) AND, for topology, **screenshot the grid**:
`tools/gui/gui.py home && tools/gui/gui.py shot out.png` then look at the wiring.
Cross-check with `cpu_load` (its block list reveals duplicates / orphaned blocks).

## 1. Routing model
- **Lane ports** — `in_port` / `out_port` per chain (row). Full `out_port` map (verified by
  clicking through Cortex Control's output block and reading `chains[i].out_port`):

  | out | dest | out | dest | out | dest |
  |----|------|----|------|----|------|
  | 1 | Out 1/2 | 8–9 | Send 1 / 2 | 16 | **Row 3** |
  | 2 | Out 3/4 | 10–13 | USB 5–8 | 17 | **Row 4** |
  | 3 | Send 1/2 | 14 | USB 5/6 | 18 | **Row 3/4** |
  | 4–7 | Out 1 / 2 / 3 / 4 | 15 | USB 7/8 | 19 | **Multi Out** |
  |     |         | 20–22 | USB 3 / 4 / 3‑4 |    |          |

  So **`out_port=16/17/18` route a lane's output *into* Row 3/4** (the "patch‑cable to a
  row" jumper), `out_port=19` = Multi Out, and physical jacks are 1‑9 — e.g. `out=7` is the
  **Out 4 jack**, NOT a row (a trap). On the INPUT side, **`in_port=7` = "Prev. row"** (take
  the row directly above's output); `in_port=1` = In 1. Idle branch lanes: `in=0, out=0`.
- **Parallel split** — `split_control_points {split, mix}` per row (in `build_preset`:
  `"splitter":{"split_col":S,"mix_col":M}`). `split=S` branches the signal **before the
  block at column S**; `mix=M` merges the branch back at column M. `(-1,-1)` = none.
  **A split is 1→2 only.** The underlying law (per the official manual): **rows 1&3 are
  "Path A", rows 2&4 are "Path B"; splitters only route Path A → Path B.** That's WHY a
  branch (Path B) row can't host its own split (rejected), while row 3 — even when
  patch-fed via in=7 — CAN split (it's Path A).
- **Read-vs-delta indexing:** in a full READ, row = chain-array index, column =
  models-array index, param index = params-array index (the `.row/.column/.index`
  *fields* are 0 there). In edits you set those fields explicitly.

## 2. Multi-amp / parallel patterns
- **N parallel amps, no shared front** (simplest, most robust — the "SRV multiamp"
  skeleton): each amp lane `in=1` taps the input directly; one lane is the **merge/output
  lane** `in=0, out=19` carrying shared post-FX. All amp lanes `out=0` sum into it.
- **Shared front-end feeding parallel amps** (saves CPU — one pedal chain, not N):
  put the shared blocks on row 0, then **split AFTER them**. `split_col` = the column of
  the **first block that should NOT be shared** (pedals at cols 0-2 → `split_col=3`, so
  the amp at col 3 and the branch both get the post-pedal signal). Splitting at col 2
  would leave the col-2 block (e.g. the TS) out of the branch — a classic bug.
- **3+ parallel amps sharing ONE pedal group before all of them** (verified against a
  hand-built reference). Do NOT nest `scp` splits on a *split-branch* (rejected). The
  working recipe uses a **no-merge tap → empty jumper row → split a patch-fed lane**:
  1. **Row 1:** In 1 → shared pedals → amp 1 → cab → reverb → `out=19` (Multi Out), with
     **`split_col` after the pedals and `mix_col = -1` (NO merge)**. The `-1` is critical:
     the split *only taps* the post-pedal signal down to row 2; row 1 continues to amp 1
     independently. (A real `mix_col` would merge the tap back and pollute the amp-1 lane —
     the classic bug.)
  2. **Row 2 = empty jumper:** receives row 1's tap; set its output to **`out=16` ("Row 3")**
     → feeds row 3.
  3. **Row 3 (amp 2):** `in_port=7` ("Prev. row" = the jumper). Give THIS lane its own
     **split `scp=(0, M)`** — split at col 0 (its input, pre-amp) to feed row 4, merge the
     return at col M (after the cab, before this lane's reverb). A **patch-fed lane (in=7)
     CAN be split** even though a split-branch can't — this is how the 3rd amp gets the
     shared signal in parallel. Then amp 2 → cab → reverb → `out=19`.
  4. **Row 4 (amp 3):** `in_port=7`, fed by row 3's `scp` split; amp 3 → cab → merges back
     into row 3 at col M.
  So one `comp→Klon→TS` feeds all three: amp 1 on row 1, amps 2+3 via jumper→row 3→split.
  Put reverb per amp-group (row 1's amp; the row 3+4 merge) so every amp is wet. Verify by
  screenshot: each amp row reads "Prev. row"/In → "Multi Out"; none should be **series**
  ("Prev. row" pointing at another amp's output) or **orphaned** (output on `+`).
  (`out=17`="Row 4", `out=18`="Row 3/4" also route to rows but the split-the-lane method
  above is the confirmed-correct one. Simpler alternative that does NOT share the front: the
  SRV 2-stage merge — amps tap `in=1`, drive on one lane, mix `out=16`→`out=19`.)
- **Branch-row column budget:** a branch that merges at column M reserves the columns
  between its last block and M as "runway"; it can't hold blocks all the way to M. If a
  block silently won't land at the end of a branch row, move the merge column out or
  place the block earlier.
- **Post-merge (shared) FX — e.g. a stereo reverb for the whole blend:** place it on
  the output row **after `mix_col`** so it processes the merged signal, not one lane. FX
  before the merge only affect that lane (why one amp ended up "dry" of the delay once).

## 3. I/O and stereo
- Output "Multi Out" (`out_port=19`) is this device's main out — leave it unless told.
- **Stereo comes from panning the lanes**, not from the output alone. Two places to pan:
  - **Lane Output Control** — params `VOLUME=0, PAN=1, MUTE=2, SOLO=3` (PAN 0.0=hard L,
    0.5=center, 1.0=hard R). Use `set_lane_output(row, pan=…, volume=…)`.
  - **Splitter / mixer blocks** — these also carry a pan/balance param, so you can
    position the branches at the split/merge instead of (or with) the lane output.
  Pan the **branch** amp lanes (e.g. one left, one right) and leave the **main/output
  row centered** — panning the output row pans the whole mix. "Not 100%" → ~0.3 / 0.7.
- Mono (M) cabs + a stereo (ST) reverb is a cheap way to get width; ST cabs cost ~2×.
- **Gain-staging — don't clip the sum.** Parallel lanes **add** at the mixer, so N amps
  each at unity overloads the output. **Reduce each parallel lane's output volume** (Lane
  Output Control VOLUME, or the mixer level) so the summed level stays below clipping —
  order of −20·log₁₀(N) dB, e.g. ≈1/3 for three amps (default lane VOLUME is 0.769 → drop
  the amp lanes to ~0.4–0.5). Verify against the output meter; leave headroom for the reverb.

## 4. Build gotchas that cause silent failures
- **`clear_grid` needs working reads.** It deletes what it reads; if reads are empty,
  it clears nothing and the next `build_preset` **merges onto the old grid** (ghost
  blocks, duplicated amps). Confirm reads work first.
- **`add_block` de-dupes:** re-adding a hash already present on that row is a no-op. To
  move a block, delete it first (or place a different hash); don't "overwrite" with the
  same model.
- **Whole-preset Grid UPDATE merges**, doesn't replace — build incrementally or clear.
- Recalling an empty slot doesn't always reset the live grid over the bridge; use
  `clear_grid` + verify.

## 5. Reliability — reads must work or nothing above is verifiable
The bridge (sharing Cortex Control's session) is what makes live building possible; it
was made robust this way (`bridge.py`, `transport.py`):
- Out FIFO opened **O_RDWR** so the reader never hits EOF when the app's forwarder
  blinks; the reader thread **self-heals** (reopens, never dies).
- Reads correlate on **`request_id`** — the device echoes it on solicited replies, so a
  buffered/older push or the app's own traffic can't be mistaken for our response. Our
  ids use a high base to avoid colliding with the app's.
- **Streamed telemetry** (CPULoad, IOMeter) is `request_id=0` broadcast — take the
  **latest** message, not the first buffered one (drain, then read).
- If reads still come back empty, **reconnect** (`disconnect`→`connect`) to revive a
  stale bridge session; `get_current_preset` also auto-reconnects+retries once.

Bottom line: **build → read back split_points + routing → screenshot → cpu_load**. Only
then is a preset "done."

## MCP tools — the complete interface (no scripts needed)
The bridge is reliable now, so **do everything through MCP tools** — never fall back to
ad-hoc scripts for device control. The full toolbox:
- **Blocks:** `build_preset` (whole topology), `add_block` / `remove_block`, `clear_grid`,
  `add_capture`, `add_ir`.
- **Params (display units):** `set_parameter(row, col, index, value)`. Read a model's param
  names/order from `get_current_preset` or `find_devices` before setting indices.
- **Routing / stereo:** `set_lane_routing(row, in_portid, out_portid)`,
  `set_lane_output(row, pan, volume, mute, solo)`, `add_split` (splits are also set via
  `build_preset`'s `splitter`).
- **Scenes:** `set_parameter_scenes(row, col, index, values[8])` (assign-to-scenes + write;
  the tool confirms each scene switch via Scene READ — racing drops values). Scene the AMP
  params too (gain ladder, lead master bump, EQ trims), not just drive bypass.
  `set_block_bypass(row, col, bypassed, scenes=[…])` (per-scene bypass — works on drives/
  amps, silent NO-OP on delays: scene the delay MIX instead), `switch_scene(0-7)`,
  `set_preset_meta(scene_labels=[…], scene_colors=[…])` — labels/colors go over dedicated
  SceneLabel/SceneColor ops (verified); scenes with data but no label show "Undefined".
  Preset `name` is NOT settable live — it's set by the save op (pass it to save_preset/_as).
- **Slot picking:** `list_empty_slots(setlist_key)` — free "Unsaved" positions (with
  bank labels like 4E). Always build/save into one of these, never a named preset's slot.
- **Save:** `save_preset(name)` / `save_preset_as(name, setlist_key, position)`. BOTH now
  persist via the app's real op — **File CREATE** (`File{action=CREATE, folder{key,
  files{index=<position>, name}}}`, **no preset_payload**; the device commits its live
  working grid). Decoded from Cortex Control's own Save. **Never** save via *RecallPreset
  UPDATE reason=SAVE* — it **hangs the device** on an empty/Unsaved slot (had to reboot).
  The tool's success string is not proof of commit: verify with `current_preset_position`
  + the app header showing the name **without a `*`** (dirty marker). See the
  `save-empty-slot-hangs` memory.
- **Read / verify:** `get_current_preset`, `cpu_load(detail)`, `get_io_settings`,
  `current_preset_position`. The only non-MCP helper is `tools/gui/gui.py shot` for a
  *visual* wiring screenshot — verification, not control.

If an operation has no tool yet, **add the MCP tool** rather than scripting it — keep the
MCP the single functional interface. When a write seems to succeed but doesn't take
(e.g. Grid UPDATE with preset-level scene_labels was a silent no-op), **capture the op
from Cortex Control** (logmark → do it in the app → decode) and match it exactly.
