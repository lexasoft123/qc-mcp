#!/usr/bin/env python3
"""Passively capture HID input reports (device->host) from the Quad Cortex.
Opens shared (no seize) so it can sniff alongside a running Cortex Control.
Dumps each report with timestamp, length, and hex for protocol reversing."""
import os, sys, time
os.environ.setdefault('DYLD_FALLBACK_LIBRARY_PATH', '/opt/homebrew/lib')
import hid

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from qc_mcp.backend import QC_PIDS, QC_VID  # noqa: E402

#: the model family, from the one list that defines it
VID, PIDS = QC_VID, tuple(QC_PIDS)
DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 12.0
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/qc_hid_cap.bin"


def main():
    last = None
    for pid in PIDS:
        try:
            d = hid.Device(VID, pid)
            break
        except Exception as exc:
            # Keep it: a device that is PRESENT but held by Cortex Control fails
            # here too, and reporting that as "not plugged in" sends people to
            # check the cable instead of quitting the app.
            last = exc
    else:
        raise SystemExit(f"no Quad Cortex on USB (looked for {VID:#06x}:"
                         + "/".join(f"{p:#06x}" for p in PIDS)
                         + f"); last error: {last}")
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
