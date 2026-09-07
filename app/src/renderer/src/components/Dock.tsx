import type { MeterOutput } from '@shared/types'
import { FLOOR, CEIL, toDb } from './Meter.js'

/**
 * Every output at a glance, docked under the bench.
 *
 * The per-preset meters answer "how loud is this one". This answers the other
 * question — is anything reaching the outputs at all, and is any of it hitting
 * the limiter — which is the one you need when a preset sounds wrong and you do
 * not yet know whether the fault is the preset or the output stage.
 *
 * The device sends LINEAR amplitude; `toDb` puts it on the -40..+12 scale the
 * QC's own OUT LEVEL readout uses.
 */

const PORTS: [string, string][] = [
  ['xlr_1', 'XLR 1'], ['xlr_2', 'XLR 2'],
  ['out_3', 'Out 3'], ['out_4', 'Out 4'],
  ['hp_l', 'HP L'], ['hp_r', 'HP R']
]

/** The headphones carry ONE limiter flag for both channels, not one each. */
const HP = new Set(['hp_l', 'hp_r'])

const pct = (db: number): number =>
  Math.max(0, Math.min(1, (db - FLOOR) / (CEIL - FLOOR))) * 100

export function Dock({
  outputs, hpLimit
}: {
  /** Whatever the device last streamed, or null when nothing is arriving. */
  outputs: Record<string, MeterOutput> | null
  /** The single shared headphone limiter flag. */
  hpLimit?: boolean
}): React.JSX.Element {
  const heard = Boolean(outputs && Object.keys(outputs).length)
  const limiting = PORTS.some(([k]) => !HP.has(k) && (outputs?.[k]?.limit ?? 0) >= 0.5)

  return (
    <div className={`dock${limiting ? ' bad' : ''}`}>
      <span className="eyebrow">Outputs</span>
      {PORTS.map(([key, label]) => {
        const o = outputs?.[key]
        const db = o ? toDb(o.level) : null
        const lim = !HP.has(key) && (o?.limit ?? 0) >= 0.5
        return (
          <span key={key} className={`dp${lim ? ' lim' : ''}`}>
            <i>{label}</i>
            <span className="lvl">
              <span className="fill" style={{ width: `${db === null ? 0 : pct(db)}%` }} />
            </span>
            <b>{db === null ? '—' : `${db > 0 ? '+' : '−'}${Math.abs(db).toFixed(1)}`}</b>
          </span>
        )
      })}
      <span className="grow" />
      {/* "No reading" is a resting state: the QC sends frames only while audio is
          actually moving, so silence here is not a fault. */}
      <span className={`dock-state${limiting ? ' bad' : heard ? ' ok' : ''}`}>
        {limiting
          ? 'limiting'
          : hpLimit
            ? 'headphone limiter'
            : heard
              ? 'limiters clear'
              : 'no reading — play something'}
      </span>
    </div>
  )
}
