---
name: optimize-preset-cpu
description: Read the Quad Cortex's per-block/per-core DSP load and reduce a preset's CPU so it fits under the ~90% ceiling. Use when a preset is CPU-heavy (Cortex Control shows a high CPU meter, or blocks/Global EQ get auto-disabled), or when building multi-amp/parallel presets that risk overloading a core. Explains how the QC reports CPU, which blocks cost most, and the edits that actually lower it.
---

# Optimize a Quad Cortex preset's CPU usage

The QC runs two DSP cores and load‑balances grid blocks across them; usable headroom is
~90%, after which it auto‑disables blocks. See `docs/CPU.md` for the full model. This
skill is the working procedure.

## 1. Measure — get the truth from the device
Call `cpu_load(detail=True)`. You get:
- `total_percent` — the headline meter.
- `by_core_weight.{core1,core2}` — summed block weight per DSP core (find the busier one).
- `blocks[]` — `{row,col,name,cpu,core}`, one per active block. **Rank by `cpu`.**

`cpu_load` per block is a *relative weight*, not a percent — use it to compare blocks,
and re‑read after each edit to measure the delta. If read‑back is flaky in bridge mode,
reconnect first (`disconnect`→`connect`); the fixed FIFO bridge otherwise stays healthy.

## 2. Know the cost order
Amps and Neural Captures (~0.5–0.6) ≫ stereo reverb (~0.38) > cabs (~0.18) >
drives/comp/EQ (~0.08–0.16). Stereo (ST) ≈ 2× the mono (M) variant.

## 3. Reduce — edits that actually work (in impact order)
1. **Delete, don't bypass.** Bypassing/disabling frees *no* CPU (manual‑confirmed). Use
   `remove_block` / rebuild without the block.
2. **De‑duplicate across parallel lanes.** The classic trap: a "shared" front‑end copied
   onto every parallel amp row (e.g. `comp→Klon→TS` × 3 = 9 instances). Put the shared FX
   on a single row **before a split** so one instance feeds the branches, or drop the
   copies. This alone often recovers 15–25%.
3. **Cut amp count / reuse.** Amps dominate; 3→2 amps is the biggest single lever.
4. **Mono over stereo.** Swap ST cabs/reverb/delay for their M variants.
5. **Balance the cores.** Keep the two heaviest blocks on opposite halves of the grid so
   neither core maxes; re‑check `by_core_weight`.
6. **Protect Global EQ.** It (and Input Gate) are dropped first on overload — leave margin
   if you depend on them.

## 4. Verify
Re‑read `cpu_load` after edits; confirm `total_percent` dropped and `by_core_weight` is
balanced. Confirm the intended blocks are still present with `get_current_preset`
(overload silently disables Global EQ / Input Gate).

## Gotchas
- Per‑block weights do **not** sum to `total_percent`; `total_percent` reflects the busier
  core. Optimize the busy core, not the sum.
- The row→core mapping is a rough default; the QC re‑balances per block, so read
  `is_on_core2` rather than assuming "rows 1‑2 = core 1".
- A preset can pass at home and lose its Global EQ elsewhere — always leave headroom.
