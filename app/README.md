# Patchbay

The launcher for [qc-mcp](../README.md). It does the setup once, runs the
daemon, and opens Cortex Control — so nobody has to remember `install.sh`,
`interceptor/build.sh` and `run-bridge.sh` in the right order.

Electron + React, built on [@singz/ui](https://github.com/lexasoft123/singz-ui)
— the night-studio design language. Every colour is a `--sz-*` token and every
control is a kit component, so the class names (`pill`, `dot`, `mode-seg`,
`modal-card`) are the same contract SingZ uses.

```bash
npm install     # electron's postinstall must run — approve it if npm asks
npm run dev     # the app, with HMR
npm run build   # typecheck + bundle into out/
```

## The four screens

- **Home** — the whole thing in one button. It shows the signal path
  (Claude → Patchbay → Quad Cortex) with each hop lit only when it is really
  live, and **Set up and connect** runs whatever is missing, in order.
- **Console** — three independent modules: which client configs hold the server
  entry, the daemon (mode, pid, socket, measured reports/s), and Cortex Control.
- **Setup** — the preflight checks, each one a real probe, with a fix for the
  ones Patchbay can perform.
- **Logs** — the interposer's frame log, parsed and filtered.

## What is real

Everything the main process reports is measured, not mocked:

| check | how |
|---|---|
| Python | `python3 --version` / `py -3 --version`, parsed and version-gated |
| venv | the `qc-mcp` entry point exists in `.venv` |
| clang | `clang --version` (macOS only) |
| Cortex Control | `Info.plist` via `defaults read`, or the exe's `ProductVersion` |
| instrumented copy | `codesign -dvvv` flags **and** the entitlements — both, because injection needs the hardened runtime off *and* library validation disabled |
| registration | each client's own config file is read for the `quad-cortex` key |
| device | `ioreg -p IOUSB` for vid `0x152a` / pid `0x880a`, or `Get-PnpDevice` |
| reports/s | counted from the interposer's own millisecond stamps in the last 2 s |

Installs are real too: `python -m venv` + an editable `pip install`,
`interceptor/build.sh` streamed line by line, `claude mcp add` when the CLI is
present (and a careful JSON merge into the other clients when it is not — the
server entry is the only key Patchbay ever touches).

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
Device->host reports are broadcast to every attached client — the same fan-out
the interposer's FIFO already does — and each client is handed a disjoint
request_id range at hello so two of them can never mistake each other's replies
for their own.

Patchbay spawns it, waits for the endpoint to actually accept a connection (a
socket file outlives a crashed daemon, so existence alone lies), and reports the
real stderr if it refuses to start.

## Platform differences

They are not cosmetic, and they are not a runtime toggle — the main process
reports its own platform and the UI follows.

|  | macOS | Windows |
|---|---|---|
| sharing the device | DYLD interposer in a re-signed copy of Cortex Control | a second, non-exclusive HID handle |
| setup checks | 7 | 5 — no compiler, no instrumented copy |
| Cortex Control module | maintained (build, verify, rebuild on drift) | observed; the daemon works with the app closed |
| endpoint | unix socket | named pipe |
| backdrop blur | yes | **no** — a Windows iGPU pays a full-window re-raster per blurred surface, which is why the kit's own `chrome.css` already drops it from the scrim |

Both paths are exercised on real hardware: the daemon, its tests and two
concurrent attached clients have been run against a Quad Cortex on macOS and on
Windows 10, and the device probe, the loopback endpoint and the `.port` file are
verified there rather than inferred.

Windows **shared mode** is verified too — with Cortex Control running, the
daemon picks the second non-exclusive handle and serves attached clients while
the app keeps its own session. That is the case the "independent writers"
caution in the Console describes.

## One caveat worth knowing

A GUI-launched app on macOS does not inherit your shell's `PATH` — `launchctl
getenv PATH` is usually unset, so `~/.local/bin` is invisible and the `claude`
CLI cannot be found. Registration then falls back to editing each client's JSON
directly. That fallback is correct, but it means Claude Code's own config file
is rewritten by us rather than by its CLI, so prefer launching Patchbay from a
shell (or install `claude` somewhere on the default path) if you can.
