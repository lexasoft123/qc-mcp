#!/usr/bin/env python3
"""Passively capture HID input reports (device->host) from the Quad Cortex.
Opens shared (no seize) so it can sniff alongside a running Cortex Control.
Dumps each report with timestamp, length, and hex for protocol reversing."""
import os, sys, time
os.environ.setdefault('DYLD_FALLBACK_LIBRARY_PATH', '/opt/homebrew/lib')
import hid

VID, PID = 0x152A, 0x880A
DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/qc_hid_cap.bin"


def main():
    d = hid.Device(VID, PID)
    try:
        print("manufacturer:", d.manufacturer)
        print("product:", d.product)
    except Exception as e:
        print("info err", e)
    print(f"Capturing device->host reports for {DUR}s ...")
    t0 = time.time()
    n = 0
    total = 0
    raw = open(OUT, "wb")
    while time.time() - t0 < DUR:
        data = d.read(4096, 50)  # size, timeout_ms
        if data:
            b = bytes(data)
            n += 1
            total += len(b)
            ts = time.time() - t0
            # length-prefixed framing in the raw dump for later reassembly
            raw.write(len(b).to_bytes(2, "little") + b)
            if n <= 40:
                print(f"[{ts:6.3f}] len={len(b):4} {b[:56].hex()}"
                      + ("..." if len(b) > 56 else ""))
        else:
            time.sleep(0.001)
    raw.close()
    print(f"\nDone. reports={n} total_bytes={total} saved={OUT}")
    d.close()


if __name__ == "__main__":
    main()
