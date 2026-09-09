import { useState } from 'react'
import { Badge, Button } from '@singz/ui'
import type { BenchSlot, ReportRow } from '@shared/types'
import { T, t } from '../i18n.js'
import { slotId } from '../bench.js'
import type { WrittenMap } from '../bench.js'

/**
 * What every preset on the bench needs, in dB, side by side — as a PROPOSAL.
 *
 * Nothing here happens on its own. Measuring produces a number per preset, the
 * number is shown, and it is editable: the measurement is a good opinion about
 * loudness and a bad one about taste, and a player who wants their lead two dB
 * above the rest should be able to say so before anything moves. Apply writes
 * exactly what is on the screen — re-deriving it at Apply time would make this
 * table a description of a change nobody was going to make.
 *
 * Then there are three states worth telling apart, and they look different:
 *
 *   proposed   the number the measurement suggested, or the one you typed
 *   applied    written to the device, audible, and NOT in the preset file —
 *              a yellow dot, because it disappears the moment the preset is
 *              reloaded and there is nothing else on screen that would say so
 *   saved      in the preset, permanent, dot gone
 *
 * The correction reads as a bar diverging from a centre line rather than as a
 * signed number alone: which way and how far is the whole question, and a
 * column of "+9.8 / -2.6" makes you do that comparison in your head.
 */

const SCALE_DB = 12          // full width of one side of the bar
const STEP_DB = 0.5

const fmt = (v: number | null | undefined, digits = 1): string =>
  v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}`

function Correction({ db, edited }: { db: number | null | undefined; edited: boolean }): React.JSX.Element {
  if (db === null || db === undefined) return <span className="rep-corr muted">—</span>
  const w = (Math.min(Math.abs(db), SCALE_DB) / SCALE_DB) * 50
  const left = db >= 0 ? 50 : 50 - w
  return (
    <span className="rep-corr">
      <span className="rep-track">
        <i className="rep-zero" />
        <i className={`rep-bar${Math.abs(db) >= 6 ? ' big' : ''}${edited ? ' edited' : ''}`}
           style={{ left: `${left}%`, width: `${w}%` }} />
      </span>
    </span>
  )
}

/**
 * The proposal, as a thing you can argue with.
 *
 * Deliberately NOT `<input type="number">`: the browser renders those in the
 * viewer's locale, so a decimal comma turned up in a table where every other
 * number — LUFS, true peak, the target — is written with a point, and in that
 * locale typing a point is then refused. A text field parsed here shows one
 * kind of number and accepts both.
 */
function Proposal({ db, edited, disabled, onChange, onNudge, onReset }: {
  db: number | null | undefined
  edited: boolean
  disabled: boolean
  /** An absolute value — what was typed. */
  onChange: (db: number) => void
  /**
   * A RELATIVE step. The buttons cannot send an absolute one: `db` is a prop,
   * so two clicks in the same tick both compute from the value before either of
   * them, and one of the two is silently lost. Sending the step and letting the
   * owner apply it to whatever it currently holds is the only version that
   * survives somebody leaning on the button.
   */
  onNudge: (by: number) => void
  onReset: () => void
}): React.JSX.Element {
  const [draft, setDraft] = useState<string | null>(null)
  if (db === null || db === undefined) return <span className="prop muted">—</span>
  const nudge = (by: number): void => {
    setDraft(null)
    onNudge(by)
  }
  const commit = (text: string): void => {
    const v = Number(text.trim().replace(',', '.').replace('−', '-'))
    setDraft(null)
    if (Number.isFinite(v)) onChange(Math.round(v * 10) / 10)
  }
  return (
    <span className={`prop${edited ? ' edited' : ''}`}>
      <button type="button" className="nudge" disabled={disabled}
              onClick={() => nudge(-STEP_DB)} aria-label={t('rep.quieter')}>−</button>
      <input
        type="text" inputMode="decimal" spellCheck={false} disabled={disabled}
        value={draft ?? db.toFixed(1)}
        aria-label={t('rep.propAria')}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={(e) => commit(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') { commit((e.target as HTMLInputElement).value); return }
          if (e.key === 'Escape') { setDraft(null); return }
          // arrows nudge, the way a fader does
          if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
            e.preventDefault()
            nudge(e.key === 'ArrowUp' ? STEP_DB : -STEP_DB)
          }
        }}
      />
      <button type="button" className="nudge" disabled={disabled}
              onClick={() => nudge(STEP_DB)} aria-label={t('rep.louder')}>+</button>
      {edited && (
        <button type="button" className="undo" disabled={disabled} onClick={onReset}
                title={t('rep.resetHint')}>↺</button>
      )}
    </span>
  )
}

export function LevelReport({
  slots, rows, target, metric, busy, progress, written, proposals, selected,
  onToggle, onPropose, onNudge, onResetProposal, onUndoOne,
  onMeasure, onApply, onSave, onRevert
}: {
  slots: BenchSlot[]
  /** Keyed by slot id; empty until measured. */
  rows: Record<string, ReportRow>
  target: number
  metric: 'lufs' | 'perceived'
  busy: boolean
  /** Name of the preset being measured right now, for the in-flight row. */
  progress: string | null
  /** Slot id -> what this session wrote to the device, and whether it was saved. */
  written: WrittenMap
  /** Slot id -> the correction that will be written, measured or edited. */
  proposals: Record<string, number>
  selected: string[]
  onToggle: (id: string) => void
  onPropose: (id: string, db: number) => void
  onNudge: (id: string, by: number) => void
  onResetProposal: (id: string) => void
  onUndoOne: (id: string) => void
  onMeasure: () => void
  onApply: () => void
  onSave: () => void
  onRevert: () => void
}): React.JSX.Element | null {
  if (slots.length === 0) return null

  const ids = slots.map(slotId)
  const measured = slots.filter((s) => rows[slotId(s)]?.measured !== undefined)
  const vals = measured
    .map((s) => rows[slotId(s)].measured)
    .filter((v): v is number => v !== null && v !== undefined)
  const spread = vals.length > 1 ? Math.max(...vals) - Math.min(...vals) : null
  const unit = metric === 'perceived' ? '' : ' LUFS'
  const unsaved = ids.filter((id) => written[id] !== undefined && !written[id].saved)
  const applied = ids.filter((id) => written[id]?.source === 'apply')

  const proposalFor = (s: BenchSlot): number | null | undefined =>
    proposals[slotId(s)] ?? rows[slotId(s)]?.correction_db
  const isEdited = (s: BenchSlot): boolean => {
    const p = proposals[slotId(s)]
    const m = rows[slotId(s)]?.correction_db
    return p !== undefined && m !== undefined && m !== null && Math.abs(p - m) > 0.001
  }

  return (
    <div className="rep">
      <div className="rep-head">
        <h3>{t('rep.title')}</h3>
        {spread !== null && (
          <Badge className={spread > 3 ? 'attn' : 'live'}>
            {t('rep.spread', { db: spread.toFixed(1) })}
          </Badge>
        )}
        {unsaved.length > 0 && (
          <Badge className="attn"><i className="dot-unsaved" />{t('rep.unsaved', { n: String(unsaved.length) })}</Badge>
        )}
        <span className="fine">{t('rep.lede')}</span>
        <span className="grow" />
        {applied.length > 0 && (
          <Button size="sm" onClick={onRevert}>
            {t('rep.undoTrims', { n: String(applied.length) })}
          </Button>
        )}
        <Button size="sm" disabled={busy} onClick={onMeasure}
                title={t('rep.measureHint')}>
          {busy ? t('rep.measuring', { name: progress ?? '' }) : t('rep.measureAll')} <kbd>M</kbd>
        </Button>
        <Button size="sm" variant="primary"
                disabled={busy || selected.length === 0 || measured.length === 0}
                onClick={onApply}
                title={t('rep.applyHint')}>
          {t('rep.apply', { n: String(selected.length) })}
        </Button>
        {unsaved.length > 0 && (
          <Button size="sm" variant="primary" disabled={busy} onClick={onSave}>
            {t('rep.saveN', { n: String(unsaved.length) })}
          </Button>
        )}
      </div>

      <div className="rep-table">
        <div className="rep-row rep-th">
          <span />
          <span>{t('rep.col.preset')}</span>
          <span className="right">{metric === 'perceived' ? 'N5 rel' : 'LUFS'}</span>
          <span className="right">
            <abbr title={t('rep.col.truePeakHint')}>{t('rep.col.truePeak')}</abbr>
          </span>
          <span className="right">{t('rep.col.correction')}</span>
          <span className="right">{t('rep.col.proposed')}</span>
        </div>
        {slots.map((s) => {
          const id = slotId(s)
          const r = rows[id]
          const on = selected.includes(id)
          const live = progress === s.name
          const w = written[id]
          const isUnsaved = w !== undefined && !w.saved
          return (
            <div key={id}
                 className={`rep-row${on ? '' : ' skip'}${live ? ' now' : ''}${isUnsaved ? ' unsaved' : ''}`}>
              <button type="button" className={`selbox${on ? ' on' : ''}`}
                      onClick={() => onToggle(id)}
                      aria-label={on ? t('rep.exclude', { name: s.name }) : t('rep.include', { name: s.name })}>
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none"
                     stroke="currentColor" strokeWidth="3.4" strokeLinecap="round"
                     strokeLinejoin="round" aria-hidden>
                  <path d="M20 6 9 17l-5-5" />
                </svg>
              </button>
              <span className="rep-name">
                {/* The yellow dot is the only thing on screen that says this
                    trim lives in the device and not in the preset file. */}
                {isUnsaved && (
                  <i className="dot-unsaved"
                     title={t('rep.dotHint', { db: fmt(w.db) })} />
                )}
                {s.name}
                {w?.saved && <Badge className="live">{t('rep.saved')}</Badge>}
                {/* Only a trim with a known number can be put back. */}
                {isUnsaved && w.db !== null && (
                  <button type="button" className="rowundo" title={t('rep.undoOneHint')}
                          onClick={() => onUndoOne(id)}>{t('rep.undoOne')}</button>
                )}
              </span>
              <span className="num">
                {live ? '…' : r?.measured !== undefined ? fmt(r.measured) : '—'}
              </span>
              <span className="num dim">{r ? fmt(r.true_peak) : '—'}</span>
              <Correction db={proposalFor(s)} edited={isEdited(s)} />
              <Proposal
                db={proposalFor(s)}
                edited={isEdited(s)}
                disabled={busy || !on}
                onChange={(db) => onPropose(id, db)}
                onNudge={(by) => onNudge(id, by)}
                onReset={() => onResetProposal(id)}
              />
              {/* On the row that produced it. One shared error string meant a
                  run that failed three times showed the last one. */}
              {r?.error && <span className="rep-err">{r.error}</span>}
            </div>
          )
        })}
      </div>

      <p className="hint rep-foot">
        {measured.length === 0
          ? t('rep.foot.unmeasured', { target: `${target}${unit}` })
          : unsaved.length > 0
            ? <T k="rep.foot.unsaved" />
            : t('rep.foot.applied')}
      </p>
    </div>
  )
}
