---
name: calibrate-param-mapping
description: Reverse how a device maps a stored/normalized parameter value to its human-readable display value (linear vs logarithmic/power taper) by capturing the official app setting known values, then curve-fitting. Use when your control writes/reads parameters but the on-device display value doesn't match what you intended (e.g. "220 ms" delay actually lands at 25 ms).
---

# Calibrate a device's parameter value taper

Devices store parameters normalized (often 0..1) and map to a display value via a
**taper** that is *not* always linear. If you assume linear, log-scaled params
(time/frequency/ratio) land far from the intended value. Calibrate by capturing the
real app setting known display values and fitting the curve.

## Method
1. **Set up a capture** with the target block's params (interposer verbose logging).
2. **Have a human dial known display values** in the app, in order, pausing between
   each (e.g. DELAY TIME → 100, 250, 500, 1000, 2000 ms). Capture the **settled**
   normalized value for each (cluster the drag stream by time gaps; take the last
   value before a >0.4 s gap).
3. **Fit.** Test:
   - **linear:** `nv = (display-min)/(max-min)`
   - **power taper:** `display = min + (max-min)*nv^k`, i.e. `nv = ((display-min)/(max-min))^(1/k)`
   Solve `k` from two points: `k = ln((d2-min)/(max-min)) / ln((d1-min)/(max-min)) ...`
   or just `p = ln(r2)/ln(nv2)` where `r=(d-min)/(max-min)`. Check it holds across all
   points.
4. **Check the specific parameter's metadata and wire values.** A declared JUCE
   XML skew uses `norm = fraction ** skew`; cabinet HPF/LPF use skew 0.3. The
   historical positive-range heuristic is a fallback and must be checked against
   observations. Some EQ gains with symbolic `MIN_EQ_DB` / `MAX_EQ_DB` bounds
   travel raw in dB; other controls with literal dB ranges can be normalized.
5. **Implement both directions** and use them everywhere (write *and* read), so you
   always speak real display units. See `catalog.to_norm` / `to_display`.

## Gotchas
- A round-trip that uses the *same wrong* conversion both ways will falsely "match" —
  validate against the device's own displayed number, not your own math.
- The catalog's stated "default" may be in display units and not equal
  `(default-min)/(max-min)` for tapered params — don't infer the taper from defaults.
- Capture logs can contain sensitive data — keep them out of version control.

Example: verify cabinet cutoff writes at known display values against the
hardware or Cortex Control as well as the protocol readback. A write and read
that use the same incorrect curve can agree with each other. The bundled GUI
automation is macOS-only; protocol reads and manual hardware checks work across
supported platforms.
