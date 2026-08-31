#!/usr/bin/env python3
"""Probe the Quad Cortex USB device: enumerate configs/interfaces/endpoints,
identify the vendor control interface and its bulk endpoints."""
import usb.core
import usb.util
import usb.backend.libusb1

# 0x880A Quad Cortex, 0x892F Quad Cortex Mini - same protocol
VID, PIDS = 0x152A, (0x880A, 0x892F)
BACKEND = usb.backend.libusb1.get_backend(
    find_library=lambda x: "/opt/homebrew/lib/libusb-1.0.dylib"
)

CLASS_NAMES = {
    0x01: "Audio", 0x02: "CDC-Control", 0x03: "HID", 0x08: "Mass-Storage",
    0x0A: "CDC-Data", 0x0E: "Video", 0xFE: "App-Specific", 0xFF: "Vendor",
}


def ep_dir(addr):
    return "IN" if addr & 0x80 else "OUT"


def ep_type(attr):
    return {0: "control", 1: "iso", 2: "bulk", 3: "interrupt"}[attr & 0x3]


def main():
    dev = next((d for d in (usb.core.find(idVendor=VID, idProduct=pid, backend=BACKEND)
                      for pid in PIDS) if d), None)
    if dev is None:
        print("Quad Cortex NOT found on USB.")
        return
    print(f"Found Quad Cortex  bus={dev.bus} addr={dev.address}")
    print(f"  bDeviceClass={dev.bDeviceClass} subclass={dev.bDeviceSubClass} "
          f"protocol={dev.bDeviceProtocol}")
    try:
        print(f"  Manufacturer={usb.util.get_string(dev, dev.iManufacturer)!r}  "
              f"Product={usb.util.get_string(dev, dev.iProduct)!r}")
    except Exception as e:
        print(f"  (string descriptors unavailable: {e})")

    for cfg in dev:
        print(f"\nConfiguration {cfg.bConfigurationValue}: "
              f"{cfg.bNumInterfaces} interfaces")
        for intf in cfg:
            cls = intf.bInterfaceClass
            name = CLASS_NAMES.get(cls, f"0x{cls:02X}")
            print(f"  Interface {intf.bInterfaceNumber} alt {intf.bAlternateSetting}: "
                  f"class={name} sub={intf.bInterfaceSubClass} proto={intf.bInterfaceProtocol} "
                  f"eps={intf.bNumEndpoints}")
            for ep in intf:
                print(f"      EP 0x{ep.bEndpointAddress:02X} {ep_dir(ep.bEndpointAddress):3} "
                      f"{ep_type(ep.bmAttributes):9} maxpkt={ep.wMaxPacketSize}")


if __name__ == "__main__":
    main()
