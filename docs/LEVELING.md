# Loudness metering and preset leveling

Level presets by measurement instead of by ear. Patchbay/the MCP plays one riff of
yours into every preset, measures what comes back, and trims each one until they sit
at the same loudness.

Needs the optional audio extra:

```bash
pip install -e '.[audio]'
```

All of it ships as prebuilt wheels on macOS arm64 / CPython 3.14 — no compiler, and
PortAudio is bundled inside the `sounddevice` wheel. `matplotlib` is in the list only
because **mosqito imports it without declaring it**; drop it and Zwicker loudness fails
at runtime. Without the extra the server still starts and every audio tool returns a
clear "install the audio extra" error.

## The workflow

1. **Record a reference riff, once.** `sample_arm()` → play → it stops on its own.
2. **See what needs changing.** `suggest_levels(...)` — report only, writes nothing.
3. **Apply.** `level_preset()`, `level_scenes()`, or `level_setlist([...])`.
4. **Save** when you're happy (`save=True`, or `save_preset()` separately).

## USB channel map (manual 4.1.0, "USB Channels")

Host-side numbering — what `sounddevice`'s 1-based `mapping` uses.

| Host records (in) | Source | Grid-selectable |
|---|---|---|
| 1, 2 | Dry DI from analog Input 1 / 2 | no |
| 3, 4 | **Analog Output 1/L and 2/R** | no |
| 5–8 | **Grid USB Output 5–8**, one per output block | **yes** |

| Host plays (out) | Destination | Grid-selectable |
|---|---|---|
| 1, 2 | Analog Out 1/L, 2/R | no |
| 3, 4 | Analog Out 3/L, 4/R | no |
| 5–8 | **Grid USB Input 5–8**, pick on any input block | **yes** |

Two rules follow, and the code enforces both:

- **Measure on host 5/6, never 3/4.** Inputs 3/4 are fed from the *analog outputs*, so
  they ride the output stage — master volume would fold into the reading and the loop
  would end up levelling the master rather than the preset. Host 5–8 come from
  dedicated grid output blocks and bypass it.
- **Never play the stimulus on host outputs 1–4.** Those go straight to the analog
  jacks, bypassing The Grid: full-level audio into whatever you are monitoring on.
  `play_and_record` raises rather than doing it.

The Dry/Wet swap in I/O Settings only changes what 1/2 vs 3/4 carry, and the manual
notes channel names update on the host only after reconnecting or power cycling.

## The measurement rig

```
Mac  --host out 5/6-->  Grid USB Input 5/6    (in_portid 12, on the HEAD lane)
                              |
                        preset under test
                              |
Mac  <--host in 5/6---  Grid USB Output 5/6   (out_portid 14, on the TAIL lane)
```

**The head and the tail are usually different lanes.** A two-row preset takes In 1 on
row 0, hands off over the internal bus (`out_portid` 16–18), and leaves from row 2.
Swapping both ends of one row would cut the chain in half and measure nothing.
`autolevel.measurement_rows()` finds them: the first lane on a physical input, and the
last lane with blocks whose output is not internal. Pass `in_row`/`out_row` to override.

Both ends are temporary. `reamp_routing` records the original ports and restores them in
a `finally`, so an aborted run never leaves a preset wired to USB and silent to the
player. `feed=False` taps only the output, leaving the instrument input alone — the shape
for measuring what the player is actually playing.

**A measurement is silent in the room.** While the tail lane's output is tapped to USB it
is no longer going to the XLRs, so nothing reaches the monitors during a run.

### Verified on hardware (2026-09-03, CorOS 4.1.0, preset "Blackmore")

    rows: input row 0, output row 2
    pass 1: -13.29 LUFS, off by -4.7 dB
    pass 2: wrote -9.59 dB -> -17.92 LUFS, off by -0.08 dB — converged

Routing and the fader were both restored afterwards.

## What gets trimmed

A **Gain** block (hash `16005`, param 0 `LEVEL`, −60..+12 dB) appended to the measured
lane, added automatically if absent. Amp master, cab and drive are never touched —
those change the tone, not the level.

`LEVEL` carries a JUCE `skew` of 3.8018, so 0 dB sits at normalized **0.5** exactly, a
deliberate unity centre detent. `catalog.py` honours the declared skew on all 773
skewed parameters; before this it treated them as linear and a requested "+6 dB" landed
somewhere else entirely.

Per-scene leveling writes the same parameter through `set_param_scenes`, which assigns
it to scenes first and confirms each scene switch before writing — a fixed sleep races
the device and silently drops values.

## Metrics

| Field | What it is |
|---|---|
| `lufs_integrated` | ITU-R BS.1770 K-weighted, −70/−10 gated. The default target. |
| `lufs_short_term_max/p95` | 3 s windows. Approximated — see below. |
| `true_peak_dbtp` | BS.1770-4 Annex 2, 4× oversampled. Drives the guard. |
| `rms_dbfs`, `sample_peak_dbfs` | Plain, for sanity checks. |
| `zwicker_n5_rel`, `zwicker_n50_rel` | ISO 532-1, **relative** sones. Opt-in. |
| `spectral_balance` | Energy in 9 bands, dB relative to the whole signal. Opt-in. |
| `crest_db` | Peak-to-RMS. High = dynamic and peaky, low = compressed and dense. |

**Why two loudness metrics.** Two presets at equal LUFS do not sound equally loud when
one is dense distortion and the other is a clean. LUFS is the cheap, standard,
reproducible number; Zwicker is closer to the ear. Pass `metric="perceived"` to level by
it — but it costs about 0.9× realtime, so a 7 s riff takes ~6 s to analyse.

**Honest limits.**

- Short-term loudness is an approximation: pyloudnorm exposes only *gated* integrated
  loudness, so each 3 s window is measured with its own meter. A real short-term meter
  is ungated. Fine on a window that is mostly signal, less so on one straddling silence.
- Zwicker values are **relative**. There is no acoustic calibration between dBFS and
  Pascals, so the sone figures compare our own captures to each other and to nothing
  else. Never present them as calibrated sones.

## Failure modes the code refuses to paper over

- **Digital silence** → reported as an error, never as a quiet preset. On macOS a
  *denied microphone permission returns silence rather than failing*, so an all-zero
  capture almost always means permission, not level.
- **Below the −70 LUFS gate** → integrated loudness is −inf, so the tools say the signal
  gates out rather than reporting nothing. Usually the wrong channels, or nothing routed
  to that output.
- **Shorter than 0.4 s** → BS.1770 needs at least one block; reported as an error.
- **True peak above −1.0 dBTP** → the loop backs the trim off and marks the preset
  `limited_by: "true_peak"` instead of pushing it into the output limiter.

## Microphone permission

macOS attributes a TCC microphone grant to the *responsible process*, not to the python
that opens the stream. Running from a terminal inherits that terminal's grant. When
**Patchbay** spawns the MCP server, the request is attributed to Patchbay, which needs:

- `NSMicrophoneUsageDescription` in the packaged Info.plist (electron-builder
  `mac.extendInfo`). Without the string the prompt never appears and the read is denied.
- Possibly `com.apple.security.device.audio-input` under Hardened Runtime — **not
  established**. SingZ ships a notarised, hardened-runtime Electron app that captures
  audio and does *not* declare it. Settle it by test: add only the usage string, build
  signed locally, try a capture, and add the entitlement only if that fails (it costs a
  re-notarisation).

## Tools

| Tool | What it does |
|---|---|
| `output_meter` | Device IOMeter: per-port peak-hold + limiter flags. No audio extra needed. |
| `audio_devices` | CoreAudio devices, flagging the Quad Cortex. |
| `sample_arm` / `sample_status` / `sample_stop` / `sample_discard` | The looper-style reference recorder. |
| `measure_loudness` | Measure whatever is currently coming out. |
| `measure_preset` | Play the riff into the current preset and measure. |
| `suggest_levels` | Report-only table of corrections. |
| `level_preset` | Close the loop on one preset. |
| `level_scenes` | Per-scene trims for the current preset. |
| `level_setlist` | Unattended pass over several presets. |
| `compare_presets` | Is the difference LEVEL or TONE? Report only. |

## Level or tone?

`compare_presets` plays the same riff through each preset and reports the loudness gap,
a 9-band spectral difference (sub / low / low-mid / mid / upper-mid / presence / edge /
brilliance / air), the crest factor, and a verdict:

| Verdict | Meaning |
|---|---|
| `match` | No meaningful difference. |
| `level` | Trimming will fix it. |
| `tone` | Trimming will **not** fix it — the EQ, cab or drive is what differs. |
| `both` | Level it first, then compare again; what is left is genuinely tonal. |

Spectral balance is normalised against the signal's own total energy, so it is
level-independent by construction: a pure gain change leaves every band untouched. That
is what lets the verdict separate the two causes instead of confusing them. A band has
to move by 3 dB or more, in at least two bands, before it counts as tonal.

## Tests

```bash
.venv/bin/python tests/test_loudness.py     # metrics, gates, channel safety
.venv/bin/python tests/test_sampler.py      # sampler state machine (fake sounddevice)
.venv/bin/python tests/test_leveling.py     # the loop: convergence, guards, routing
.venv/bin/python tests/test_meters.py       # IOMeter decoding
.venv/bin/python tests/test_catalog_skew.py # the dB taper
```

They run without the audio extra — the ones needing numpy skip cleanly.
