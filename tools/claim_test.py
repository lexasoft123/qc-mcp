#!/usr/bin/env python3
"""Test claiming interface 4 (bulk 0x04 OUT / 0x84 IN) and passively reading
any frames the QC pushes. Read-only: sends nothing."""
import sys
import usb.core
import usb.util
import usb.backend.libusb1

VID, PID = 0x152A, 0x880A
IFACE, EP_OUT, EP_IN = 4, 0x04, 0x84
BACKEND = usb.backend.libusb1.get_backend(
    find_library=lambda x: "/opt/homebrew/lib/libusb-1.0.dylib"
)


def main():
    dev = usb.core.find(idVendor=VID, idProduct=PID, backend=BACKEND)
    if dev is None:
        print("QC not found"); sys.exit(1)

    # Do NOT set_configuration (would reset); config is already active.
    try:
        if dev.is_kernel_driver_active(IFACE):
            print(f"Interface {IFACE}: kernel driver active -> detaching")
            dev.detach_kernel_driver(IFACE)
        else:
            print(f"Interface {IFACE}: no kernel driver attached")
    except NotImplementedError:
        print("is_kernel_driver_active not implemented on this backend (macOS)")
    except usb.core.USBError as e:
        print(f"kernel driver check error: {e}")

    try:
        usb.util.claim_interface(dev, IFACE)
        print(f"CLAIMED interface {IFACE} OK")
    except usb.core.USBError as e:
        print(f"CLAIM FAILED: {e}")
        sys.exit(2)

    print("Passively reading EP 0x84 for ~3s (QC may push status frames)...")
    got = 0
    for _ in range(6):
        try:
            data = dev.read(EP_IN, 512, timeout=500)
            got += 1
            b = bytes(data)
            print(f"  RX {len(b)} bytes: {b[:64].hex()}"
                  + ("..." if len(b) > 64 else ""))
        except usb.core.USBError as e:
            if e.errno in (60, 110) or "timeout" in str(e).lower():
                print("  (read timeout - no unsolicited data)")
            else:
                print(f"  read error: {e}")
                break
    print(f"Done. Frames received: {got}")
    usb.util.release_interface(dev, IFACE)


if __name__ == "__main__":
    main()
