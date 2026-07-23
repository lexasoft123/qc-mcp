#!/usr/bin/env python3
"""Passively monitor the 'Quad Cortex' CoreMIDI input, logging every message
(especially SysEx) with size + hex. Read-only sniffer."""
import sys
import time
import rtmidi

DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0


def find_port(midi, name):
    for i, n in enumerate(midi.get_ports()):
        if name.lower() in n.lower():
            return i, n
    return None, None


def main():
    mi = rtmidi.MidiIn()
    idx, name = find_port(mi, "Quad Cortex")
    if idx is None:
        print("Quad Cortex MIDI IN not found"); sys.exit(1)
    mi.open_port(idx)
    mi.ignore_types(sysex=False, timing=False, active_sense=False)
    print(f"Listening on [{idx}] {name} for {DUR}s ...")

    counts = {"sysex": 0, "other": 0}
    total_bytes = 0
    t0 = time.time()
    while time.time() - t0 < DUR:
        msg = mi.get_message()
        if msg:
            data, dt = msg
            b = bytes(data)
            total_bytes += len(b)
            if b and b[0] == 0xF0:
                counts["sysex"] += 1
                print(f"[{time.time()-t0:6.2f}] SYSEX len={len(b)}: "
                      f"{b[:48].hex()}" + ("..." if len(b) > 48 else ""))
            else:
                counts["other"] += 1
                print(f"[{time.time()-t0:6.2f}] MIDI  len={len(b)}: {b.hex()}")
        else:
            time.sleep(0.001)
    print(f"\nDone. sysex={counts['sysex']} other={counts['other']} "
          f"total_bytes={total_bytes}")
    mi.close_port()


if __name__ == "__main__":
    main()
