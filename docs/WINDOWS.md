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

## Running alongside Cortex Control

This works on Windows, and needs **no interposer at all**.

macOS needs one because IOKit gives the device to a single owner: the only way in
is to inject a dylib into Cortex Control and share its session. Windows behaves
differently — the HID class driver **copies every input report to every open
handle**, and Cortex Control opens the device with `FILE_SHARE_READ|WRITE`. So
the MCP just opens its own non-exclusive handle next to the app:

```python
QuadCortex(share=True)      # open_hid(seize=False)
```

`connect(mode='bridge')` does this for you, starting the stock app first if it
isn't running. The daemon's `auto` takes the same non-exclusive handle **whether
or not Cortex Control is up**: a handle already held exclusively can never be
shared afterwards, so seizing first would lock the app out for the rest of the
session — start the daemon, and Cortex Control opens to "no device" until you
disconnect. Sharing costs nothing while the app is shut (our own reads and
writes are byte-identical either way), so `direct` is the explicit way to ask
for exclusivity, worth it for heavy multi-report work like building presets. Verified with the app running: `CorOS 4.1.0`, live
preset/position/mode reads, and a **write** — amp VOLUME 6.0 → 4.0, read back
4.0, then restored — with Cortex Control alive throughout. Control, not just
eavesdropping.

### ERROR_GEN_FAILURE(31) is noise here, not a failed write

`WriteFile` on this device returns `31` constantly on paths whose writes provably
land: the direct-mode session that wrote VOLUME and read the new value back
reported **60** of them. It is the Windows counterpart of IOKit's harmless
`0xe0005000`, and `WinHIDTransport.BENIGN_WRITE_CODES` lists it as such.

Do not use it to decide whether a write worked — **read the value back**. Chasing
it cost a long detour here, and twice led to the wrong conclusion that a second
handle could not send. `QuadCortex` counts non-benign codes in `write_errors` /
`last_write_error` if you need a signal, but a genuine lost write shows up as a
read-back mismatch, not as an error code.

One more trap from that detour: **pick a continuous parameter when testing
writes.** Param 0 of an amp is `INPUT`, a discrete selector that clamps — writing
2.0 reads back 1.0 and looks exactly like a dropped write, in direct mode too.

### The caveat: two writers on one endpoint

The app and the MCP are then independent writers on the same HID endpoint, with
nothing sequencing them. A single-report message is one `WriteFile` and is
atomic, but a message split across several reports can interleave with the
other side's and corrupt both.

Measured over one app session (`interceptor-win`'s log, flags byte of each
host→device report):

| flags | meaning | count |
|-------|---------|-------|
| `c0` | SINGLE (whole message in one report) | 76 |
| `40` | FIRST of a multi-report message | 1 |
| `80` | LAST of a multi-report message | 1 |

So ~97% of the app's traffic can't interleave at all, and the exposure is the
occasional multi-report message (startup here; preset saves and uploads are the
other case). State divergence is *not* a concern — the device pushes state to
every open handle, so the app's UI keeps up with changes the MCP makes.

Practical rule, which `connect()` returns as a `caution` when it shares:
**reads and light edits alongside the app are fine; for building or saving
presets, use `connect(mode='direct', quit_app=True)`** so nothing else is
writing.

## The interposer (`interceptor-win/`) — capture only

The Windows twin of `interceptor/`, for reverse-engineering rather than for
bridge mode. `qclaunch.exe` starts Cortex Control suspended, `LoadLibrary`s
`qcinject.dll` into it, and resumes; the DLL patches five KERNEL32 import slots.

It works because Cortex Control (x64, one static exe) imports **no `hid.dll`**
and has no delay-load table — it reaches the device purely through `CreateFileW`
/ `ReadFile` / `WriteFile` / `GetOverlappedResult`, all in the plain import
table. So IAT patching is enough; no Detours or MinHook.

```powershell
.\interceptor-winuild.ps1       # needs VS Build Tools (C++ workload)
.\interceptor-win
un-bridge.ps1  # launches the app with QC_LOG/QC_VERBOSE set
```

Verified capture on CorOS 4.1.0: 61 host→device and 1432 device→host frames in
one session, framing decoding exactly as PROTOCOL.md §2 describes.

Two things to know:

- **Enumeration opens the device with access 0** just to read its ids, then
  closes it. Latch onto one of those and you lose the real handle the moment it
  closes — filter on `access & (GENERIC_READ|GENERIC_WRITE)`.
- **Injection is unverified.** Frames queued from the pipe are written to the
  device, but a forced pipe-bridge session never got a reply to its own request,
  so something in the path is wrong. Note the diagnosis attempted at the time —
  "ERROR_GEN_FAILURE means a concurrent/second-handle write was refused" — is
  now known to be **false**: 31 is noise (above), so those runs proved nothing
  either way and the question is simply open. Nothing needs it: shared handles
  cover running alongside the app. It would matter only if a future CorOS
  started opening the device exclusively, which is also when
  `qc_mcp/winbridge.py` — the tested named-pipe client for this DLL — would
  become the transport.

## Still macOS-only

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
      +-- backend.open_hid(seize=)   picks by sys.platform
      |      |                       seize=False = the Windows "alongside" mode
      |      +-- iohid.IOHIDTransport    macOS   (IOKit HID via ctypes)
      |      +-- winhid.WinHIDTransport  Windows (setupapi + hid.dll via ctypes)
      |
      +-- backend.open_bridge()      chosen by connect(mode='bridge') on macOS
             |
             +-- bridge.FifoBridge      macOS, over the DYLD interposer's FIFOs
             +-- winbridge.WinBridge    Windows, over the DLL's named pipes
                                        (built + tested, not currently used)
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
  A Quad Cortex Mini is `PID_892F` on the same interface with the same
  129-byte reports; `backend.QC_PIDS` holds the family and both transports
  match all of it.
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

Writes verified end-to-end from Windows (into an empty slot, never a named
preset): `build_preset` (5 blocks), `set_parameter`, `set_parameter_scenes`,
per-scene `set_block_bypass`, `set_preset_meta` scene labels, `assign_stomp`,
`list_device_presets`, `save_preset_as`, `switch_scene`. The save was confirmed
committed by re-reading the device's own directory, which lists the new preset at
the target position — not just by the success string.

Running alongside the app verified too, reads *and* writes: with the stock Cortex
Control up, `connect(mode='bridge')` joined on a shared handle (`shared=True,
exclusive=False`), read firmware/preset/position/mode, wrote an amp VOLUME and
read the new value back, and left the app running.

Not verified: the interposer's **injection** path (open — see above), and Global
EQ / I/O writes, which are global and destructive and were left alone.

Console note: runtime messages are plain ASCII on purpose. A Windows console on a
legacy codepage (e.g. cp866) renders em-dashes as `?`, so the Windows-facing
strings avoid them. Keep it that way when editing `winhid.py` or
`win_hid_check.py`.
