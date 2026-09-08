#!/usr/bin/env python3
"""Record a riff, then level the current preset by it. One command, no GUI.

    .venv/bin/python tools/level_now.py                 # measure and suggest only
    .venv/bin/python tools/level_now.py --write         # actually trim the preset
    .venv/bin/python tools/level_now.py --target -16    # a different target

Needs the audio extra (`pip install -e '.[audio]'`), the Quad Cortex on USB, and
Cortex Control QUIT (this opens the device directly).

What it does, in order:

  1. Arms the recorder on the dry DI (host USB 1/2) and waits for your first note.
     Recording stops on its own when you stop playing.
  2. Finds which lane the preset takes its input on and which one it leaves by —
     usually not the same lane.
  3. Plays the riff back into The Grid over USB 5/6 and measures what comes out.
  4. Reports the correction, and with --write trims the lane's output volume until
     the preset lands on target.

While it measures, the output is tapped to USB and is NOT going to the XLRs, so
the room stays quiet. Routing and the fader are restored whatever happens.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", type=float, default=-18.0, help="LUFS target (default -18)")
    ap.add_argument("--write", action="store_true", help="trim the preset, not just report")
    ap.add_argument("--tolerance", type=float, default=0.5, help="LU (default 0.5)")
    ap.add_argument("--threshold", type=float, default=-40.0,
                    help="dBFS that counts as the first note (default -40)")
    ap.add_argument("--reuse", action="store_true",
                    help="use the riff already recorded instead of recording a new one")
    args = ap.parse_args()

    try:
        from qc_mcp import audio_io, autolevel, loudness            # noqa: F401
        from qc_mcp import leveling as bench_mod
        from qc_mcp.transport import QuadCortex
    except Exception as exc:
        print("!! %s" % exc)
        print("   install the audio extra:  pip install -e '.[audio]'")
        return 1

    # ── the device ───────────────────────────────────────────────────────────
    try:
        dev = audio_io.find_device()
    except Exception as exc:
        print("!! %s" % exc)
        return 1
    print("Quad Cortex: %d in / %d out @ %d Hz" % (dev["inputs"], dev["outputs"], 48000))

    try:
        qc = QuadCortex(bridge=False).open(handshake=True)
    except Exception as exc:
        print("!! could not open the device: %s" % exc)
        print("   Quit Cortex Control first — direct mode needs exclusive access.")
        return 1
    bench = bench_mod.Bench(qc)
    state = bench.preset_state() or {}
    in_row, out_row = autolevel.measurement_rows(qc)
    print("preset:      %s" % (state.get("name") or "?"))
    print("signal:      in on row %d, out on row %d" % (in_row, out_row))

    # ── the riff ─────────────────────────────────────────────────────────────
    path = audio_io.DEFAULT_SAMPLE_PATH
    if args.reuse and os.path.exists(path):
        print("riff:        reusing %s" % path)
    else:
        s = audio_io.sampler()
        s.arm(threshold_dbfs=args.threshold)
        print("\n>> PLAY. Recording starts on your first note and stops when you stop.")
        print("   (Ctrl-C to give up.)\n")
        last = ""
        try:
            while s.state in (s.ARMED, s.RECORDING):
                st = s.status()
                line = ("   %-9s %5.1f s   in %s dBFS        "
                        % (st["state"], st["seconds_recorded"],
                           "--" if st["input_dbfs"] is None else "%6.1f" % st["input_dbfs"]))
                if line != last:
                    sys.stdout.write("\r" + line); sys.stdout.flush(); last = line
                time.sleep(0.15)
        except KeyboardInterrupt:
            s.discard(); print("\n   cancelled"); return 1
        res = s.stop()
        print()
        if res.get("error"):
            print("!! %s" % res["error"]); return 1
        print("riff:        %.2f s, peak %s dBFS, %s LUFS"
              % (res["duration_s"], res["peak_dbfs"], res["lufs"]))
        path = res["path"]

    # ── measure, and optionally trim ─────────────────────────────────────────
    print("\nmeasuring (silent in the room — the output is tapped to USB)")
    knob = autolevel.LaneVolumeTrim(bench, out_row)
    before = knob.read()
    steps: list = []
    out = autolevel.level_current(
        qc, target=args.target, tolerance=args.tolerance, max_iterations=3,
        row=out_row, in_row=in_row, out_row=out_row, di_path=path,
        trim=knob, dry_run=not args.write, on_step=steps.append)

    if out.get("error"):
        print("!! %s" % out["error"]); return 1

    for st in out["iterations"]:
        print("   pass %d: %6.2f LUFS   off by %+5.2f dB%s"
              % (st["n"], st["measured"], st["delta_db"] or 0.0,
                 "   [held: true peak]" if st.get("limited_by") else ""))

    if not args.write:
        print("\nSUGGESTION: %+.1f dB on row %d (currently %+.2f dB)."
              % (out["suggested_db"], out_row, before))
        print("Nothing was written. Re-run with --write to apply it.")
    else:
        print("\n%s  final %+.2f dB on row %d (was %+.2f)"
              % ("converged." if out.get("converged") else "did not settle —",
                 out.get("final_trim_db") or before, out_row, before))
        print("Not saved to the preset. Save it on the device, or in Patchbay's")
        print("Leveling view, if you want to keep it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
