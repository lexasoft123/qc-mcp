---
name: reverse-hardware-transport
description: Identify how a desktop app talks to a USB/hardware device (which transport, interface, and endpoints) before reversing its protocol. Use when a device has no public API and you need to find the control channel — e.g. "how does this app control my audio interface / pedal / synth over USB", ruling out MIDI vs raw bulk vs HID vs vendor control vs CoreAudio.
---

# Reverse a hardware app's transport

Goal: find *where* the bytes flow before you reverse *what* they mean. Don't assume
MIDI. Establish: transport (HID / bulk / control / CoreAudio / network), the USB
interface + endpoints, and how the app opens it (so you can replicate access).

## Method (macOS; adapt paths for other OSes)

1. **Find the app + device, and whether the app is running.**
   ```bash
   mdfind -name "AppName"; ls -la /Applications/**/AppName.app
   ps aux | grep -i appname | grep -v grep
   ```

2. **Confirm the USB device and read its descriptors** (VID/PID, class, interfaces,
   endpoints). `ioreg` is more reliable than `system_profiler`:
   ```bash
   ioreg -p IOUSB -l -w0 | grep -iE "USB Product Name|idVendor|idProduct"
   ioreg -w0 -l | sed -n '/YourDevice@/,/NextDevice@/p' | grep -iE \
     "Interface|bInterfaceClass|Endpoint|MIDIServer|IOHIDLibUserClient|UserClient"
   ```
   Note the composite class (0xEF/IAD = audio+more), and **which macOS driver owns
   each interface** (AppleUSBAudio / MIDIServer / IOHIDLibUserClient). The client
   names under the device (e.g. "MIDIServer", "Google Chrome") reveal who has it open.

3. **Check the app process's own connections** — rule out network:
   ```bash
   lsof -nP -p <pid> | grep -iE "TCP|UDP"   # empty => not network
   ```

4. **Read the app's log** — class/method names leak the transport:
   ```bash
   tail -60 ~/Library/Logs/**/appname*.log   # look for UsbController, *MessageReceiver
   ```

5. **Static clues from the binary** — the decisive step:
   ```bash
   BIN=".../AppName.app/Contents/MacOS/AppName"
   otool -L "$BIN"                                  # linked frameworks (IOKit? CoreMIDI? CoreAudio?)
   nm -u "$BIN" | grep -iE "IOHID|IOUSB|IOService|IOConnect|IOCreatePlugIn"
   strings -n5 "$BIN" | grep -iE "UsbController|ReportStream|SetReport|Bulk|deviceRequest"
   codesign -d --entitlements :- "$BIN" | tr -d '\0'   # entitlements (usb/audio/dyld)
   ```
   - Imports of `IOHIDManagerCreate` / `IOHIDDeviceSetReport` / `IOHIDDeviceRegisterInputReportCallback`
     ⇒ **USB-HID reports** (host→dev = SetReport, dev→host = input callback).
   - `IOCreatePlugInInterfaceForService` + no HID ⇒ classic **IOUSBLib** (control/bulk).
   - Heavy CoreAudio + few IOKit symbols ⇒ control tunneled via the **audio device**.

6. **Test userspace access** matching the finding (e.g. pyusb control pipe, or hidapi
   for HID). If the interface is owned by a macOS class driver (MIDIServer/audio),
   libusb `claim_interface` will be denied — HID or device-level access is the path.

## Gotchas
- A device can be a CoreMIDI port yet carry its real control protocol elsewhere
  (monitor the MIDI port; if nothing flows during app activity, it's not MIDI).
- `SetReport` returning a nonzero IOReturn may be **benign** — check the real app
  ignores it too before assuming failure.
- The interface may be opened **exclusively** (seize); then you must quit the app to
  take it, or share via injection (see `macos-dylib-interpose`).

Worked example: this repo's QC is USB-HID (128-byte reports), found via IOHID
symbols + `usb::DeviceReportStream` strings. See PROTOCOL.md §1.
