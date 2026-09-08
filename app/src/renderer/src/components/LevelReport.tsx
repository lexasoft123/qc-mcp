import { useState } from 'react'
import { Badge, Button } from '@singz/ui'
import type { BenchSlot, ReportRow } from '@shared/types'

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
              onClick={() => nudge(-STEP_DB)} aria-label="Half a dB quieter">−</button>
      <input
        type="text" inputMode="decimal" spellCheck={false} disabled={disabled}
        value={draft ?? db.toFixed(1)}
        aria-label="Proposed correction in dB"
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
              onClick={() => nudge(STEP_DB)} aria-label="Half a dB louder">+</button>
      {edited && (
        <button type="button" className="undo" disabled={disabled} onClick={onReset}
                title="Back to the measured suggestion">↺</button>
      )}
    </span>
  )
}

export function LevelReport({
  slots, rows, target, metric, busy, progress, applied, saved, proposals, selected,
  onToggle, onPropose, onNudge, onResetProposal, onMeasure, onApply, onSave, onRevert
}: {
  slots: BenchSlot[]
  /** Keyed by bench position; empty until measured. */
  rows: Record<number, ReportRow>
  target: number
  metric: 'lufs' | 'perceived'
  busy: boolean
  /** Name of the preset being measured right now, for the in-flight row. */
  progress: string | null
  /** Position -> dB actually written to the device this session. */
  applied: Record<number, number>
  /** Positions whose trim has since been saved into the preset. */
  saved: number[]
  /** Position -> the correction that will be written, measured or edited. */
  proposals: Record<number, number>
  selected: number[]
  onToggle: (position: number) => void
  onPropose: (position: number, db: number) => void
  onNudge: (position: number, by: number) => void
  onResetProposal: (position: number) => void
  onMeasure: () => void
  onApply: () => void
  onSave: () => void
  onRevert: () => void
}): React.JSX.Element | null {
  if (slots.length === 0) return null

  const measured = slots.filter((s) => rows[s.position]?.measured !== undefined)
  const vals = measured
    .map((s) => rows[s.position].measured)
    .filter((v): v is number => v !== null && v !== undefined)
  const spread = vals.length > 1 ? Math.max(...vals) - Math.min(...vals) : null
  const unit = metric === 'perceived' ? '' : ' LUFS'
  const unsaved = Object.keys(applied).map(Number).filter((p) => !saved.includes(p))

  const proposalFor = (s: BenchSlot): number | null | undefined =>
    proposals[s.position] ?? rows[s.position]?.correction_db
  const isEdited = (s: BenchSlot): boolean => {
    const p = proposals[s.position]
    const m = rows[s.position]?.correction_db
    return p !== undefined && m !== undefined && m !== null && Math.abs(p - m) > 0.001
  }

  return (
    <div className="rep">
      <div className="rep-head">
        <h3>What each preset needs</h3>
        {spread !== null && (
          <Badge className={spread > 3 ? 'attn' : 'live'}>
            {spread.toFixed(1)} dB spread
          </Badge>
        )}
        {unsaved.length > 0 && (
          <Badge className="attn"><i className="dot-unsaved" />{unsaved.length} unsaved</Badge>
        )}
        <span className="fine">
          A proposal, not a change. Adjust any of them before applying.
        </span>
        <span className="grow" />
        {Object.keys(applied).length > 0 && (
          <Button size="sm" onClick={onRevert}>
            Undo trims ({Object.keys(applied).length})
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
        {unsaved.length > 0 && (
          <Button size="sm" variant="primary" disabled={busy} onClick={onSave}>
            Save {unsaved.length} to presets
          </Button>
        )}
      </div>

      <div className="rep-table">
        <div className="rep-row rep-th">
          <span />
          <span>Preset</span>
          <span className="right">{metric === 'perceived' ? 'N5 rel' : 'LUFS'}</span>
          <span className="right">True pk</span>
          <span className="right">Correction</span>
          <span className="right">Proposed&nbsp;dB</span>
        </div>
        {slots.map((s) => {
          const r = rows[s.position]
          const on = selected.includes(s.position)
          const live = progress === s.name
          const wrote = applied[s.position]
          const isUnsaved = wrote !== undefined && !saved.includes(s.position)
          return (
            <div key={`${s.folderKey}:${s.position}`}
                 className={`rep-row${on ? '' : ' skip'}${live ? ' now' : ''}${isUnsaved ? ' unsaved' : ''}`}>
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
                {/* The yellow dot is the only thing on screen that says this
                    trim lives in the device and not in the preset file. */}
                {isUnsaved && (
                  <i className="dot-unsaved"
                     title={`${fmt(wrote)} dB on the device, not saved to the preset`} />
                )}
                {s.name}
                {saved.includes(s.position) && <Badge className="live">saved</Badge>}
                {r?.error && <Badge className="bad">failed</Badge>}
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
                onChange={(db) => onPropose(s.position, db)}
                onNudge={(by) => onNudge(s.position, by)}
                onReset={() => onResetProposal(s.position)}
              />
            </div>
          )
        })}
      </div>

      <p className="hint rep-foot">
        {measured.length === 0
          ? `Measure all plays the riff into each preset in turn and reports how far it is from ${target}${unit}. Nothing is written.`
          : unsaved.length > 0
            ? <>Applied trims are on the device and <strong>not in the presets</strong> — the
              yellow dot marks them. Reloading a preset loses its trim; Save writes it in.</>
            : 'Apply moves the lane output fader by the proposed amount. It does not touch the preset file until you save.'}
      </p>
    </div>
  )
}
