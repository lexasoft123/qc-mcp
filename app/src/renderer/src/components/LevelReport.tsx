import { Badge, Button } from '@singz/ui'
import type { BenchSlot, ReportRow } from '@shared/types'

/**
 * What every preset on the bench needs, in dB, side by side.
 *
 * Report first, write second — and the two are deliberately far apart. Reading a
 * table changes nothing; moving somebody's faders is a decision they make once
 * they have seen the numbers, and it can be undone.
 *
 * The correction reads as a bar diverging from a centre line rather than as a
 * signed number alone: which way and how far is the whole question, and a column
 * of "+9.8 / -2.6" makes you do that comparison in your head.
 */

const SCALE_DB = 12          // full width of one side of the bar

const fmt = (v: number | null | undefined, digits = 1): string =>
  v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}`

function Correction({ db }: { db: number | null | undefined }): React.JSX.Element {
  if (db === null || db === undefined) return <span className="rep-corr muted">—</span>
  const w = (Math.min(Math.abs(db), SCALE_DB) / SCALE_DB) * 50
  const left = db >= 0 ? 50 : 50 - w
  return (
    <span className="rep-corr">
      <span className="rep-track">
        <i className="rep-zero" />
        <i className={`rep-bar${Math.abs(db) >= 6 ? ' big' : ''}`}
           style={{ left: `${left}%`, width: `${w}%` }} />
      </span>
      <b>{fmt(db)}</b>
    </span>
  )
}

export function LevelReport({
  slots, rows, target, metric, busy, progress, applied, selected,
  onToggle, onMeasure, onApply, onRevert
}: {
  slots: BenchSlot[]
  /** Keyed by bench position; empty until measured. */
  rows: Record<number, ReportRow>
  target: number
  metric: 'lufs' | 'perceived'
  busy: boolean
  /** Name of the preset being measured right now, for the in-flight row. */
  progress: string | null
  /** Positions whose fader this session moved, so Revert has something to offer. */
  applied: number[]
  selected: number[]
  onToggle: (position: number) => void
  onMeasure: () => void
  onApply: () => void
  onRevert: () => void
}): React.JSX.Element | null {
  if (slots.length === 0) return null

  const measured = slots.filter((s) => rows[s.position]?.measured !== undefined)
  const vals = measured
    .map((s) => rows[s.position].measured)
    .filter((v): v is number => v !== null && v !== undefined)
  const spread = vals.length > 1 ? Math.max(...vals) - Math.min(...vals) : null
  const unit = metric === 'perceived' ? '' : ' LUFS'

  return (
    <div className="rep">
      <div className="rep-head">
        <h3>What each preset needs</h3>
        {spread !== null && (
          <Badge className={spread > 3 ? 'attn' : 'live'}>
            {spread.toFixed(1)} dB spread
          </Badge>
        )}
        <span className="fine">
          Measured with your riff. Reading this changes nothing on the device.
        </span>
        <span className="grow" />
        {applied.length > 0 && (
          <Button size="sm" onClick={onRevert}>
            Undo trims ({applied.length})
          </Button>
        )}
        <Button size="sm" disabled={busy} onClick={onMeasure}>
          {busy ? `Measuring ${progress ?? ''}…` : 'Measure all'}
        </Button>
        <Button size="sm" variant="primary"
                disabled={busy || selected.length === 0 || measured.length === 0}
                onClick={onApply}>
          Apply to {selected.length} on the device
        </Button>
      </div>

      <div className="rep-table">
        <div className="rep-row rep-th">
          <span />
          <span>Preset</span>
          <span className="right">{metric === 'perceived' ? 'N5 rel' : 'LUFS'}</span>
          <span className="right">True pk</span>
          <span className="right">Correction&nbsp;&nbsp;dB</span>
        </div>
        {slots.map((s) => {
          const r = rows[s.position]
          const on = selected.includes(s.position)
          const live = progress === s.name
          return (
            <div key={`${s.folderKey}:${s.position}`}
                 className={`rep-row${on ? '' : ' skip'}${live ? ' now' : ''}`}>
              <button type="button" className={`selbox${on ? ' on' : ''}`}
                      onClick={() => onToggle(s.position)}
                      aria-label={on ? `Exclude ${s.name}` : `Include ${s.name}`}>
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none"
                     stroke="currentColor" strokeWidth="3.4" strokeLinecap="round"
                     strokeLinejoin="round" aria-hidden>
                  <path d="M20 6 9 17l-5-5" />
                </svg>
              </button>
              <span className="rep-name">
                {s.name}
                {applied.includes(s.position) && <Badge className="attn">trimmed</Badge>}
                {r?.error && <Badge className="bad">failed</Badge>}
              </span>
              <span className="num">
                {live ? '…' : r?.measured !== undefined ? fmt(r.measured) : '—'}
              </span>
              <span className="num dim">{r ? fmt(r.true_peak) : '—'}</span>
              <Correction db={r?.correction_db} />
            </div>
          )
        })}
      </div>

      <p className="hint rep-foot">
        {measured.length === 0
          ? `Measure all plays the riff into each preset in turn and reports how far it is from ${target}${unit}. Nothing is written.`
          : `Apply moves the lane output fader on the device so you can hear them balanced. It does not touch the preset file — save a preset yourself to keep it, or undo the trims.`}
      </p>
    </div>
  )
}
