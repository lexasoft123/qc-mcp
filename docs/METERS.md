# Output meters — `IOMeter` telemetry

The QC streams its I/O meters as `IOMeterMessage` (command **5**) while sound moves. The
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

Returns `{samples, ports: {name: {peak, last, peak_db, last_db}}, over_full_scale, limiters: {...},
any_limiting}`. The
window is a peak hold: a longer `hold_s` catches transients a single sample would miss.

## Known unknowns

- **The levels are a dB scale, not a linear amplitude: value = 1 + dB/40**, 0 at
  −40 dB of full scale and below, 1.0 at full scale (measured 2026-11-19, Quad Cortex
  mini, CorOS 4.1.1). A 440 Hz tone over USB 5/6 through an empty row to Multiple
  Outputs, on `xlr_1`:

  | Tone (dBFS) | −40 | −30 | −20 | −12 | −6 |
  |---|---|---|---|---|---|
  | `xlr_1` | no frame | 0.25 | 0.50 | 0.70 | 0.85 |

  `xlr_2`, `out_3`, `out_4` read the same; `hp_l` / `hp_r` read 0.044 higher (1.8 dB)
  at that headphone level. Cortex Control's own OUT LEVEL readout agrees: −10.4 dB
  while `grid_xlr_1` read 0.735 (2026-11-22). `output_meter` returns `peak`/`last`
  (raw) and `peak_db`/`last_db` (`None` for 0, i.e. −40 dB or below).
  Before this was measured, the tool and the bench read the value as a linear amplitude
  (`20*log10`): −20 dBFS showed as −6 dB, −6 dBFS as −1.4 dB.
- **Over full scale** (2026-11-22, a sine pushed past it by EQ output gain): the jack
  fields and their `grid_*` taps go past 1.0 — a −1 dBFS sine read 1.044 at the row's
  output (about +1.8 dB), 12 dB more read 1.386 — while the USB fields stop at exactly
  1.0 (0.992 at −0.2 dBFS). `output_meter` lists the ports past 1.0 as
  `over_full_scale`. No other message comes when an output clips.
- **The limiter fields carry no known meaning.** `xlr_1_limiter` … `out_4_limiter`
  read 1.0 and `hp_limiter_active` true in every frame, silence included (2026-11-19
  and 2026-11-22, CorOS 4.1.1): they are no clipping flag. `output_meter` still passes
  them through (`limiters`, `any_limiting`), and the bench's dock lights "limiting" from
  them.
- **Which field is which output** (2026-11-22): Output 1/2 feeds `grid_xlr_1/2`, Output
  3/4 `grid_out_3/4`, Send 1/2 `grid_send_1/2` (within 0.01 of `send_1/2`). Multiple
  Outputs feeds `grid_xlr_1/2`, the four jacks' own fields (no `grid_out_3/4`) and USB
  3/4 (`usb_output_2l/r`). `usb_output_N` is USB pair N (USB Out 5/6 is
  `usb_output_3`). `grid_xlr_1/2` read as `xlr_1/2` whatever the Master Volume (90 %
  tried): the meters show the signal at the output block, not after the knob.
- **The meters sit after the lane's mute**: a row to Multiple Outputs with its lane
  output muted sends no output field at all, `grid_*` included.
- **Frames come only while sound moves**, about 11 a second, none in silence. After a
  short peak (0.05 s or 0.2 s, 12 dB past full scale) the value stays over 1.0 for
  about 0.5 s (6–7 frames).
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
