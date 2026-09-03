# Plan — loudness metering + automatic preset leveling

## 🎯 GOAL
**Level presets and scenes by measured loudness instead of by ear.** Two layers:

1. **Device meters** — decode the QC's streamed `IOMeter` telemetry into an MCP tool
   (per-port peak + limiter flags) so builds/reviews can check "signal present / clipping".
2. **Real loudness** — capture the QC's USB audio output on the Mac, compute LUFS
   (BS.1770), true peak and ISO 532-1 Zwicker loudness (N5/N50), and close the loop:
   play a fixed reference DI into the grid through the USB reamp input, measure, adjust
   the post-chain level, verify. Per scene, per preset, per setlist.

Picked from PPI Analyzer (https://www.ppianalyzer.com, a €19 offline file analyser that
prints a per-file dB correction and leaves the user to apply it by hand):
- **Perceived loudness alongside LUFS** — a dense high-gain tone and a clean tone at
  equal LUFS do not sound equally loud. Report Zwicker N5/N50 next to LUFS, and let the
  loop target either.
- **Report-only mode** — "suggested correction in dB per preset" without writing.
- **Timbre/spectral-balance diff vs a reference** (later phase) for `review-preset`.

What we do that it cannot: we control the stimulus (fixed DI through the reamp path, so
plain LUFS comparisons are valid) and we write the correction to the device ourselves.

**The stimulus is the user's own riff, recorded like a looper pedal** — no bundled
clip. Two sources, both in the plan:
- **Mac-side looper** (P2, the foundation): arm → auto-start on signal → stop on
  silence / fixed length / explicit stop → auto-trim → saved as the reference DI →
  loop-preview through the current preset. Persists across presets and setlists.
- **Device looper** (P2b, alternate): the QC's native `Looper X` block (hash 27000)
  driven over `LooperMessage` (cmd 28); the loop plays through the whole preset
  without any Mac→QC audio path. Depends on loop persistence across recalls (open).

Each phase is self-contained: read the cited files/lines first, copy the cited patterns,
run the phase's verification. All paths relative to the repo root. Python is
`.venv/bin/python` (the worktree may lack `.venv` — use the main checkout's).

---

## Phase 0 — Documentation discovery (DONE; consolidated here)

### Device facts (verified sources)
- QC USB audio: **48 kHz fixed, 24-bit, 8 in / 8 out** (Neural DSP manual 4.1.0 and
  https://neuraldsp.com/getting-started/using-quad-cortex-as-an-audio-interface).
- **USB channel map — SETTLED** from the manual's "USB Channels" section (4.1.0) and the
  official `USB_IO_schematic` table. Host-side numbering:

  | Host records (DAW in) | Source | Grid-selectable |
  |---|---|---|
  | 1, 2 | Dry DI from analog INPUT 1 / INPUT 2 | no |
  | 3, 4 | **analog OUTPUT 1/L and 2/R** (the wet feed) | no |
  | 5, 6, 7, 8 | **Grid's USB OUTPUT 5–8** — one per output block | **yes** |

  | Host plays (DAW out) | Destination | Grid-selectable |
  |---|---|---|
  | 1, 2 | analog OUT 1/L, 2/R | no |
  | 3, 4 | analog OUT 3/L, 4/R | no |
  | 5, 6, 7, 8 | **Grid's USB INPUT 5–8** — pick on any input block | **yes** |

  Dry/Wet swap (I/O Settings → USB) only toggles what 1/2 vs 3/4 carry, and the manual
  warns "Channel names on your computer will update **after reconnecting or power
  cycling**" — so a swap made mid-session does not re-label the CoreAudio device.
- host→QC: **USB In 5/6/7/8** are grid-selectable lane inputs (the reamp workflow).
  Lane `in_port` ids **8..13 = USB_IN_5, 6, 7, 8, 5_6, 7_8** (enum
  `GainCalInputPortParameter.InputPortId` in `qc_descriptors.pb`; matches the port table
  in `.claude/skills/build-preset-routing/SKILL.md:21-35`). `in_port=1` = In 1,
  `in_port=7` = Prev row.
- `IOMeterMessage` (cmd 5, `cortex_protobuf_v2.IOMeterMessage`, 41 fields, all float):
  `input_1/2`, `return_1/2`, `xlr_1` + `xlr_1_limiter`, `xlr_2` + `xlr_2_limiter`,
  `out_3/4` + limiters, `send_1/2`, `hp_l/hp_r`, `hp_limiter_active` (bool),
  `usb_output_1l..4r`, `usb_input_1l..4r`, `grid_xlr_1/2`, `grid_out_3/4`,
  `grid_send_1/2`. Full text: `proto/ProductionAutomation.proto:361-403`.
  **Units: LINEAR AMPLITUDE 0..1** (settled — see docs/METERS.md), converted with
  `20*log10` onto the device's own −40..+12 dB scale.
- `GridModelMeterMessage` (cmd 37) has only `row`, `column` — **no level field**
  (`proto/ProductionAutomation.proto:842-847`). Semantics unknown; P1 open task.
- `IOMeter` is **not** in the handshake subscribe list (`transport.py:96-98`), but
  `tools/gui/mine_log.py:29` treats it as streaming NOISE (seen in captures while Cortex
  Control runs). Bridge mode likely gets it for free; direct mode may need a READ.
- `USBPortSettings` = `level, hp_select, plugged, dry_wet`. **No reamp/global USB-input
  setting exists** — reamp routing is per lane (`in_port`), full stop.
- **Native looper**: `LooperMessage` (cmd 28, `proto/ProductionAutomation.proto:545-554`)
  = `action, request_id, status: LooperStatus, state: uint32, one_shot_play,
  sync_start_waiting, quantize_enabled, update_type: LooperUpdateType.Enum {METER=0,
  BUTTONS=1}`. `LooperStatus` (`:521-543`) = `state, progress, undo_progress,
  duplicate_cycle, num_duplicate_cycles, one_shot_stopped, redo_available,
  loop_length, free_samples, in_reverse, one_shot, half_speed, fixed_duplicate_cycles,
  armed, waiting_for_cycle, undo_count, max/min_write_displacement,
  events_waiting_for_quantize, current_clock, transition, action`. Blocks: `27000
  "Looper X"` (category Loopers), `27002 "2-Track Looper"` (hidden). **`state` values
  (record/play/overdub/stop) are not in the schema** — capture the app's looper
  buttons (P2b).

### Repo APIs to copy (exact locations)
| Need | Where |
|---|---|
| Drain-then-latest broadcast reader (the template for `output_meter`) | `src/qc_mcp/server.py:308-324` (inside `cpu_load`) |
| Decode/queue pump `_collect(seconds)` → appends `(cmd, obj, raw, pb)` to `qc._pending` | `src/qc_mcp/transport.py:150-161` |
| cmd → message class (`P.message_class(cmd)`, `P.NAME_TO_CMD`) | `src/qc_mcp/protocol.py:98-102`, `27-44` |
| Solicited READ `read_message` / `read_state` | `src/qc_mcp/transport.py:470-487`, `814-822` |
| Lane sub-block param write `set_lane_param(row, "output_control", idx, val)` | `src/qc_mcp/transport.py:376-389` |
| `set_lane_output` tool (VOLUME=0, PAN=1, MUTE=2, SOLO=3; **raw 0..1**) | `src/qc_mcp/server.py:637-658` |
| `set_routing(row, in_portid, out_portid)` | `src/qc_mcp/transport.py:341-353` |
| `set_param`, `set_param_scenes`, `_await_scene`, `set_block_bypass` | `src/qc_mcp/transport.py:408-422`, `626-646`, `614-624`, `699-712` |
| `add_block(model_hash, row, column)` / `recall(...)` / `write_preset_file` (File CREATE) | `src/qc_mcp/transport.py:196-223`, `736-757`, `454-468` |
| `get_current_preset` shape (`chains[].in_port/out_port/blocks/split_points`) | `src/qc_mcp/server.py:122-164` (`_preset_summary`) |
| Catalog taper `_prange`, `_is_log`, `to_norm`, `to_display` | `src/qc_mcp/catalog.py:66-101` |
| Tool docstring style | `src/qc_mcp/server.py:298-306` |
| Test skeleton + hand-rolled runner (plain `assert`, no pytest dep) | `tests/test_directory.py:1-8`, `94-105` |
| Telemetry doc template | `docs/CPU.md:10-19` |
| Optional-dependency pattern (`re`, `gui` extras) | `pyproject.toml` |

### Level-trim blocks (from `src/qc_mcp/ModelRepo.xml`, single-line file)
- **`16005 "Gain"`** — idx 0 `LEVEL` −60..+12 dB (`skew="3.8018"`, `min_string="OFF"`),
  idx 1 `PAN` −1..1, idx 2 `PHASE INVERT`. **The per-scene trim block.**
- `16006 "Volume"` — idx 0 `LEVEL` 0..100 %, idx 1 `CURVE` Linear/Log. Not dB; avoid.
- `23000 "LaneOutputControl"` — `VOLUME` min/max are symbolic `MIN_MIXER_DB`/
  `MAX_MIXER_DB` → `_prange` returns None → values pass through raw 0..1. dB range
  undocumented (default 0.769 per routing skill).
- **`catalog.py` ignores ModelRepo's `skew`** — for `Gain.LEVEL` (`min=-60`, so
  `_is_log` is false) display↔normalized is wrong today. Fixed in P3.

### Verified on this machine (2026-09-02, Python 3.14.6 / arm64)
Measured, not assumed — a scratch venv was built and the device was probed:
- **All six packages install as prebuilt wheels on 3.14**, no compiler, no Homebrew:
  numpy 2.5.2, scipy 1.18.1, sounddevice 0.5.6, soundfile 0.14.0, pyloudnorm 0.2.0,
  mosqito 1.2.1 (+ pyuff). PortAudio **V19.7.0-devel ships inside the sounddevice
  wheel** — `brew install portaudio` is not needed.
- **`mosqito` imports `matplotlib` but does not declare it** — `from mosqito.sq_metrics
  import loudness_zwtv` raises `ModuleNotFoundError: matplotlib` on a clean install. The
  extra must list matplotlib explicitly, or Zwicker loudness must be implemented locally.
  Full stack on disk: **230 MB** (scipy 99, numpy 34, matplotlib 33).
- **`pyloudnorm` returns −23.05 LUFS for a 1 kHz sine at −20 dBFS** — the P2 test constant
  is confirmed; pin −23.0 ± 0.2.
- **Zwicker cost: `loudness_zwtv` takes 1.77 s for 2 s of audio** (~0.9× realtime, mono).
  A 7 s riff analyses in ~6 s, so a 6-preset × 3-iteration run adds ~2 min of pure compute
  on top of playback. Make N5/N50 opt-in per measurement, not automatic.
- **The QC enumerates in CoreAudio as `Quad Cortex`, `in=8 out=8`, default 48000 Hz** —
  the manual's fixed-48 k / 8×8 claim holds. Resolve it by name at run time; its index
  moved between probes on a machine with 10 audio devices.
- **A passive 8-channel capture works and is not blocked by macOS** (run from the Claude
  Code shell): USB 1–4 returned a live noise floor (−89.9, −55.3, −85.7, −85.0 dBFS),
  **USB 5–8 returned exact digital silence**. Consistent with 1–4 being the device→host
  feed (dry/wet per the I/O setting) and 5–8 being the host→device reamp direction only.
  Narrows the P2 loopback task: the wet pair is inside USB 1–4.

### Microphone permission (corrected against SingZ's shipped app)
macOS attributes a TCC microphone grant to the **responsible process**, not to the
python that opens the stream. Capture worked in the probe above because the shell
inherits its parent's grant. When **Patchbay spawns the MCP server**, the request is
attributed to Patchbay.

What is certain:
- **`NSMicrophoneUsageDescription` is required** in the packaged Info.plist. Without the
  string the prompt never appears and the read is denied. SingZ sets it via
  electron-builder `mac.extendInfo` (`~/Dev/my/SingZ/electron-builder.yml:94-95`);
  Patchbay sets no `extendInfo` at all today.
- **A denied microphone does not raise — it returns digital silence.** The failure looks
  like "this preset is quiet", not like an error. `measure_loudness` must detect an
  all-zero capture and report a permission problem, never emit −inf LUFS and let the
  loop trim against nothing. This is the single most important guard in P2.

What is NOT established (do not assume either way):
- Whether `com.apple.security.device.audio-input` is needed under **Hardened Runtime**.
  Apple lists it under Hardened Runtime resource access, but **SingZ ships a notarized,
  hardened-runtime Electron app that captures audio and does not declare it** — its
  `build/entitlements.mac.plist` carries only `allow-jit` +
  `allow-unsigned-executable-memory`, exactly like Patchbay's. Caveat: SingZ's native
  capture addon lives in the unreleased `dsp-graph-plan` worktree, so the shipped app may
  be capturing through Chromium `getUserMedia` (where Electron owns the TCC dance) rather
  than through a native addon in-process. Those two paths may not behave the same.
- **Settle it by test, not by reading Apple docs**: add only the usage string, build a
  signed local Patchbay, and try a capture. Add the entitlement only if that fails —
  it costs a re-notarisation, and `entitlements.mac.plist`'s comment block should be
  updated with whichever answer the test gives.

### Library APIs (allowed; cited)
- **sounddevice** (docs `/spatialaudio/python-sounddevice`):
  `sd.query_devices(device=None, kind=None)` (keys `name,index,hostapi,
  max_input_channels,max_output_channels,default_samplerate`; `device` may be a name
  substring, must match exactly one); `sd.rec(frames, samplerate, channels, dtype,
  mapping=[1-based ch...], device=..., blocking=False)` + `sd.wait()`;
  `sd.playrec(data, samplerate, channels, input_mapping, output_mapping,
  device=(in, out))`; `sd.InputStream(samplerate, blocksize, device, channels, dtype,
  extra_settings, callback)` with `callback(indata, frames, time, status)` — **no
  `mapping` on streams**; `sd.CoreAudioSettings(channel_map=[0-based...],
  change_device_parameters=False, fail_if_conversion_required=False)`;
  `sd.check_input_settings(device, channels, dtype, samplerate)`.
- **pyloudnorm** (`csteinmetz1/pyloudnorm` master): `pyln.Meter(rate,
  filter_class="K-weighting", block_size=0.400)`; `meter.integrated_loudness(data)`
  → LUFS, `data` float ndarray `(samples, ch)` or `(samples,)`, ≤5 ch, **≥ 0.4 s**,
  gated (−70 abs, −10 rel), `-inf` if fully gated; `meter.loudness_range(data)`;
  `pyln.normalize.loudness(data, in_lufs, target)`. **No true peak, no short-term.**
- **scipy**: `scipy.signal.resample_poly(x, up, down, axis=0)` — true peak = 4×
  oversample then `20*log10(max|x|)`; 44.1k→48k = `(160, 147)`.
- **mosqito 1.2.1**: `from mosqito.sq_metrics import loudness_zwtv, loudness_zwst`;
  `loudness_zwtv(signal_mono, fs, field_type="free")` → `(N[t] sones, N_specific,
  bark_axis, time_axis)`; needs fs ≥ 48 kHz (48 k OK, else it resamples). N5 =
  `np.percentile(N, 95)`, N50 = `np.percentile(N, 50)`. Input nominally Pascals —
  we use a fixed FS→Pa convention and report N as relative.
- **soundfile**: `sf.read(path, dtype='float32', always_2d=True)` → `(data, sr)`;
  `sf.write(path, data, 48000, subtype='PCM_24')`.

### Anti-patterns (do not do)
- Don't use `qc.request(...)` for broadcasts — it filters on `request_id`, which is 0.
- Don't take the *first* buffered telemetry message; drain then take the **latest**.
- Don't design around `GridModelMeter` carrying a level — it doesn't.
- Don't pass `mapping=` to `InputStream`; don't mix 1-based `mapping` with 0-based
  `channel_map`.
- Don't call `integrated_loudness` on < 0.4 s or on int arrays; don't treat it as a
  short-term meter (it gates).
- Don't level with amp MASTER/OUTPUT or cab params (tone changes); only lane VOLUME
  or a trailing `Gain` block.
- Don't save via RecallPreset SAVE (hangs the device); `save_preset` = File CREATE.
- Don't write per-scene values without `_await_scene` confirmation.
- Don't edit the stale vendored copy under `app/dist/.../Resources/qc-mcp/`.
- Don't commit reference DI recordings or capture logs (personal audio; gitignore).

---

## Phase 1 — `output_meter` from IOMeter telemetry

**Implement** (`src/qc_mcp/server.py`, new helper in `src/qc_mcp/transport.py`):
1. `transport.py`: `def latest_broadcast(self, name, hold_s=0.5)` — generalise
   `server.py:308-324`: `want = P.NAME_TO_CMD[name]`, drop queued `want` from
   `self._pending`, loop `_collect(0.25)` until `hold_s` elapsed, collect **all** fresh
   objects in the window, return the list (empty if none). Keep `cpu_load` unchanged
   (or refactor it onto the helper only if tests still pass).
2. `server.py`: `@mcp.tool() def output_meter(hold_s: float = 1.0) -> dict` — calls
   `latest_broadcast("IOMeter", hold_s)`; returns `{samples: n, ports: {name: {peak:
   max over window, last: last value}}, limiters: {xlr_1, xlr_2, out_3, out_4: max
   flag}, hp_limiter_active: any, note}`. Field names copied verbatim from
   `proto/ProductionAutomation.proto:361-403`. If no samples: return the same
   "no ... update captured" note style as `cpu_load` plus hint "in direct mode send an
   IOMeter READ first".
3. If bridge mode yields nothing: try one `read_message("IOMeter")` (`transport.py:
   470-487`) before the window, and record the outcome in `docs/METERS.md`.
4. **Calibrate units** (device needed): with the app's I/O meter visible, feed a
   known signal (P2's player, or a guitar), read `xlr_1` while screenshotting the app
   meter (`tools/gui/gui.py shot`, see `drive-gui-correlate-protocol` skill). Decide
   linear-vs-dB; add `peak_dbfs` derived field if the float is linear. Document.
5. **Open task — GridModelMeter**: capture the app with a block selected / block meter
   showing and see what cmd 37 is sent and what comes back (`drive-gui-correlate-
   protocol`). Write the finding to `docs/METERS.md` even if negative.
6. `docs/METERS.md` — copy the shape of `docs/CPU.md:10-19`.

**Verification**
- `tests/test_meters.py` (copy `tests/test_directory.py` skeleton): build a fake
  `qc` with `_pending` pre-filled with two `IOMeterMessage` objects (construct via
  `P.message_class(5)()`), assert `output_meter` picks the max peak and the limiter
  flags; assert the empty case returns the note.
- Live: `output_meter(hold_s=2)` while playing → non-zero `xlr_1/2`, limiter 0.0;
  crank a lane VOLUME → limiter flag flips. `cpu_load` still works after the refactor.
- `grep -n "IOMeter" docs/METERS.md src/qc_mcp/server.py` both hit.

**Guards**: no `request()` for cmd 5; no invented fields (only names in the proto).

---

## Phase 2 — Audio capture + loudness core (`measure_loudness`)

**Implement**
1. `pyproject.toml`: add extra `audio = ["numpy>=1.26", "scipy>=1.11",
   "sounddevice>=0.5", "soundfile>=0.12", "pyloudnorm>=0.1.1", "mosqito>=1.2"]`
   next to `re`/`gui`. Server must import lazily: every audio tool does `try: import
   ... except ImportError: return {"error": "install qc-mcp[audio]"}`.
2. `src/qc_mcp/loudness.py` — pure numpy, **no device, no sounddevice import**:
   - `analyze(data: np.ndarray, rate: int) -> dict` → `{lufs_integrated,
     lufs_short_term_max, lufs_short_term_p95, true_peak_dbtp, rms_dbfs,
     zwicker_n5, zwicker_n50, duration_s, gated_silence_s}`.
     LUFS via `pyln.Meter(rate).integrated_loudness(data)`; short-term via 3 s
     windows / 0.5 s hop each fed to `Meter(rate, block_size=3.0)` on that slice
     (documented as an approximation of EBU short-term); true peak via
     `resample_poly(data, 4, 1, axis=0)`; Zwicker via `loudness_zwtv(mono, rate)` on
     `data.mean(axis=1)` scaled with a fixed constant `FS_TO_PA = 20.0` (documented
     as arbitrary/relative), then percentiles. Trim leading/trailing silence below
     −70 dBFS before analysis; return `-inf`-safe floats (JSON: use `None`).
   - `db_delta(measured, target) -> float` and `clamp`.
3. `src/qc_mcp/audio_io.py` — sounddevice wrapper:
   - `find_qc_device(name_hint="Quad Cortex") -> dict` (uses `sd.query_devices()`,
     substring match, asserts `max_input_channels >= 8`, rate 48000).
   - `record(seconds, channels=(5, 6), device=None) -> np.ndarray` using `sd.rec(...,
     mapping=list(channels), samplerate=48000, dtype='float32')` + `sd.wait()`.
   - `play_and_record(stimulus, out_channels=(5, 6), in_channels=(5, 6))` using
     `sd.playrec(..., output_mapping, input_mapping, device=(idx, idx))`, padding the
     recording by 1.0 s for latency + tails.
   - **Measure on host 5/6, not 3/4.** Host 3/4 is sourced from *analog OUTPUT 1/L и 2/R*,
     so it rides the analog output path and would fold master volume / output trim into
     the measurement — the loop would then "level" the master, not the preset. Host 5–8
     are fed by dedicated Grid USB output blocks and are independent of it. So the
     measurement lane sets `out_portid=14` (USB 5/6) for the duration of a run, exactly
     like the reamp input is swapped in, and both are restored in the same `finally`.
   - **Never play the stimulus on host outputs 1–4.** Those go straight to the analog
     jacks, bypassing the grid — full-level audio into the user's monitors. Only 5–8
     reach The Grid. `play_and_record` must hard-refuse any `out_channels` below 5.
   - `load_wav_48k(path)` via `soundfile.read(..., always_2d=True)` +
     `resample_poly` if needed; mono→stereo duplicate.
4. `server.py` tools:
   - `audio_devices() -> list` (query_devices, marks the QC).
   - `measure_loudness(seconds: float = 6.0, channels: list = [3, 4],
     stimulus_path: str = "", device: str = "") -> dict` — record (or play+record
     when `stimulus_path` given) then `loudness.analyze`. Include `channels` and
     `device` in the result.
   - **Looper-style sample recorder** (replaces a plain timed recording). Source =
     USB 1/2, the dry DI (default DRY/WET; `get_io_settings().usb.dry_wet` is checked
     and the tool refuses with a hint if the pair is set to wet). Implemented as a
     small state machine in `src/qc_mcp/sampler.py` on top of `sd.InputStream`
     (callback appends `indata[:, :]` chunks to a list; no work in the callback):
     `idle → armed → recording → done`.
     - `sample_arm(threshold_dbfs=-40, max_seconds=30, silence_seconds=1.5,
       path="") -> dict` — opens the stream, waits for the first block above the
       threshold (**auto-start on the first note**, like a looper's auto-record),
       records until `silence_seconds` of continuous signal below the threshold, or
       `max_seconds`, or `sample_stop()`. Returns immediately with `{state: "armed"}`;
       the stream runs in a background thread.
     - `sample_status() -> dict` — `{state, seconds_recorded, peak_dbfs,
       input_level_dbfs}` (live meter for the user; poll while they play).
     - `sample_stop() -> dict` — ends recording (explicit "second press"); auto-trims
       leading/trailing silence at −60 dBFS with 20 ms fades; saves 24-bit WAV to
       `path` or `~/.qc-mcp/reference_di.wav`; returns `{duration_s, peak_dbfs,
       lufs, path}`. `sample_discard()` throws it away and re-arms ("undo").
     - `sample_play(loops=1, through_preset=True) -> dict` — loop-preview: plays the
       sample out USB 5/6 into the current preset via the P4 reamp wrapper (so the
       user hears their riff through the rig and can judge it), or `through_preset=
       False` just returns the file info. This is also the stimulus playback P4 uses.
     - `sample_info()` — current reference sample metadata; error with hint
       "`sample_arm` then play a riff" if none exists.
   - The user records **once per guitar/pickup setting**; the file is personal and
     never committed.
5. `.gitignore`: `*.wav` under repo, `~/.qc-mcp/` is outside the repo anyway.

**Verification**
- `tests/test_loudness.py` (offline, synthetic): 1 kHz sine at −20 dBFS, 5 s, 48 k →
  `lufs_integrated` within ±0.2 of **−23.0** (K-weighting gain at 1 kHz ≈ −3 dB —
  confirm numerically and pin the constant in the test); `true_peak_dbtp` ≥ sample
  peak; silence → `None`/gated; a 0.3 s clip raises/returns error, not a crash;
  Zwicker N5 ≥ N50 > 0. Skip the mosqito assertions if not installed (tests must run
  in the base venv).
- `tests/test_sampler.py` (offline): drive the state machine with synthetic blocks
  (silence → tone → silence): asserts auto-start on the first above-threshold block,
  auto-stop after `silence_seconds`, `max_seconds` cap, trim removes the leading
  silence (start within 20 ms of the tone onset), `sample_stop` while armed with no
  signal returns an error not an empty file, `sample_discard` re-arms.
- Live: `audio_devices()` lists the QC with 8/8 at 48000; `measure_loudness(4)` while
  playing returns plausible LUFS (−30..−10); `sample_arm()` → play a riff → status
  shows `recording` with a moving `seconds_recorded` → stop playing → `sample_status`
  turns `done` by itself; `sample_play()` is audible through the current preset.
- Loopback check for the open question "which USB pair carries the wet output":
  route a lane `out_port=14` (USB 5/6) and `19` (Multi Out), measure channels
  (3,4) vs (5,6) vs (7,8); write the answer into `docs/LEVELING.md`.

**Guards**: `mapping` is 1-based; open streams at 48000 with
`fail_if_conversion_required=True` in `CoreAudioSettings` so a rate mismatch fails
loudly; never `change_device_parameters=True` (would disturb Cortex Control/DAW);
the `InputStream` callback only appends to a list (no numpy math, no I/O in it); one
sampler instance per server (a second `sample_arm` while armed returns the status).

---

## Phase 2b — Device looper as the stimulus (alternate source; after P2)

Goal: the user records the riff on the QC itself (footswitch, or our tool), and the
loop plays through the whole preset — no Mac→QC audio, no reamp routing swap.

**Discover first** (device + app, `drive-gui-correlate-protocol` skill):
1. With a preset containing `Looper X` (27000) at column 0 of the input lane, press
   the app's looper Record / Play / Stop / Overdub / Undo buttons and log the
   `LooperMessage` each emits: which `state` integer, `update_type=BUTTONS`, and
   the `LooperStatus` stream (`update_type=METER`, `request_id=0` broadcast —
   drain-then-latest like P1). Write the `state` table to `docs/METERS.md`.
   **Fallback if the `state` ints stay opaque**: the manual documents Looper X over
   **MIDI CC**, which the QC accepts on USB MIDI — CC#48 open/close the Looper X
   (0-63 opens in perform mode, 64-127 closes), **CC#53 Record/Overdub/Stop**
   (64-127 acts, 0-63 stops), **CC#54 Play/Stop**, CC#56 Undo/Redo, CC#49 Duplicate,
   CC#50 One Shot, CC#51 Half Speed, CC#55 Reverse, CC#58 Quantize (0=off, 1-8 beats,
   9=16 beats), CC#60 perform/params. That is a documented, stable control path that
   does not require reversing the protobuf enum at all.
2. **Persistence**: record a loop, recall another preset (with and without a
   looper block), recall back — does `LooperStatus.loop_length` survive? Is the
   audio there? This decides whether P2b can serve `level_setlist` or only
   `level_preset`/`level_scenes`.
3. Where the block sits: leveling needs the loop **before** the amp (DI position);
   confirm the block's pre/post routing param in `ModelRepo.xml` (27000 params) and
   whether the loop records the lane input regardless of block position.

**Implement** (only if step 1 succeeds):
- `transport.py`: `looper(state, one_shot=False)` → `LooperMessage{action=UPDATE,
  state, update_type=BUTTONS}` via `request`; `looper_status()` → latest
  `LooperStatus` broadcast.
- `server.py`: `looper(action: "record"|"play"|"stop"|"overdub"|"undo"|"clear")`,
  `looper_status()`.
- `leveling.py`: stimulus source `"device_looper"`: ensure `Looper X` exists at the
  head of the input lane (add temporarily if absent, remember to remove), require
  `loop_length > 0`, `looper("play")`, measure the wet USB pair for one loop length
  (`loop_length` samples / 48000 + tails), `looper("stop")`. If step 2 shows loops
  don't survive recall, `level_setlist` refuses this source with a hint to use the
  Mac-side sample.

**Verification**: `looper("record")` → status `state` changes and `progress` grows;
`looper("play")` → `measure_loudness` sees signal; removing the temporary block leaves
`get_current_preset` identical to before (diff the summary).

**Guards**: don't guess `state` integers — only the captured ones; don't leave a
temporary `Looper X` block behind (finally-block removal, same discipline as the
reamp routing restore); Looper X CPU cost (`cpu_load`) is checked before adding on
a heavy preset.

---

## Phase 3 — Level-trim primitives (dB-accurate knobs)

**Implement**
1. `src/qc_mcp/catalog.py`: honour ModelRepo `skew`. Parse the attribute in `_parse`
   (`catalog.py:19-37`) and in `to_norm`/`to_display` (`81-101`) apply, when
   `skew` is present and ≠ 1, the JUCE `NormalisableRange` convention:
   `display = min + (max-min) * norm**(1/skew)`, `norm = ((display-min)/(max-min))
   ** skew`. Hypothesis check: `Gain.LEVEL` norm 0.5 → **0.0 dB** with skew 3.8018.
   **Verify on device** per `calibrate-param-mapping` skill: set LEVEL to −12, 0, +6
   dB via the app, read back the normalized value with `get_current_preset` raw
   read; fit. Existing power taper (`LOG_TAPER`) stays for params without `skew`.
2. `transport.py`: `def add_gain_trim(self, row, column=None)` — adds `16005` at the
   last free column of the output lane (after `mix_col`, per routing skill: post-merge
   FX go after `mix_col`); returns the column. Re-uses `add_block` (dedupe rule:
   re-adding the same hash on a row is a no-op — check `get_current_preset` first).
3. `server.py`: `set_level_trim(row, column, db, scenes: list = None)` → `set_param`
   with `device_hash=16005` (display dB → norm via the fixed taper), or
   `set_param_scenes` for a per-scene list (8 values). And `set_lane_output` gains an
   optional `volume_db` path **only after** step 4 establishes the mapping.
4. **Self-calibrate lane VOLUME → dB** (device + P2): with the reference DI playing,
   measure LUFS at VOLUME 0.769, 0.6, 0.4, 0.2; fit dB vs value; store the fit in
   `docs/LEVELING.md` and as a constant table in `loudness.py` (`LANE_VOLUME_DB`).
   If the curve is not monotonic/clean, leveling uses the Gain block only.

**Verification**
- `tests/test_catalog_skew.py`: round-trip `to_display(16005, 0, to_norm(16005, 0,
  x)) == x` for x in {−60, −12, 0, +6, +12}; norm(0 dB) ≈ 0.5.
- Live: `set_level_trim(row, col, -6.0)` → app shows −6.0 dB; `measure_loudness`
  before/after differs by 6 ± 0.5 LU (this doubles as the end-to-end check of P2).

**Guards**: never touch amp block params in this phase; per-scene writes go through
`set_param_scenes` (which awaits scene confirmation) — no custom scene loop.

---

## Phase 4 — Closed loop: `suggest_levels`, `level_preset`, `level_scenes`, `level_setlist`

**Implement** (`src/qc_mcp/leveling.py` orchestrating transport + audio; tools in
`server.py`):
1. **Stimulus + routing wrapper** `with_reamp_input(qc, row=0, usb_pair=(5, 6))`:
   read `get_current_preset` → remember `chains[row].in_port`; `set_routing(row,
   in_portid=12)` (USB_IN_5_6; use 8 for mono USB 5); on exit restore the original
   `in_port` **always** (try/finally), and verify by re-reading. Stimulus =
   the P2 sample (`~/.qc-mcp/reference_di.wav`) unless `di_path` given; error if
   missing, pointing at `sample_arm`. `stimulus="device_looper"` selects P2b when
   available (then no routing swap and no Mac playback happen).
2. `measure_preset(target_metric="lufs")` = wrapper + `play_and_record` (out USB 5/6,
   in wet pair from the P2 loopback answer) + `analyze`. Returns the full dict plus
   `metric_value`.
3. **Correction step**: `delta = target - measured` (LU). Knob priority:
   trailing `Gain` trim (add if absent, P3) → `LEVEL += delta` clamped −60..+12;
   for whole-preset mode when the user opts `knob="lane_volume"`, use the P3 fit.
   Iterate: measure → correct → measure, stop when `|delta| <= tolerance` (default
   0.5 LU) or after `max_iter=4`. **Guards** before accepting: `true_peak_dbtp <=
   -1.0` and `output_meter` limiter flags all 0 during the take; otherwise back off
   the trim to meet the guard and report `limited_by="true_peak"`.
4. `suggest_levels(positions: list = None, target_lufs=-18.0, metric="lufs",
   di_path="")` — **report-only** (the PPI-style output): for each preset (recall via
   `recall_preset`, which verifies the position moved), measure once, return a table
   `[{position, name, lufs, n5, n50, true_peak, correction_db}]`. Reference can be
   `target_lufs` or `reference_position` (level everything to one preset).
5. `level_preset(target=-18.0, metric="lufs", knob="gain", tolerance=0.5, save=False)`
   — current preset, current scene. `save=True` → `save_preset()` (File CREATE),
   then confirm via header/`current_preset_position` as CLAUDE.md requires.
6. `level_scenes(target, scenes=None, ...)` — for each scene: `set_scene` +
   `_await_scene` (copy `transport.py:626-646`), measure, write the per-scene
   `Gain.LEVEL` via `set_param_scenes` (single value per scene; the block's LEVEL is
   first assigned to scenes). Restore scene 0.
7. `level_setlist(setlist_key="", positions=None, target, ..., save=False)` — loop
   `recall_preset` → `level_preset` (or `level_scenes` when `per_scene=True`) → save
   if asked; returns the before/after table. Abort the loop on the first device error
   and report partial results (never leave a lane routed to USB — the wrapper's
   finally handles it).

**Verification**
- `tests/test_leveling.py` (offline): fake `qc` + fake `measure` function returning
  a scripted sequence (−24, −18.3, −18.0); assert the loop converges in ≤3
  iterations, clamps at +12 dB, restores `in_port` even when `measure` raises, and
  respects the true-peak guard (scripted `true_peak_dbtp=-0.2` → trim backed off).
- Live (bridge mode, single preset): `level_preset(-18)` → final `|delta| ≤ 0.5`,
  `get_current_preset` shows the original `in_port` restored and one `Gain` block on
  the output lane; app header shows `*` (dirty) until `save=True`.
- Live (two presets, clean vs high-gain): `suggest_levels` shows the LUFS gap and
  the N5 gap; after `level_setlist(metric="lufs")` LUFS within 0.5 LU, and note the
  residual N5 difference in `docs/LEVELING.md` (this is the PPI argument, measured).

**Guards**: no scene write without `_await_scene`; no RecallPreset SAVE; no amp
params; routing restore in `finally`; recalls need `folder_key` (use `recall_preset`
default = current folder).

---

## Phase 5 — Timbre / spectral balance vs reference (optional, after P4)

**Implement** (`loudness.py` + `server.py`): `spectral_balance(data, rate)` → energy
in 8 bands (e.g. 60-120, 120-250, 250-500, 500-1k, 1-2k, 2-4k, 4-8k, 8-16k Hz) in dB
relative to total, from the K-weighted signal (reuse pyloudnorm's `Meter` filters via
`meter._filters` only if public; otherwise a `scipy.signal.butter` bandpass bank).
`compare_presets(reference_position, positions)` → per preset: LUFS delta, N5/N50
delta, 8-band diff, `crest_db = true_peak - rms`, plus a plain-language verdict
("level" vs "EQ" difference: if bands differ > 3 dB in ≥2 bands it's tone, not gain).
Feed into `review-preset` skill section 3.

**Verification**: synthetic test — pink noise vs pink noise + 6 dB shelf at 2 kHz
flags the 2-4k/4-8k bands; identical inputs → all deltas ≈ 0.

---

## Phase 6 — Docs, skills, final verification

1. `docs/LEVELING.md` — workflow (arm the sampler → play a riff once → suggest →
   level → save), the USB pair answer, lane VOLUME fit, IOMeter units, looper `state`
   table + persistence answer, limits (approximate short-term, relative sones). Link
   from `README.md` tool table (add `output_meter`, `measure_loudness`, `sample_arm`/
   `sample_status`/`sample_stop`/`sample_play`, `looper`, `suggest_levels`,
   `level_preset`, `level_scenes`, `level_setlist`, `audio_devices`).
2. Skills: `.claude/skills/build-preset-routing/SKILL.md:133-134` (replace "verify
   against the output meter" with `output_meter` + `measure_loudness`), `:190-192`
   (add the tools to Read/verify), `:155-156` (IOMeter now decoded);
   `.claude/skills/review-preset/SKILL.md:36-39` (gain-staging step uses
   `output_meter` limiter flags + `measure_loudness`, mention N5 vs LUFS); `:15`.
3. `CLAUDE.md` gotchas: audio extra is optional; `mapping` 1-based; IOMeter units;
   reamp routing must be restored; leveling never touches amp params; DI file is
   personal and gitignored. Add a one-line pointer to this plan in `PLAN.md`.
4. **Final verification**: `.venv/bin/python tests/test_directory.py` and the new
   test files all exit 0 in the base venv (audio tests skip cleanly without the
   extra); `grep -rn "request(\"IOMeter\"\|RecallPreset.*SAVE\|mapping=" src/qc_mcp`
   shows no misuse; `grep -n "skew" src/qc_mcp/catalog.py` hits; one full live run of
   `level_setlist` over 3 presets in bridge mode with `save=True`, then reboot-free
   device state (positions verified, `in_port` restored on every preset).

## Status
- **P0 — discovery: DONE** (2026-09-02).
- **P1 — `output_meter`: DONE.** `transport.latest_broadcast()` factored out of
  `cpu_load`; `output_meter(hold_s, detail)` with peak-hold + limiter flags; one shared
  HP limiter flag; `docs/METERS.md`. 7 offline tests.
- **P2 — audio capture + loudness core: DONE.** `loudness.py` (LUFS, short-term, true
  peak, RMS, Zwicker), `audio_io.py` (device lookup by name, record, play_and_record,
  WAV I/O, the looper-style `Sampler`), `[audio]` extra, 6 MCP tools. 15 + 10 tests.
  Verified live: device resolves, passive capture works, channel refusal fires.
- **P3 — dB-accurate trims: DONE.** `catalog.py` honours the declared JUCE `skew`
  (773 params); Gain LEVEL round-trips exactly and 0 dB sits at norm 0.5. 9 tests.
- **P4 — closed loop: DONE (untested on hardware).** `leveling.py` — `reamp_routing`
  context manager, `level_current`, `level_scenes`; tools `suggest_levels`,
  `level_preset`, `level_scenes`, `level_setlist`. 15 tests covering convergence,
  clamping, the true-peak guard, silent-capture refusal and routing restoration.
- **P2b — device looper: not started.** The MIDI CC path (docs/METERS.md) removes the
  need to reverse the protobuf state ints first.
- **P5 — spectral comparison: DONE.** `spectral_balance` (9 bands, level-independent),
  `crest_db`, `compare_spectra`, `verdict` (level vs tone vs both), and the
  `compare_presets` tool. 7 tests, including that a pure gain change reads as "level"
  and an EQ change reads as "tone".
- **P6 — docs/skills: DONE.** `docs/LEVELING.md`, `docs/METERS.md`, README table,
  CLAUDE.md gotchas, and both skills (`build-preset-routing` gain-staging + read/verify
  list, `review-preset` ground-truth + gain-staging) now point at `output_meter`,
  `measure_loudness` and `suggest_levels`.

## Not yet verified on hardware
Everything below needs the device plus a recorded riff, and playback into the rig:
- the reamp loop end to end (`measure_preset`, `level_preset`) — nothing has yet played
  audio INTO the QC from here;
- that `out_portid=14` really lands on host inputs 5/6;
- (IOMeter units: settled — linear amplitude);
- `level_scenes` against real scenes;
- whether Patchbay needs the audio-input entitlement or just the usage string.

## Open questions (tracked as tasks above)
- ~~IOMeter float units~~ — **settled: linear amplitude 0..1**, converted with
  `20*log10` onto the device's own −40..+12 dB scale (see docs/METERS.md). Whether
  direct mode needs a READ/subscribe is still open.
- What `GridModelMeter` (cmd 37) actually returns (P1.5).
- ~~Which USB pair carries the wet signal~~ — **settled from the manual**, see the
  channel map in Phase 0. Measure on host 5/6 off a dedicated Grid USB output block.
- LaneOutputControl VOLUME → dB mapping (P3.4); whether lane VOLUME can be scened at
  all (it is a lane sub-block, not a grid block — assume no, use `Gain`).
- Looper `state` integers, whether the app's looper buttons emit cmd 28 at all, and
  whether a recorded loop survives preset recall (P2b.1–2).
- Hands-free start/stop from the device (a footswitch) for the Mac-side sampler: no
  footswitch event is known in the protocol; the signal-threshold auto-start/auto-stop
  is the substitute. Revisit if a Stomp/Scene message turns out to be usable as a
  trigger.
