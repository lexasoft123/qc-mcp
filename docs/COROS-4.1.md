# CorOS 4.1 — what's new, and how to use it

Everything the 4.1 update added, from the MCP's point of view. Wire formats are in
[PROTOCOL.md §12](../PROTOCOL.md); this page is about *using* the features. All of
it was verified against a live unit on CorOS 4.1.0.

The MCP supports **4.0 and 4.1 on one build** — it reads the device's firmware on
connect and picks the matching wire schema, so the 4.1-only tools below return a
clear "needs CorOS 4.1" instead of failing silently on an older unit.
`device_info` reports what the connected unit actually has:

```json
{"firmware": "4.1.0", "protocol_generation": "4.1", "device_name": "QC MAX",
 "features": {"model_presets": true, "dual_footswitch": true,
              "favorites_by_type": true, "midi_clock_readout": true}}
```

---

## 1. Device presets — a whole knob set on one block

*"Save your favourite amp, cab, overdrive, effect or utility settings and recall
them in any rig."* 4.1.0 ships **2751 factory presets across 602 models**, plus up
to **32 user presets per device**.

```
list_device_presets("Myth Drive")              → 17 presets: Crunch, Boost, Barely Drive, …
load_device_preset(row=0, column=0, preset="Crunch")
save_device_preset(row=0, column=0, name="My Klon")
delete_device_preset("My Klon", model="Myth Drive")
```

Use `load_device_preset` as the **first pass on a block** — it replaces every
parameter at once with something a designer dialled in — then fine-tune. It's also
the fastest way to hear what a model is meant to sound like.

Two behaviours worth knowing:

- **The device refuses a save whose parameters match an existing preset** for that
  model. The app shows "Preset Conflict"; `save_device_preset` returns that as an
  error. Change something first, or just load the preset that already holds those
  values.
- Loading is verified for you: the tool reads the block back and returns
  `params_changed` plus the new values.

## 2. Global EQ and I/O Settings presets

The same mechanism covers two things that are **not** blocks on the grid. They ride
pseudo-models — Global EQ is catalog hash `4004`, I/O Settings `31000`:

```
list_settings_presets("global_eq")     → 23 factory presets (Bass 8x10 Punch, …)
load_settings_preset("global_eq", "Bass 8x10 Punch")
```

> **These are global.** They change what every preset on the unit sounds like, and
> reloading a preset does not undo them.

Global EQ is fully reversible — `load_settings_preset` returns the previous 28
parameter values, and `set_global_eq` writes them back exactly:

```
prev = load_settings_preset("global_eq", "Bass 8x10 Punch")["previous"]
set_global_eq(prev["global_eq"], prev["bypassed"])      # exact undo
```

**I/O Settings is not reversible that cheaply**: loading one rewrites the hardware
input levels, impedance and type, so it requires `confirm=True`, and it is refused
outright if the current settings can't be read first (there would be no way back).
Put values back with `set_io_port`, one port at a time:

```
set_io_port("in", 2, level=0.3196, impedance=0.125, input_type=1.0)
```

`input_type` is a **3-position** control — `0.0` instrument · `0.5` mic · `1.0`
line. Cortex Control's own panel only offers instrument and mic, so a port set to
*line* on the unit's screen can be read and restored here but **not** through the
app. `get_io_settings` names it correctly.

## 3. Footswitch assignments, including dual (secondary)

```
assign_stomp(row=0, column=0, footswitch="E")                    # bypass on switch E
assign_stomp(row=0, column=0, footswitch="F", kind="secondary")  # second function on F
assign_stomp(row=0, column=1, footswitch="G", momentary=True)    # active only while held
unassign_stomp(row=0, column=0, footswitch="E")
```

A block holds **one assignment per kind**, so a capable device can occupy two
switches at once — verified with a Vintage Digital reverb on E (PRIMARY) and F
(SECONDARY). Assigning the same kind again *moves* it rather than adding.

> The device does **not** check whether a device has a second function. It accepts
> `kind="secondary"` on anything — on a plain drive the switch is simply consumed
> and does nothing. Only use it on devices that have one (Vintage Digital, Aeons
> Reverb).

`get_current_preset` reports the current bindings as `stomp_assignments`.

## 4. Favorites and Recents, split by type

4.1 keeps three separate lists of 64 entries each:

```
list_favorites(favorites=True, kind="preset")    # also "ir", "capture"
```

On a 4.0 device the `kind` argument is ignored and the single combined list comes
back, so the same call is safe either way.

## 5. External MIDI clock

`get_tempo` reports the preset tempo alongside an incoming clock's BPM and whether
it is outside the device's usable range — useful when the QC is slaved to a DAW or
drum machine:

```json
{"external_midi_clock_bpm": 0.0, "external_midi_clock_out_of_range": false,
 "external_clock_present": false}
```

## 6. New devices in the catalog

The catalog grew **533 → 633 models**; `find_devices` searches all of them.

- **Eight new native devices:** Multivoicer (Pitch), Glitch (Morph), Ring Modulator
  (Morph), Arpeggio Delay, Crystal Delay, Vintage Digital (Reverb), Douglas Shining
  Comp, Plugin Parametric-4.
- **Five X plugin device sets** — Petrucci, **John Mayer**, Rabea, Misha Mansoor,
  Tim Henson — each with amps, cabs, drives, comps and effects. These appear in the
  catalog on every unit but need that plugin's licence to make sound; if a block
  loads but stays silent, that's the licence, not the routing.
- **Renames:** `30001` Mono Synth → **Overlord Synth**, and the Darkglass® models to
  Douglas (`3000` B3K → Douglas MT 3K, `21001`/`33001` 210C → 210 Douglas Ceramic, …).

If you are chasing an artist tone and they're one of those five, use their real
devices instead of substituting.

## Not covered yet

- **Multiple device management** (Cortex Control 4.1 can drive several units). The
  MCP assumes one; the bridge binds to a single app session.
- **`RemoteControl`(72)** — the new command for driving the QC's own screen. A bare
  READ draws no reply, so it presumably needs an enable step; unreversed.

## After a firmware update

A CorOS update invalidates three cached things, none of which fail loudly:

```bash
interceptor/build.sh                      # re-instrument the updated app copy
tools/build_descriptors.py build 4.2      # new wire schema (then: diff 4.1 4.2)
tools/dump_model_repo.py --diff           # new device catalog, then without --diff
```
