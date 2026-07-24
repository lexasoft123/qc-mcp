# Quad Cortex CPU / DSP usage — how to read it and how to minimize it

## Hardware
The QC's audio DSP is **two Analog Devices ADSP‑SC589** parts → **4× SHARC+ cores**
(plus 2× ARM Cortex‑A5 for the OS/UI). For preset purposes the model that matters is
**two DSP "cores"**: the device assigns every grid block to one of the two and
load‑balances between them. Usable headroom is roughly **90%**; past that the QC
starts auto‑disabling blocks (see below).

## How the device reports CPU (what the MCP reads)
`CPULoadMessage` (streamed continuously, `ProductionAutomation.proto`):
- `cpu_total_load` — the headline percentage shown in Cortex Control's top‑right meter.
- `chains[]` → one per grid **row** (0‑3), each `columns[]` → one per **column** (0‑7):
  - `cpu_load` — that block's cost as a **relative weight** (not a percent; the
    per‑block weights don't sum to `cpu_total_load`). Use it to rank blocks.
  - `is_on_core2` — which of the two DSP cores the block currently runs on.

`cpu_load(detail=True)` in the MCP parses this into `{total_percent, by_core_weight,
blocks[]}`, labelling each block by name from the current preset.

### Observed costs (relative weight units)
| block class | approx cost | notes |
|---|---|---|
| Amp model | 0.5 – 0.6 | the dominant cost (e.g. Dumbbell ODS 0.60, Bassman 0.54) |
| Neural Capture | ~amp‑class | similar order to an amp |
| Stereo reverb (ST) | ~0.38 | spring‑reverb engine measured 0.38 |
| Cab (M) | ~0.18 | mono impulse |
| Drive / comp / EQ | 0.08 – 0.16 | Klon 0.15, comp 0.16, TS‑808 0.08 |

## Core assignment — the real rule
A common community heuristic is "rows 1&2 on one core, rows 3&4 on the other."
**In practice the QC balances _per block_, not strictly per row** — measured live, both
heavy amps (on rows 0 and 3) were pushed onto **core2** together while the lighter
blocks stayed on core1, so `is_on_core2` was mixed _within_ a row. Treat the row→core
pairing as a rough default the balancer overrides; the number that limits you is the
**busier core**, which is what `cpu_total_load` tracks.

`GLOBAL EQ` and the `INPUT GATE` blocks run on the shared/first core and are the
**first things the QC auto‑disables when a preset exceeds available resources**
(confirmed in the manual). So a preset that runs fine may silently lose its Global EQ.

## Minimizing CPU — rules that actually work
1. **Delete, don't bypass.** Per the manual, *"bypassing or disabling blocks does not
   reduce CPU consumption."* Only removing a block frees its cost.
2. **Don't duplicate blocks across parallel lanes.** A shared front‑end that hits three
   parallel amp rows as three separate `comp→Klon→TS` copies is **9 pedal instances** —
   measured ~1.15 of total load, most of it avoidable. Put shared FX *before* a split so
   one instance feeds all branches, or drop the redundant copies.
3. **Amps/captures dominate.** Fewer amps = the biggest win. Three amps is inherently
   ~1.7 units before any FX. If CPU‑bound, drop to two, or reuse one amp model.
4. **Prefer mono (M) over stereo (ST).** Stereo blocks roughly double the cost. Use mono
   cabs and mono time/reverb unless you truly need stereo width.
5. **Balance heavy blocks across the two cores.** Keep the two heaviest blocks (amps) on
   opposite core‑halves of the grid so neither core maxes; the balancer mostly does this
   but preset structure (which rows are paired/merged) constrains it.
6. **Watch Global EQ headroom.** If you rely on Global EQ, leave margin — it's dropped
   first on overload.

## Reading it from the MCP
```
cpu_load()                     # {total_percent, by_core_weight, blocks:[{row,col,name,cpu,core}]}
```
Rank `blocks` by `cpu` to find what to cut; compare `by_core_weight.core1` vs `core2`
to see the balance. Re‑read after each edit to measure the delta.

Sources: Neural DSP QC manual (CPU monitor; Global EQ/Input Gate auto‑disable; bypass
≠ CPU savings); Analog Devices ADSP‑SC589 / SHARC+ architecture notes; live
`CPULoadMessage` telemetry from the device.
