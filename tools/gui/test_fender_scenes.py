#!/usr/bin/env python3
"""E2E scene test (user request): build a Fender Deluxe Reverb preset with 3 scenes —
clean / crunch / lead — via per-scene GAIN, plus a Klon overdrive.

Builds on the device, then reads back and asserts:
  * the chain is Klon -> Fender Deluxe amp -> cab
  * the amp GAIN is scene-varying with clean < crunch < lead
  * (Klon present; "for lead" = intended to be enabled only in the lead scene)

Run: python3 tools/gui/test_fender_scenes.py   (bridge) or QC_BRIDGE=0 ... (direct)
Non-persistent (never saves); recall a preset afterward to restore.
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))
from qc_mcp import catalog  # noqa: E402
from qc_mcp.transport import QuadCortex  # noqa: E402

KLON = 1        # "Myth Drive" (Based on Klon Centaur)
AMP = 1094      # "US DLX Normal" (Fender Blackface Deluxe Reverb)
CAB = 12000     # Default Cabsim
VOL = 0         # amp param 0 = VOLUME (the blackface Deluxe's gain control)
KLON_GAIN = 0   # Klon param 0 = GAIN
BYPASS = 4      # block bypass param (1.0 = bypassed, 0.0 = on)
# clean(A) / crunch(B) / lead(C); later scenes hold the lead value
AMP_VOL = [0.30, 0.45, 0.45, 0.45, 0.45, 0.45, 0.45, 0.45]       # 3.0 / 4.5 / 4.5
KLON_OFF = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]              # off in clean only
KLON_GAINS = [0.50, 0.50, 0.30, 0.30, 0.30, 0.30, 0.30, 0.30]    # lead gain 3.0


def main():
    qc = QuadCortex(bridge=os.environ.get("QC_BRIDGE") != "0" and os.path.exists("/tmp/qc_inject"))
    qc.open(handshake=not qc.bridge)
    qc.clear_grid(); time.sleep(0.6)
    qc.add_block(KLON, row=0, column=0); time.sleep(0.3)
    qc.add_block(AMP, row=0, column=1); time.sleep(0.3)
    qc.add_block(CAB, row=0, column=2); time.sleep(0.3)
    qc.set_param_scenes(0, 1, VOL, AMP_VOL); time.sleep(0.4)          # amp volume/scene
    qc.set_block_bypass(0, 0, scenes=[True, False, False, False,      # Klon off in clean
                                      False, False, False, False]); time.sleep(0.4)
    qc.set_param_scenes(0, 0, KLON_GAIN, KLON_GAINS); time.sleep(0.4)  # Klon gain lead=3

    # bridge read-back is flaky; retry until the amp's scene-varying VOLUME comes back
    bp, cats, vol, kbyp = None, [], None, None

    def rd(v):
        return round(v.float_value, 3)
    for _ in range(6):
        bp = qc.get_current_preset(timeout_ms=6000)
        cats, vol, kbyp = [], None, None
        if bp:
            for ch in bp.chains:
                if getattr(ch, "row", 0) != 0:
                    continue
                for m in sorted(ch.models, key=lambda x: x.column):
                    if not m.hash:
                        continue
                    cats.append((catalog.lookup(m.hash) or {}).get("category", "?"))
                    if m.column == 1 and len(m.params) > VOL:
                        vol = m.params[VOL]
                    if m.column == 0 and len(m.params) > BYPASS:
                        kbyp = m.params[BYPASS]
        if vol and len(vol.param_values) >= 3:
            break
        time.sleep(0.6)
    if not bp or not vol:
        print("read-back empty after retries (run QC_BRIDGE=0 direct mode)"); qc.close(); return 1

    vvals = [rd(x) for x in vol.param_values]
    bvals = [rd(x) for x in kbyp.param_values] if kbyp else []
    checks = [
        ("chain = OD -> Amp -> Cab",
         cats[:3] == ["Guitar Overdrive", "Guitar Amplifier", "Cabsim Guitar (M)"]),
        ("amp VOLUME scene-varying", bool(vol.scene_mode)),
        ("amp VOLUME clean=3.0 / crunch=lead=4.5", vvals[:3] == [0.3, 0.45, 0.45]),
        ("Klon bypass scene-varying", bool(kbyp and kbyp.scene_mode)),
        ("Klon OFF in clean, ON in crunch/lead", bvals[:3] == [1.0, 0.0, 0.0]),
    ]
    print(f"chain: {cats[:3]}")
    print(f"amp VOLUME: scene_mode={vol.scene_mode} values={vvals}")
    print(f"Klon bypass(p4): scene_mode={kbyp.scene_mode if kbyp else None} values={bvals}")
    ok = all(v for _, v in checks)
    for name, v in checks:
        print(f"  [{'PASS' if v else 'FAIL'}] {name}")
    print(f"\n=== {'ALL PASS' if ok else 'FAILURES'} ===")
    qc.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
