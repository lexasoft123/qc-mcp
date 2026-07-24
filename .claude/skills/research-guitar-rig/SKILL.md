---
name: research-guitar-rig
description: Research a guitarist's/band's rig from the web and translate it faithfully to Quad Cortex amp/cab/pedal models with correct settings — including the artist's tone palette as Scenes (clean/crunch/lead/heavy/ambient). Use when a request names an artist, song, or "sound like X" and you need to find the real amps, cabs, pedals, signal chain, knob settings, and per-scene tones, map each to the closest QC catalog model, and dial the parameters. Pairs with build-preset-routing (topology) and optimize-preset-cpu.
---

# Research a guitar rig and translate it to the Quad Cortex

Goal: turn "build me a John Mayer rig" into the **right models, topology, and settings** —
grounded in real sources, not guesses. Do the research FIRST; the catalog resolution and
param dialing follow from it.

## 1. Where to look (ranked)
Search the web; cross-check at least two sources (rigs vary by era/tour — pin the era).
- **Premier Guitar "Rig Rundown"** — the gold standard, an on-camera walkthrough of the
  touring rig. (Their article page may 403 to bots; fall back to the YouTube video's
  description/transcript, or secondary write-ups quoting it.)
- **Dedicated gear guides** — guitargearfinder.com, ground-guitar.com, equipboard.com,
  guitarworld.com / guitar.com features ("X's gear era by era"). Good for the full list.
- **Forums for exact settings** — The Gear Page (TGP), **Neural DSP Unity**
  (unity.neuraldsp.com), **Fractal Audio forum** (forum.fractalaudio.com — search "<artist>
  tone", often lists amp knob values), Kemper Rig Exchange, r/guitar, r/Fractal.
- **The manufacturer's own signature product** — e.g. **Neural DSP Archetype: <artist>**.
  Since it's the same company as the QC, its stock presets are the best statement of the
  intended amp/pedal voicing and settings; mirror them.
- **Video** — YouTube "rig rundown" and "how to sound like X" clips frequently show exact
  knob positions; pause on the amp/pedal shots.

Use `WebSearch` then `WebFetch` the best 1-2 pages with a focused prompt ("list the amps,
how they're run, cabs, and each pedal's chain position and settings"). If a page 403s, try
another source rather than forcing it.

### 1a. Mine the signature (Archetype) plugin — the shortcut
If the artist has a **Neural DSP Archetype** (or any signature amp-sim/plugin), it often
yields the *whole rig from one source* — Neural models the artist's actual amps/pedals and
states how they run. Five places, in order:
1. **Product page** `neuraldsp.com/plugins/archetype-<artist>` — the amps (with voicing
   notes), cabs/mics, every pedal/effect module, the signal chain, and **how the amps run**
   (e.g. Archetype: John Mayer X says outright: *three amps in parallel, "blended exactly as
   John uses them"*).
2. **Manual** `neuraldsp.com/manual/archetype-<artist>` — every module and **every knob**,
   i.e. the exact parameter values to map.
3. **Getting Started / "Tips for using your plugin"** — the common Archetype layout: a stomp
   section of 3-4 series pedals, amps arranged **low→high gain left-to-right**, cab sim with
   movable mics.
4. **Factory + artist presets** — ships with the artist's own presets plus hundreds of
   curated ones; their names + GUI settings are ready-made **tones/scenes** (§6). Seen in
   demo videos or in the plugin itself.
5. **Demo/walkthrough videos & reviews** — show the GUI, presets, and knob positions.

**Decode the branded module names.** Neural gives modules artist-flavored names, so
translate **branded name → real pedal/amp → QC model** (Archetype: JM example):

| Archetype module | real gear | QC model |
|---|---|---|
| Smooth Operator / Headroom Hero / Signature 83 (×3, parallel) | 15" American / high-headroom clean / boutique Dumble-family | US Tweed Basslad / US TWN / **Dumbbell ODS** |
| Justa Boost | Klon-style clean boost | Myth Drive |
| Tealbreaker (TS + BB modes) | Tube Screamer + Bluesbreaker | Green 808 (+ a Bluesbreaker OD) |
| Millipede Delay | analog BBD delay | an analog/BBD delay |
| Gravity Tank | '60s spring reverb + harmonic tremolo | Spring Reverb (+ tremolo) |

One plugin thus gives the amps (and that they're **parallel**), the front pedals, the time
FX, and the settings — then map each module to the QC catalog (§3) and cross-check against
§1 sources for the physical touring rig.

## 2. What to extract — the rig anatomy
- **Amps:** which models, how many, and **how they run** — single, stacked, or **multiple
  amps blended in parallel** (very common for pro rigs; see build-preset-routing).
- **Cabs / speakers:** cab model + speaker type (drives the cab-model choice).
- **Pedalboard:** each pedal, its **chain position**, whether it's **always-on vs
  footswitched**, and **in front of the amp vs in an FX loop**. Note the "core" always-on
  pedals (e.g. a Klon, a comp) — those define the base tone.
- **Signal chain / routing:** series vs parallel; wet/dry; where time-based FX sit.
- **Tone character:** clean / edge-of-breakup / high-gain; bright/dark; compressed; how
  loud the amps run (headroom).

## 3. Map real gear → QC catalog models
- `find_devices(query=..., category=...)` searches by name **and** by the emulated gear —
  every catalog model's `based_on` cites the real brand (e.g. "Based on Klon® Centaur®").
  Match on that. Categories: `Guitar Amplifier`, `Cabsim Guitar (M)`/`(ST)`, `Guitar
  Overdrive`, `Compressor`, `Delay`, `Reverb`, `Modulation`, etc.
- **Handle gaps** — the QC won't have every amp. Substitute within the same *family*:
  - Two-Rock / Dumble Steel String Singer → **Dumbbell ODS** (Dumble-derived).
  - Fender Bassman/Bandmaster (tweed) → **US Tweed Basslad**; Twin/Deluxe → **US TWN / US DLX**.
  - Klon → **Myth Drive**; TS-808 → **Green 808**; Boss DC-2 → **Chief DC2W**.
  State the substitution and why (don't silently swap). If a signature capture exists on
  the device or Cortex Cloud, prefer it (search the DIRECTORY).
- Mono (M) cabs by default; stereo (ST) only when you need width (2× CPU).

## 4. Understand and apply the settings
- **Amp knobs** — map the artist's described settings to the QC amp's params (in DISPLAY
  units via `set_parameter`). Names/ranges differ per model: gain/drive, bass/mid/treble,
  presence, master/output; some amps expose channel, bright, cut, sag. Read the model's
  params first (`get_current_preset` or `find_devices` returns the param list) so you set
  the right index.
- **Pedal settings** — gain/drive, tone, level (a Klon is usually low-gain/high-output as
  an always-on boost; a comp set for sustain, not squash).
- **Gain staging** — respect how hard the amps run (headroom vs breakup) and don't let a
  parallel sum clip (build-preset-routing §gain-staging).
- Start from the **model defaults**, then move toward the researched settings; forum knob
  values are a *starting point* — trust the target tone.

## 5. Apply, then verify
- Build the topology with `build_preset` / the build-preset-routing skill.
- **Read back** `get_current_preset` and confirm the models resolved and params landed in
  the expected display values; screenshot the grid for the wiring.
- Sanity-check against the source: right number of amps, right chain order, always-on
  pedals present, tone character plausible.

## 6. Scenes — one preset, the artist's tone palette
Most artists don't play one tone — they switch between a handful (clean rhythm, a crunch,
a lead boost, maybe a heavy or ambient sound). On the QC these are **Scenes A–H**: snapshots
*within one preset* that store per-block **parameter values** and **bypass** states, so you
footswitch tones without reloading. Research which tones the artist actually uses (per
song/section) and build one per scene.

**Research angle** — from the same sources (§1), note the *distinct sounds*, not just gear:
rhythm vs lead, where they kick in a boost/OD, when a big ambient wash appears, whether
there's a high-gain part. That list becomes the scene map.

**Common scene archetypes** (what changes between them):
- **Clean** — amp at/below breakup, drives OFF, comp on, modest reverb.
- **Crunch** — push the amp (gain/volume up) or engage a low-gain OD; a touch more level.
- **Lead** — boost/OD ON + a **volume bump** (via amp channel/level or a lane/output volume)
  + often more delay/reverb; this is the classic "solo" scene.
- **Heavy** — high-gain amp/channel, noise gate on and tighter, scooped or focused EQ.
- **Ambient** — large reverb + delay (feedback/mix up), modulation/swells, guitar volume
  role; the amp itself often stays clean.

**How scenes work on the QC** (mechanism — see CLAUDE.md and build-preset-routing):
- A parameter must be **assigned to scenes** first (in the app: right-click → *Assign to
  Scenes*; on the wire: `params{index, scene_mode:true}` with no value), then each scene
  gets its own value. The MCP `set_parameter_scenes` does the assign + per-scene write.
- **Per-scene bypass** (turn a drive/block on for just the lead scene) is the same mechanism
  on the block's **bypass param (index 4)**; `set_block_bypass(..., scenes=[...])`.
- **Per-scene volume** for a lead bump: assign the amp's level or the lane output VOLUME to
  scenes and raise it on the lead scene.
- Name/color scenes for the tones; `switch_scene(0–7)` = A–H. Verify by switching scenes and
  reading back the per-scene values.

Keep the *signal chain identical* across scenes — scenes change **settings/bypass**, not the
grid. If two tones need different blocks (e.g. a whole extra amp), that's fine: include the
block in the preset and bypass it per-scene.

## Caveats
- **Pin the era** — an artist's rig changes across tours; "the" rig doesn't exist.
- **"Based on" ≠ identical** — emulations voice differently; adjust by ear/target.
- **Cite sources** back to the user (which rig/era you modeled) so they can correct you —
  and confirm ambiguous substitutions rather than guessing (e.g. "QC has no Two-Rock amp —
  use the Dumble model, or a clean Fender?").
