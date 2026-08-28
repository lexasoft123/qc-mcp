# Windows

qc-mcp runs on Windows in **direct mode**: it opens the Quad Cortex's HID
interface exclusively and speaks the same protocol as on macOS. Every device tool
works — reading presets, building them, scenes, footswitches, device presets,
Global EQ / I/O. What is *not* available is bridge mode and the GUI harness; both
are macOS-only for reasons below.

## Install

```powershell
git clone <your-repo-url> qc-mcp
cd qc-mcp
.\install.ps1
```

If PowerShell blocks the script ("running scripts is disabled on this system"),
run it once as:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

`install.ps1` mirrors `install.sh`: it creates `.venv`, installs the package
editable, and registers `quad-cortex` with Claude Code at user scope. It skips
the `[gui]` extra, which pulls in pyobjc.

For other MCP clients, point them at the venv binary (note the Windows path and
the escaped backslashes JSON requires):

```json
{ "mcpServers": { "quad-cortex": { "command": "C:\\path\\to\\qc-mcp\\.venv\\Scripts\\qc-mcp.exe" } } }
```

## Connecting

`connect()` on Windows goes straight to direct mode — there is no second option
to ask about. **Cortex Control must be closed**: it holds the HID interface
exclusively, and so do we. If it is running, `connect` says so; pass
`quit_app=True` to have the MCP close it first (a normal `taskkill` close
request, not a force kill).

When you want the app back, call `disconnect()` first — the device is only ever
open to one program at a time.

## Checking the device is reachable

```powershell
.venv\Scripts\python.exe tools\win_hid_check.py
```

It separates the three ways this fails and tells you which one you hit:

| exit | meaning | fix |
|------|---------|-----|
| 0 | the device answered | nothing — you're set |
| 3 | not enumerated | power on the QC; use a **data** USB cable (charge-only cables enumerate nothing); check Device Manager → Human Interface Devices |
| 4 | enumerated but can't be opened exclusively | quit Cortex Control (or whatever else holds it) |
| 5 | opens, but no protocol reply | not a Windows problem — please open an issue with the output |

`--list` dumps every HID interface on the machine without opening anything, which
is what to attach to a bug report when the QC doesn't show up.

## What's macOS-only, and why

**Bridge mode** (running the MCP *alongside* a live Cortex Control, sharing its
session) works by `DYLD_INSERT_LIBRARIES`-injecting a small dylib into the app
that mirrors its HID traffic onto two FIFOs. That is a Mach-O/dyld mechanism with
no direct Windows equivalent — a Windows port would need DLL injection plus a
`HidD_*`/`WriteFile` hook (Detours or MinHook) into `Cortex Control.exe`, and its
own IPC (named pipes rather than FIFOs). The Python side is already
platform-agnostic: `bridge.FifoBridge` implements the same four-method backend
API as the HID transports, so a `WinBridge` would slot in at
`backend.BRIDGE_PLATFORMS` without touching the transport.

**The GUI harness** (`tools/gui/`) drives Cortex Control by window-id
`screencapture` and reads its JUCE accessibility tree through AppKit. Both are
macOS APIs. It is only used for reverse-engineering and on-screen verification,
never by the MCP server.

**The reverse-engineering tools** in `tools/` that shell out to `otool`, `codesign`
or the interposer are likewise macOS-only. `tools/build_descriptors.py` and the
offline tests are not.

## How the port is structured

```
transport.QuadCortex
      |
      +-- backend.open_hid()        picks by sys.platform
             |
             +-- iohid.IOHIDTransport    macOS   (IOKit HID via ctypes)
             +-- winhid.WinHIDTransport  Windows (setupapi + hid.dll via ctypes)
      |
      +-- bridge.FifoBridge          macOS only, chosen by connect(mode='bridge')
```

All three implement `open()` / `set_report()` / `read_reports()` / `close()` and
nothing above them knows which one it holds; `tests/test_platform.py` asserts
that, including the signatures.

Windows-specific details worth knowing if you touch `winhid.py`:

- Windows **pads every input report** to `InputReportByteLength` (129 here), so a
  5-byte frame reads back as 129 bytes. Harmless — the frame carries its own
  `[chunkLen]` and `P.Reassembler` slices to it.
- The HID driver's per-handle input queue holds **32 reports** by default and
  silently drops the rest. A directory listing streams thousands back-to-back, so
  `HidD_SetNumInputBuffers` raises it to 512. This is the single most likely cause
  of "reads work until I list the directory".
- The handle is opened `FILE_FLAG_OVERLAPPED`, so **every** read and write must
  pass an `OVERLAPPED` — and `close()` can `CancelIoEx` a pending read instead of
  leaving a thread wedged on a device that stopped talking.
- Writes must be **exactly** `OutputReportByteLength` bytes including the report
  id; short or long buffers are rejected outright.
- The QC publishes several HID collections; the protocol one is the collection
  with 129-byte reports, which is how `open()` picks.
- **A device held exclusively refuses even a zero-access probe open.** So
  enumeration cannot read its VID/PID and it disappears from the list entirely —
  which once made "quit Cortex Control" report as "no device found, is it plugged
  in?". `enumerate_devices` now falls back to the ids Windows encodes in the
  interface path (`...hid#vid_152a&pid_880a&mi_05#...`) and marks the entry
  `busy`, so `open()` still reaches `CreateFile` and returns the honest error.

## Verified on hardware

Windows 10 22H2 (19045) x64, Python 3.12.10, against a Quad Cortex on CorOS 4.1.0:

```
Quad Cortex HID interface | in=129 out=129 | exclusive=True
CorOS 4.1.0 | schema 4.1 | type QC | name 'QC MAX'
```

- The device presents **one** HID collection with 129-byte reports,
  `HID\VID_152A&PID_880A&MI_05` — interface 5, matching PROTOCOL.md §1.
- All four offline suites pass (6/6, 6/6, 5/5, 10/10).
- Reads verified identical to macOS: `get_current_preset` (4 chains, 11 blocks,
  stomp assignment), `get_mode`, `current_preset_position`, `get_tempo`,
  `get_io_settings`, `list_device_presets`, `search_directory`.
- **`directory_summary(refresh=True)`: 11.9 s, 3636 presets / 762 IRs / 8336
  captures, nothing dropped.** This is the input-queue test — thousands of reports
  streamed back to back. If you ever see it come back short, suspect
  `HidD_SetNumInputBuffers`.
- Five open/close cycles leave no lingering threads and no leaked handles.
- With the device already held, the second opener gets
  `CreateFile error 32` (ERROR_SHARING_VIOLATION) and the "quit Cortex Control"
  message; `win_hid_check.py` exits 4.

Not yet exercised on Windows: **writes** (building presets, saving, footswitch
assignment, Global EQ / I/O). They use the same `set_report` path as the reads
above and the same protocol layer as macOS, so there is no Windows-specific code
left untested — but no preset has actually been written from a Windows host.

Console note: runtime messages are plain ASCII on purpose. A Windows console on a
legacy codepage (e.g. cp866) renders em-dashes as `?`, so the Windows-facing
strings avoid them. Keep it that way when editing `winhid.py` or
`win_hid_check.py`.
