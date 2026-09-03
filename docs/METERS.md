# Output meters — `IOMeter` telemetry

The QC streams its I/O meters continuously as `IOMeterMessage` (command **5**). The
`output_meter` tool decodes them. No audio extra needed — this is protocol only.

Same shape as [CPU.md](CPU.md): a `request_id=0` broadcast, so it cannot be read with
`transport.request()` (that correlates on request id). Use
`QuadCortex.latest_broadcast("IOMeter", hold_s=...)`, which drops what is already queued
and then samples a window — the same drain-then-latest pattern `cpu_load` uses, factored
out so both share it.

## Message

`cortex_protobuf_v2.IOMeterMessage`, 41 fields, all `float` except one
(`proto/ProductionAutomation.proto:361-403`):

| Group | Fields |
|---|---|
| Physical in | `input_1`, `input_2`, `return_1`, `return_2` |
| XLR out | `xlr_1` + `xlr_1_limiter`, `xlr_2` + `xlr_2_limiter` |
| TRS out | `out_3` + `out_3_limiter`, `out_4` + `out_4_limiter` |
| Sends | `send_1`, `send_2` |
| Headphones | `hp_l`, `hp_r`, **`hp_limiter_active`** (the only `bool`) |
| USB | `usb_output_1l..4r`, `usb_input_1l..4r` |
| Grid taps | `grid_xlr_1/2`, `grid_out_3/4`, `grid_send_1/2` |

**The headphones have ONE limiter flag for both channels**, not one per channel — the
tool reports it as `limiters.hp` and deliberately gives `hp_l` / `hp_r` no flag of
their own.

The `grid_*` fields are the level the preset feeds each output *before* the output
stage; the plain `xlr_*` / `out_*` fields are after it. Comparing the two is the quickest
way to see whether the output level, not the preset, is the thing that is wrong.

## Usage

```
output_meter(hold_s=1.0)               # peak-hold window, play while it runs
output_meter(hold_s=2.0, detail=True)  # adds the grid taps and all USB channels
```

Returns `{samples, ports: {name: {peak, last}}, limiters: {...}, any_limiting}`. The
window is a peak hold: a longer `hold_s` catches transients a single sample would miss.

## Known unknowns

- ~~The float scale is not calibrated.~~ **Settled: IOMeter reports LINEAR AMPLITUDE
  0..1, not dB.** Established by the Patchbay Leveling bench, which converts with
  `20*log10(level)` and displays on the **−40..+12 dB** scale the QC's own OUT LEVEL
  readout uses. `output_meter` now returns both: `peak`/`last` (raw linear) and
  `peak_db`/`last_db` (clamped at the −40 floor, `None` for true zero).

  Why the floor matters: fed straight to a bar, the linear value buries everything below
  −6 dBFS in the bottom tenth of the meter — which is exactly where guitar playing
  lives.
- **The limiter flags are trustworthy** — they are booleans, and they are what the
  leveling loop's guard uses.
- **`IOMeter` is not in the handshake subscribe list** (`transport.py:96-98`), yet it is
  treated as streaming noise in the capture log tooling, so it appears to arrive
  unsolicited at least while Cortex Control is running. If a direct-mode session returns
  nothing, an explicit READ may be needed first; the tool's empty-result hint says so.
- **`GridModelMeterMessage` (command 37) carries no level field** — only `row` and
  `column` (`proto/ProductionAutomation.proto:842-847`). It looks like a selector that
  tells the device which grid cell to meter, with the value arriving elsewhere. Not
  implemented; needs a capture of the app selecting a block.

## Looper X over MIDI CC

Unrelated to metering but discovered alongside it, and relevant if the device's own
looper is ever used as a measurement stimulus. The manual documents Looper X on MIDI CC,
which the QC accepts over USB MIDI — a documented, stable control path that needs no
reversing of the protobuf `LooperStatus.state` integers:

| CC | Action |
|---|---|
| 48 | 0–63 opens Looper X (perform mode), 64–127 closes |
| 49 | Duplicate / stop duplicate |
| 50 | One Shot on/off |
| 51 | Half Speed on/off |
| 52 | Punch (0–63 out, 64–127 in/out) |
| **53** | **Record / Overdub / Stop** (64–127 acts, 0–63 stops) |
| **54** | **Play / Stop** |
| 55 | Reverse on/off |
| 56 | Undo / Redo |
| 57 | Duplicate mode: 0 free, 1 sync |
| 58 | Quantize: 0 off, 1–8 beats, 9 = 16 beats |
| 59 | MIDI clock start |
| 60 | 0 perform mode, 1 params mode |
| 61 | Routing mode (0–13) |
