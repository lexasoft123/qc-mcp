import type { MeterOutput } from '@shared/types'

/** The scale the QC's own OUT LEVEL readout uses: -40 dB floor, +12 ceiling. */
export const FLOOR = -40
export const CEIL = 12
const TICKS = [12, 6, 0, -6, -12, -20, -30, -40]

const pct = (db: number): number =>
  Math.max(0, Math.min(1, (db - FLOOR) / (CEIL - FLOOR))) * 100

/**
 * IOMeter reports **linear amplitude**, 0..1 — not dB. Straight to the bar it
 * would put everything below -6 dBFS in the bottom tenth of the meter, which is
 * where guitar playing actually lives.
 */
export const toDb = (level: number): number =>
  level <= 0 ? FLOOR : Math.max(FLOOR, 20 * Math.log10(level))

/**
 * A stereo output meter with peak hold.
 *
 * `outputs` is whatever the device last streamed. Frames only arrive while the
 * rig is passing audio (and one bridge session sent none at all), so "no
 * reading" is a resting state the meter states plainly *under* the bars — an
 * overlaid label would sit across the dB scale.
 */
export function Meter({
  outputs, peak, live
}: {
  outputs: Record<string, MeterOutput> | null
  /** Highest level seen for this preset, in dB. */
  peak: number | null
  /** This is the preset currently loaded on the device. */
  live: boolean
}): React.JSX.Element {
  const pair = channels(outputs)
  const heard = pair.length > 0

  return (
    <div className="lvl-meter-wrap">
      {/* height comes from CSS so the meter grows with the window */}
      <div className={heard ? 'lvl-meter' : 'lvl-meter idle'}>
        <div className="lvl-meter-bars">
          {(heard ? pair : [FLOOR, FLOOR]).map((db, i) => (
            <div className="lvl-meter-bar" key={i}>
              <div
                className={db > -0.5 ? 'lvl-meter-fill hot' : 'lvl-meter-fill'}
                style={{ height: `${pct(db)}%` }}
              />
              {peak !== null && (
                <div className="lvl-meter-peak" style={{ bottom: `${pct(peak)}%` }} />
              )}
            </div>
          ))}
        </div>
        <div className="lvl-meter-scale" aria-hidden>
          {TICKS.map((t) => (
            <span key={t} style={{ bottom: `${pct(t)}%` }}>{t > 0 ? `+${t}` : t}</span>
          ))}
        </div>
      </div>
      <div className="lvl-meter-note">
        {heard
          ? peak !== null ? `peak ${peak.toFixed(1)} dB` : 'live'
          : live ? 'play to read' : 'not loaded'}
      </div>
    </div>
  )
}

/**
 * The two channels worth showing. The mains (XLR 1/2) are the leveling
 * reference; a preset routed only to 3/4 or headphones falls back to those, so
 * the meter follows the signal rather than insisting on one pair of jacks.
 */
function channels(outputs: Record<string, MeterOutput> | null): number[] {
  if (!outputs) return []
  for (const [l, r] of [['xlr_1', 'xlr_2'], ['out_3', 'out_4'], ['hp_l', 'hp_r']]) {
    const a = outputs[l]
    const b = outputs[r]
    if (a || b) return [toDb(a?.level ?? 0), toDb(b?.level ?? 0)]
  }
  return []
}

/** The loudest of a frame's outputs, for the per-preset peak hold. */
export function loudest(outputs: Record<string, MeterOutput>): number | null {
  const all = channels(outputs)
  return all.length ? Math.max(...all) : null
}
