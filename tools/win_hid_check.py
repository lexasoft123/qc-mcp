#!/usr/bin/env python3
"""Windows HID diagnostic: does this machine see and speak to the Quad Cortex?

Run it before blaming the MCP - it separates the three ways a Windows setup
fails (device not enumerated / device held by Cortex Control / protocol not
answering) and prints exactly which one you hit.

    python tools\\win_hid_check.py            # enumerate + open + read Version
    python tools\\win_hid_check.py --list     # just list HID devices, open nothing

Exit code 0 = the device answered. Anything else = see the message.
"""
from __future__ import annotations
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

VID, PID = 0x152A, 0x880A


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true",
                    help="list every HID interface, don't open the device")
    ap.add_argument("--vid", type=lambda x: int(x, 0), default=VID)
    ap.add_argument("--pid", type=lambda x: int(x, 0), default=PID)
    args = ap.parse_args()

    if sys.platform != "win32":
        print(f"This check is for Windows; you're on {sys.platform}. "
              "On macOS use tools/hid_capture.py / the MCP's connect().")
        return 2

    from qc_mcp import winhid

    print(f"== HID interfaces matching {args.vid:#06x}:{args.pid:#06x} ==")
    try:
        found = winhid.enumerate_devices(args.vid, args.pid)
    except OSError as e:
        print(f"  enumeration failed: {e}")
        return 3
    if not found:
        print("  none.")
        if args.list:
            print("\n== every HID interface on this machine ==")
            for d in winhid.enumerate_devices():
                print(f"  {d['vid']:#06x}:{d['pid']:#06x}  in={d['input_len']:<4}"
                      f" out={d['output_len']:<4} {d['product']}")
        print("\nThe Quad Cortex isn't enumerating. Check that it's powered on, that\n"
              "the USB cable is a DATA cable (charge-only cables enumerate nothing),\n"
              "and that Windows shows it under Device Manager > Human Interface Devices.")
        return 3
    busy = [d for d in found if d.get("busy")]
    for d in found:
        print(f"  {d['path']}")
        if d.get("busy"):
            print("    HELD EXCLUSIVELY by another process - can't read its caps")
            continue
        print(f"    in={d['input_len']} out={d['output_len']} "
              f"usage={d['usage_page']:#06x}/{d['usage']:#04x} "
              f"product={d['product']!r} serial={d['serial']!r}")
    protocol = [d for d in found if d["input_len"] == 129]
    print(f"  -> {len(protocol)} collection(s) with 129-byte reports "
          f"(the protocol endpoint){' - none!' if not protocol else ''}")
    if busy:
        print("  -> the device is already open elsewhere; quit Cortex Control "
              "(Windows has no bridge mode) and run this again.")
    if args.list:
        return 0

    print("\n== opening exclusively ==")
    io = winhid.WinHIDTransport(vid=args.vid, pid=args.pid, seize=True)
    try:
        io.open()
    except RuntimeError as e:
        print(f"  FAILED: {e}")
        print("\nIf Cortex Control is running, quit it (direct mode needs the device\n"
              "to itself - Windows has no bridge mode) and run this again.")
        return 4
    print(f"  ok: {io.path}\n     exclusive={io.exclusive} "
          f"in={io._in_len} out={io._out_len}")
    io.close()

    print("\n== protocol round-trip ==")
    from qc_mcp.transport import QuadCortex
    qc = QuadCortex(bridge=False)
    try:
        qc.open()
        v = qc.read_state("Version")
        print(f"  CorOS {qc.firmware} | schema {qc.protocol_version} | "
              f"type {qc.device_type} | name {qc.custom_name!r}")
        print(f"  raw Version: zenos_git_hash={getattr(v, 'zenos_git_hash', '')!r} "
              f"app_fw_version={getattr(v, 'app_fw_version', '')!r}")
    except Exception as e:
        print(f"  FAILED: {type(e).__name__}: {e}")
        print("\nThe device enumerated and opened but didn't answer. That's a protocol\n"
              "or framing problem, not a Windows one - please open an issue with the\n"
              "output above.")
        return 5
    finally:
        qc.close()
    print("\nAll good - the MCP server will work on this machine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
