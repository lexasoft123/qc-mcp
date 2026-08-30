# Patchbay

Ask Claude for a tone and it builds it on your Quad Cortex. Patchbay is the
launcher that makes that true: it installs [qc-mcp](../README.md), registers the
server with your MCP clients, runs the daemon that owns the device, and opens
Cortex Control alongside it — so nobody has to remember `install.sh`,
`interceptor/build.sh` and `run-bridge.sh` in the right order.

![Patchbay connected — the signal path from Claude through Patchbay to a Quad Cortex, all three hops lit, sharing Cortex Control's live session over the bridge](../docs/patchbay/home.png)

Electron + React + TypeScript, built on
[@singz/ui](https://github.com/lexasoft123/singz-ui) — the night-studio design
language, in amber. Every colour is a `--sz-*` token and every control is a kit
component, so the class names (`pill`, `dot`, `mode-seg`, `modal-card`) are the
same contract SingZ uses.

**A fresh machine needs nothing pre-installed.** Not even Python: Patchbay
ships `uv`, which fetches its own CPython 3.12 and builds the environment in
about eight seconds. That gap is the whole reason — macOS's `/usr/bin/python3`
is 3.9.6, under the 3.10 the package needs, and Windows has none at all.

## What it does

- **One button** — Home shows the signal path, Claude → Patchbay → Quad Cortex,
  with each hop lit only when it is really live. **Connect** runs whatever is
  missing, in order: the environment, the instrumented build, the client
  registration, Cortex Control, the daemon. Press it again to disconnect.
- **Shares the device instead of fighting for it** — on macOS a DYLD interposer
  rides Cortex Control's own HID session, so the app and Claude both talk to the
  Quad Cortex at once. On Windows the daemon opens a second, non-exclusive
  handle beside the app. Either way you keep using Cortex Control while Claude
  edits presets.
- **One daemon, every client** — the daemon owns the device and fans
  device→host reports out to every attached MCP client, each with a disjoint
  `request_id` range so two can never mistake each other's replies. Claude Code,
  Claude Desktop, Cursor and VS Code can all be attached at the same time.
- **Setup that actually checks** — seven probes on macOS, five on Windows, each
  one a real measurement rather than a stored flag, with a fix for the ones
  Patchbay can perform. It never asks for your password.
- **The wire, live** — the Logs view parses the interposer's frame log as it is
  written: direction, report size, hex, and a filter for errors.
- **Your installed Cortex Control is never touched.** The instrumented build is
  a *local copy*, re-signed ad-hoc with the app's own entitlements carried over.

### Console — every module, measured

![The Console view: the connected Quad Cortex, which MCP clients hold the server entry, and the running daemon with its mode, socket, session and live reports-per-second](../docs/patchbay/console.png)

Three independent modules: which client configs hold the server entry, the
daemon (mode, pid, socket, measured reports/s), and Cortex Control. The mode
selector switches auto / bridge / direct on a running daemon.

### Setup — seven real probes

![The Setup view with all seven checks green: bundled Python, the virtual environment, command line tools, Cortex Control 4.1.0, the instrumented copy, client registration and the Quad Cortex on USB](../docs/patchbay/setup.png)

### Logs — the interposer's frame log

![The Logs view streaming HID reports with timestamps, direction, byte counts and hex, with failed writes highlighted](../docs/patchbay/logs.png)

## Install

Download the installer for your platform from
[Releases](https://github.com/lexasoft123/qc-mcp/releases) — `.dmg` on macOS
(Apple silicon and Intel), `.exe` on Windows.

The macOS `.dmg` is signed with a Developer ID certificate and notarized by
Apple, so it opens by double-clicking with no warning and no right-click
dance. Windows is **not** signed yet, so the `.exe` still needs
**More info** → **Run anyway** past SmartScreen.

Then press **Connect**. First run takes a couple of minutes, mostly building the
instrumented copy; after that it is a few seconds.

## What is real

Everything the main process reports is measured, not mocked:

| check | how |
|---|---|
| Python | the bundled `uv --version`, or a system `python3` / `py -3`, parsed and version-gated |
| venv | the `qc-mcp` entry point exists in `.venv` |
| clang | `clang --version` (macOS only) |
| Cortex Control | `Info.plist` via `defaults read`, or the exe's `ProductVersion` |
| instrumented copy | `codesign -dvvv` flags **and** the entitlements — both, because injection needs the hardened runtime off *and* library validation disabled |
| registration | each client's own config file is read for the `quad-cortex` key |
| device | `ioreg -p IOUSB` for vid `0x152a` / pid `0x880a`, or `Get-PnpDevice` |
| reports/s | counted from the interposer's own millisecond stamps in the last 2 s |

Installs are real too: `uv venv` + `uv pip install -e .` (or `python -m venv` +
`pip` when there is no bundled uv), `interceptor/build.sh` streamed line by
line, `claude mcp add` when the CLI is present (and a careful JSON merge into
the other clients when it is not — the server entry is the only key Patchbay
ever touches).

What ships in the installer and what gets built on your machine, and why:
**[docs/PACKAGING.md](../docs/PACKAGING.md)**.

## The daemon

`qc-mcp` grew a daemon so this launcher's model is real: **one process owns the
device, every client shares it.**

```
qc-mcp                                 stdio MCP server (unchanged default)
qc-mcp --daemon --socket PATH [--mode auto|bridge|direct]
qc-mcp --attach --socket PATH          stdio MCP server riding the daemon
```

The split sits as low as it can: the daemon moves *HID reports*, and framing,
gzip, protobuf, the catalog and the preset model still run inside each client.
Device→host reports are broadcast to every attached client — the same fan-out
the interposer's FIFO already does — and each client is handed a disjoint
request_id range at hello so two of them can never mistake each other's replies
for their own.

Patchbay spawns it, waits for the endpoint to actually accept a connection (a
socket file outlives a crashed daemon, so existence alone lies), and reports the
real stderr if it refuses to start.

**Order matters on macOS.** The daemon picks bridge vs direct once, at startup,
from whether the instrumented Cortex Control is already up — so Connect launches
the app *first* and waits for both FIFOs and the process before starting the
daemon. Starting the daemon first silently produces direct mode, which seizes
the device and leaves the interposer unused.

## Platform differences

They are not cosmetic, and they are not a runtime toggle — the main process
reports its own platform and the UI follows.

|  | macOS | Windows |
|---|---|---|
| sharing the device | DYLD interposer in a re-signed copy of Cortex Control | a second, non-exclusive HID handle |
| setup checks | 7 | 5 — no compiler, no instrumented copy |
| Cortex Control module | maintained (build, verify, rebuild on drift) | observed; the daemon works with the app closed |
| endpoint | unix socket | loopback port + a `.port` file |
| backdrop blur | yes | **no** — a Windows iGPU pays a full-window re-raster per blurred surface, which is why the kit's own `chrome.css` already drops it from the scrim |

Both paths are exercised on real hardware: the daemon, its tests and two
concurrent attached clients have been run against a Quad Cortex on macOS and on
Windows 10, and the device probe, the loopback endpoint and the `.port` file are
verified there rather than inferred.

Windows **shared mode** is verified too — with Cortex Control running, the
daemon picks the second non-exclusive handle and serves attached clients while
the app keeps its own session. That is the case the "independent writers"
caution in the Console describes.

## Develop

```bash
npm install     # electron's postinstall must run — approve it if npm asks
npm run dev     # the app, with HMR
npm run build   # typecheck + bundle into out/
npm run icons   # redraw build/icon.icns + .ico from build/icon/forge.html
```

The icon is drawn on canvas and cut with the same Chromium the app runs, so what
`forge.html` draws is what ships — a signal chain across the Quad Cortex Grid,
spiking. The full mark holds down to 48 px; 16 and 32 get a simplified one.

## Build & releases

```bash
npm run dist:mac     # -> dist/*.dmg  (arm64 and x64, one arch per invocation)
npm run dist:win     # -> dist/*.exe  (nsis)
```

`dist:*` fetches the pinned `uv` first. Tagging `v*` and pushing runs
[.github/workflows/release.yml](../.github/workflows/release.yml), which gates on
the offline Python suite, packages both platforms, and attaches the artifacts.

## One caveat worth knowing

A GUI-launched app on macOS does not inherit your shell's `PATH` — `launchctl
getenv PATH` is usually unset, so `~/.local/bin` is invisible and the `claude`
CLI cannot be found. Registration then falls back to editing each client's JSON
directly. That fallback is correct, but it means Claude Code's own config file
is rewritten by us rather than by its CLI, so prefer launching Patchbay from a
shell (or install `claude` somewhere on the default path) if you can.
