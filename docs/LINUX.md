# Linux

qc-mcp runs on Linux in **direct mode** over hidraw (`src/qc_mcp/linuxhid.py`).
Every device tool works the way it does on Windows. Bridge mode and the GUI
harness don't exist here: Cortex Control has no Linux build, so there is no app
session to share and nothing to drive.

Tested on a Quad Cortex Mini (CorOS 4.1.0, Arch Linux, `hid-generic`): reading
state and the full directory, recalling and saving presets, switching modes. The
full-size Quad Cortex (`880a`) is matched by the udev rule but untested.

## Install

```bash
./install.sh
sudo install -m 644 udev/70-quad-cortex.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=hidraw
```

`install.sh` skips the `[gui]` extra's pyobjc on Linux by marker, so it works
unchanged. The udev rule tags the device `uaccess`, which gives the logged-in
seat user read/write on its hidraw node; without it the node is root-only and
`connect` says so.

## How the device appears

The QC exposes its audio interfaces to ALSA/PipeWire and one vendor HID
collection (USB interface 5) as `/dev/hidrawN`:

```
HID_ID=0003:0000152A:0000892F
HID_NAME=Neural DSP Quad Cortex mini
report descriptor: report 1 in, report 2 out, 128 bytes each
```

hidraw prefixes the report id on both read and write, so reports are 129 bytes,
the same as the Windows backend's `InputReportByteLength`.

## Exclusivity

hidraw has no exclusive open. With `seize=True` the backend takes a `flock` on
the node so a second qc-mcp can't interleave frames with the first; the daemon's
lockfile is the main guard.
