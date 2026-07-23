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
4. **Find the rule** that decides which params use which taper. Here every freq/time/
   ratio param fit the *same* exponent **k ≈ 1.667** (`nv = r^0.6`), while dB/%/level
   params were linear. The classifier that matched all data: **`min > 0` and
   `max/min ≥ 5` → power taper; else linear.**
5. **Implement both directions** and use them everywhere (write *and* read), so you
   always speak real display units. See `catalog.to_norm` / `to_display`.

## Gotchas
- A round-trip that uses the *same wrong* conversion both ways will falsely "match" —
  validate against the device's own displayed number, not your own math.
- The catalog's stated "default" may be in display units and not equal
  `(default-min)/(max-min)` for tapered params — don't infer the taper from defaults.
- Capture logs can contain sensitive data — keep them out of version control.

Worked example: QC uses `nv = ((display-min)/(max-min))^0.6` for freq/time/ratio;
linear for dB/%. A naive-linear "220 ms" delay was actually ~25 ms. See PROTOCOL.md §5.
