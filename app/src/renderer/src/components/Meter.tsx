import type { MeterOutput } from '@shared/types'

/**
 * The output meter's arithmetic, shared by the dock and the bench rows.
 *
 * The per-card meter that used to live here is gone — the input rail and the
 * rows' inline meters draw the levels now — but everything that draws one
 * still needs the same scale and the same reading of the device's frames.
 */

/** The scale the QC's own OUT LEVEL readout uses: -40 dB floor, +12 ceiling. */
export const FLOOR = -40
export const CEIL = 12

/**
 * IOMeter reports **linear amplitude**, 0..1 — not dB. Straight to the bar it
 * would put everything below -6 dBFS in the bottom tenth of the meter, which is
 * where guitar playing actually lives.
 */
export const toDb = (level: number): number =>
  level <= 0 ? FLOOR : Math.max(FLOOR, 20 * Math.log10(level))

/**
 * The two channels worth showing. The mains (XLR 1/2) are the leveling
 * reference; a preset routed only to 3/4 or headphones falls back to those, so
 * the meter follows the signal rather than insisting on one pair of jacks.
 */
export function channels(outputs: Record<string, MeterOutput> | null): number[] {
  if (!outputs) return []
  for (const [l, r] of [['xlr_1', 'xlr_2'], ['out_3', 'out_4'], ['hp_l', 'hp_r']]) {
    const a = outputs[l]
    const b = outputs[r]
    if (a || b) return [toDb(a?.level ?? 0), toDb(b?.level ?? 0)]
  }
  return []
}

/** The loudest of a frame's outputs, for the peak hold. */
export function loudest(outputs: Record<string, MeterOutput>): number | null {
  const all = channels(outputs)
  return all.length ? Math.max(...all) : null
}
