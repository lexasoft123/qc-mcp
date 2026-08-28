# qc-mcp — control a Neural DSP Quad Cortex from Claude (or any MCP client)

An MCP server that reads and controls a **Neural DSP Quad Cortex** — inspect the
current preset and signal chain, **build presets from a prompt**, switch presets /
scenes / modes, edit blocks and parameters, and read hardware I/O — by speaking
Cortex Control's internal **USB‑HID protocol**, reverse‑engineered from scratch.

> There is no public API for the Quad Cortex. This was built by inspecting the
> device and the Cortex Control app. **Unofficial; not affiliated with Neural DSP.**
> Full protocol write‑up in [PROTOCOL.md](PROTOCOL.md).

https://github.com/lexasoft123/qc-mcp

---

## What it can do

- **Read** the full live preset the way Cortex Control does on boot — every block,
  grid position, routing, splitters/mixers, and parameter (in real display units).
- **Build presets from natural language** — pick devices from the 633‑device
  catalog, lay out parallel/multiamp topologies with splitters + routing, set
  parameters, and save. (Reproduces complex multi-amp presets faithfully.)
- **Live control** — switch presets, scenes (A–H), performance modes, master volume.
- **Device presets** (CorOS 4.1) — list, recall and save a device's settings, so a
  dialled-in amp or drive can be reused in any rig instead of rebuilt.
- **Footswitch assignments** — bind blocks to stomp switches A–H (latching or
  momentary), including 4.1's secondary/dual assignments.
- **Two connection modes** — seize the device directly, or (on macOS) run
  **alongside a live Cortex Control** via a shared session (bridge mode).
- **Works across firmware** — CorOS 4.0 and 4.1 both supported; the wire schema is
  chosen per connection from the device's version, and newer‑only tools say so
  rather than failing silently.

New in CorOS 4.1 — device presets, dual footswitch assignments, Global EQ / I/O
Settings presets, and 100 new devices: see **[docs/COROS-4.1.md](docs/COROS-4.1.md)**.

## Requirements

- **macOS** (IOKit HID via ctypes) or **Windows** (setupapi + hid.dll via ctypes)
- A Quad Cortex on USB, and **Cortex Control** installed (CorOS 4.0 or 4.1)
- Python 3.10+

Windows runs direct mode only — every device tool works, but bridge mode and the
GUI harness are macOS‑specific (they need dyld injection and macOS screen/
accessibility APIs). Verified on Windows 10 22H2 x64 against a QC on CorOS 4.1.0,
including the full 8336‑capture directory stream. See
**[docs/WINDOWS.md](docs/WINDOWS.md)**.

## Install

```bash
git clone <your-repo-url> qc-mcp && cd qc-mcp
./install.sh
```

On Windows, use the PowerShell twin instead — same behaviour, no `[gui]` extra:

```powershell
git clone <your-repo-url> qc-mcp
cd qc-mcp
.\install.ps1
```

`install.sh` is idempotent and does everything: creates the venv, installs the
package (with the GUI verification extras), and registers `quad-cortex` with
Claude Code at **user scope** — the server is then available in *every* Claude
session, from any folder, without opening Claude in this repo. Re-run it after
moving the repo to re-register the new path. Use `./install.sh --local` if you
prefer the registration confined to this folder.

Two notes:
- The repo also ships a project-scope `.mcp.json`, so even without running
  `install.sh`, opening Claude Code inside the repo offers the server (it just
  expects `.venv` to exist — so run the installer once anyway).
- The repo's **skills and CLAUDE.md knowledge** (routing recipes, gotchas, CPU
  model) only load for sessions opened **inside the repo** — from other folders
  you get raw device control without that expertise.

Other MCP clients — point them at the venv binary:

```json
{ "mcpServers": { "quad-cortex": { "command": "/absolute/path/qc-mcp/.venv/bin/qc-mcp" } } }
```

(on Windows: `C:\path\to\qc-mcp\.venv\Scripts\qc-mcp.exe`)

## Two ways to connect

**Direct (default).** The MCP seizes the QC's HID interface. Only one client at a
time — **quit Cortex Control first**, and disconnect the MCP before reopening it.

**Bridge (simultaneous, macOS only).** Run the MCP *alongside* a running Cortex
Control by sharing its session, so the app's UI stays in sync. Requires building
an instrumented copy of Cortex Control once (it injects a small logging/bridge
dylib — hence macOS only):

```bash
interceptor/build.sh          # one-time: build the instrumented app (re-signs a local copy)
interceptor/run-bridge.sh &   # launch it
# the MCP auto-detects the bridge and runs alongside the app
```

See [interceptor/](interceptor/) and PROTOCOL.md §11. On Windows `connect()`
skips the question and goes direct; `docs/WINDOWS.md` covers what a Windows
bridge would take.

## Tools (a selection)

| tool | type | description |
|------|------|-------------|
| `get_current_preset` | read | full preset: blocks, positions, routing, params (display units) |
| `get_io_settings` | read | hardware inputs/outputs, headphones, USB |
| `find_devices` | read | search the catalog by name / emulated gear / category |
| `build_preset` | **write** | build a whole preset from a spec (chains, routing, splitters, blocks, params) |
| `add_block` / `remove_block` | **write** | place / delete a block at (row, col) |
| `set_parameter` | **write** | set a parameter in display units (taper‑aware) |
| `clear_grid` | **write** | reset the grid to a clean single chain |
| `switch_preset` / `switch_scene` / `switch_mode` | **write** | navigate presets, scenes A–H, modes |
| `list_device_presets` / `load_device_preset` | read / **write** | a device's saved settings (CorOS 4.1+), recalled onto a block |
| `save_device_preset` / `delete_device_preset` | **write** | store a block's current knobs as a reusable user device preset |
| `assign_stomp` / `unassign_stomp` | **write** | bind a block to a footswitch (A–H), latching or momentary |
| `list_settings_presets` / `load_settings_preset` | read / **write** | Global EQ and I/O Settings presets (CorOS 4.1+) |
| `set_global_eq` | **write** | write Global EQ parameters back — the exact undo for a preset load |
| `set_io_port` | **write** | hardware input/output level, impedance, type, ground lift, mute |
| `get_tempo` | read | preset tempo + external MIDI-clock BPM and out-of-range flag |
| `set_master_volume`, `save_preset`, `connect`/`disconnect`, `device_info`, `cpu_load` | | |

The Python API (`qc_mcp.transport.QuadCortex`, `qc_mcp.preset.PresetBuilder`) exposes
lower‑level building blocks (splitters, per‑lane params, routing, etc.).

## Example: build a preset from a prompt

> "Make a clean Vox‑style tone for The Shadows — AC15 Top Boost, a tape echo around
> 220 ms with a few repeats, and a touch of spring reverb."

The model uses `find_devices` to pick the amp/cab/delay/reverb, then `build_preset`
to lay out `Comp → AC15 → cab → Tape Echo (220 ms) → Spring` with the right params.

## How it works (short version)

Cortex Control talks to the QC over USB‑HID using protobuf messages wrapped in
chunked 128‑byte reports. This project:

- speaks the HID framing directly — IOKit on macOS (`src/qc_mcp/iohid.py`),
  setupapi/hid.dll on Windows (`src/qc_mcp/winhid.py`), picked by
  `src/qc_mcp/backend.py`,
- encodes/decodes the message layer incl. gzip, and negotiates the wire schema
  against the device's CorOS version (`src/qc_mcp/protocol.py`),
- maintains the session + heartbeat the QC needs to stream state
  (`src/qc_mcp/transport.py`),
- resolves every block to its real gear + parameters, with a calibrated value taper
  (`src/qc_mcp/catalog.py`),
- models/builds whole presets (`src/qc_mcp/preset.py`),
- exposes it all as MCP tools (`src/qc_mcp/server.py`).

## Status

Working: reading, live control, and **accurate preset building** (topology, routing,
splitters/mixers, and parameters in real units). See [PLAN.md](PLAN.md).
Also working: per‑scene parameter/bypass values, footswitch (stomp) assignments,
and device presets. Not yet reversed: `RemoteControl`(72), the 4.1 command for
driving the QC's own screen.

## Layout

```
src/qc_mcp/   protocol.py transport.py catalog.py preset.py server.py
              backend.py    picks the HID backend for the OS
              iohid.py      macOS  (IOKit)      winhid.py  Windows (hid.dll)
              bridge.py     share Cortex Control's session (macOS)
              descriptors/  one protobuf schema per CorOS generation
proto/        recovered Preset.proto, ProductionAutomation.proto, ModelRepo.xml
tools/        reverse-engineering utilities; win_hid_check.py diagnoses Windows
interceptor/  DYLD interposer: capture traffic + bridge mode (interpose.c, build.sh)
PROTOCOL.md   full protocol writeup   PLAN.md   preset-building plan
docs/         COROS-4.1.md  DIRECTORY.md  CPU.md  WINDOWS.md
```

## DISCLAIMER

This is unofficial software that controls audio hardware over a reverse‑engineered
protocol. It can change and overwrite presets on your device. **Use at your own
risk**; there is no warranty (see LICENSE). Not affiliated with or endorsed by
Neural DSP. Device/model names belong to their respective owners; the emulated‑gear
catalog is Neural DSP's public device list: https://neuraldsp.com/device-list

Please don't commit capture logs (`interceptor/*.log`, `interceptor/msgs/`) — they
can contain your session id and cloud auth token. They are gitignored by default.
