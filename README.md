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
- **Build presets from natural language** — pick devices from the 533‑device
  catalog, lay out parallel/multiamp topologies with splitters + routing, set
  parameters, and save. (Reproduces complex multi-amp presets faithfully.)
- **Live control** — switch presets, scenes (A–H), performance modes, master volume.
- **Two connection modes** — seize the device directly, or run **alongside a live
  Cortex Control** via a shared session (bridge mode).

## Requirements

- macOS (uses IOKit HID via ctypes; the interposer/bridge is macOS‑specific)
- A Quad Cortex on USB, and **Cortex Control** installed
- Python 3.10+

## Install

```bash
git clone <your-repo-url> qc-mcp && cd qc-mcp
python3 -m venv .venv
./.venv/bin/pip install -e .
```

Register with Claude Code:

```bash
claude mcp add quad-cortex -- "$PWD/.venv/bin/qc-mcp"
```

Or in an MCP client config:

```json
{ "mcpServers": { "quad-cortex": { "command": "/absolute/path/qc-mcp/.venv/bin/qc-mcp" } } }
```

## Two ways to connect

**Direct (default).** The MCP seizes the QC's HID interface. Only one client at a
time — **quit Cortex Control first**, and disconnect the MCP before reopening it.

**Bridge (simultaneous).** Run the MCP *alongside* a running Cortex Control by
sharing its session, so the app's UI stays in sync. Requires building an
instrumented copy of Cortex Control once (it injects a small logging/bridge dylib):

```bash
interceptor/build.sh          # one-time: build the instrumented app (re-signs a local copy)
interceptor/run-bridge.sh &   # launch it
# the MCP auto-detects the bridge and runs alongside the app
```

See [interceptor/](interceptor/) and PROTOCOL.md §11.

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

- speaks the HID framing directly via IOKit (`src/qc_mcp/iohid.py`),
- encodes/decodes the message layer incl. gzip (`src/qc_mcp/protocol.py`),
- maintains the session + heartbeat the QC needs to stream state
  (`src/qc_mcp/transport.py`),
- resolves every block to its real gear + parameters, with a calibrated value taper
  (`src/qc_mcp/catalog.py`),
- models/builds whole presets (`src/qc_mcp/preset.py`),
- exposes it all as MCP tools (`src/qc_mcp/server.py`).

## Status

Working: reading, live control, and **accurate preset building** (topology, routing,
splitters/mixers, and parameters in real units). See [PLAN.md](PLAN.md).
In progress: **scenes/stomps** — per‑scene parameter/bypass values and footswitch
(stomp) assignments.

## Layout

```
src/qc_mcp/   iohid.py protocol.py transport.py catalog.py preset.py server.py + data
proto/        recovered Preset.proto, ProductionAutomation.proto, ModelRepo.xml
tools/        reverse-engineering utilities
interceptor/  DYLD interposer: capture traffic + bridge mode (interpose.c, build.sh)
PROTOCOL.md   full protocol writeup   PLAN.md   preset-building plan
```

## DISCLAIMER

This is unofficial software that controls audio hardware over a reverse‑engineered
protocol. It can change and overwrite presets on your device. **Use at your own
risk**; there is no warranty (see LICENSE). Not affiliated with or endorsed by
Neural DSP. Device/model names belong to their respective owners; the emulated‑gear
catalog is Neural DSP's public device list: https://neuraldsp.com/device-list

Please don't commit capture logs (`interceptor/*.log`, `interceptor/msgs/`) — they
can contain your session id and cloud auth token. They are gitignored by default.
