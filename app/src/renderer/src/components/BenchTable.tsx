import { useState } from 'react'
import type { ReactNode } from 'react'
import { Badge, Button } from '@singz/ui'
import type { BenchSlot, MeterOutput, ReportRow } from '@shared/types'
import { t } from '../i18n.js'
import { MOD } from '../keys.js'
import { slotId } from '../bench.js'
import type { Step, WrittenMap } from '../bench.js'
import { channels, CEIL, FLOOR } from './Meter.js'

/**
 * One row per preset, and everything about that preset on the one row.
 *
 * What used to be a report row, a card in a strip and two scene lists is a
 * single 44 pt line: include, slot, name, the live stereo meter, the level the
 * fader is at now, what was measured, how far off it is, the change that will
 * be written — as a PROPOSAL you can argue with — what the level will be after,
 * and whether the device holds something the file does not. The focused row
 * opens a drawer for the things only the loaded preset has: its lanes, its
 * scenes, the by-ear knob.
 *
 * Nothing here happens on its own. Measuring produces a number per preset, the
 * number is shown, and it is editable: the measurement is a good opinion about
 * loudness and a bad one about taste, and a player who wants their lead two dB
 * above the rest should be able to say so before anything moves. Apply writes
 * exactly what is on the screen.
 *
 * The header and the rows share ONE grid, so a label can never drift off the
 * number it names. The rows are the only thing on the page that scrolls.
 */

const SCALE_DB = 12          // full width of one side of the correction bar
const STEP_DB = 0.5

const fmt = (v: number | null | undefined, digits = 1): string =>
  v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(digits)}`

const pct = (db: number): number =>
  Math.max(0, Math.min(1, (db - FLOOR) / (CEIL - FLOOR))) * 100

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
  const nudge = (by: number): void => {
    setDraft(null)
    onNudge(by)
  }
  const commit = (text: string): void => {
    const v = Number(text.trim().replace(',', '.').replace('−', '-'))
    setDraft(null)
    if (Number.isFinite(v)) onChange(Math.round(v * 10) / 10)
  }
  // No early return to a different node: an unmeasured row draws the same
  // field, disabled, so the column cannot change shape as results arrive.
  const empty = db === null || db === undefined
  return (
    <span className={`prop${edited ? ' edited' : ''}${empty ? ' muted' : ''}`}>
      <button type="button" className="nudge" disabled={disabled || empty}
              onClick={() => nudge(-STEP_DB)} aria-label={t('rep.quieter')}>−</button>
      <input
        type="text" inputMode="decimal" spellCheck={false} disabled={disabled || empty}
        value={empty ? '—' : draft ?? db.toFixed(1)}
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
      <button type="button" className="nudge" disabled={disabled || empty}
              onClick={() => nudge(STEP_DB)} aria-label={t('rep.louder')}>+</button>
      {/* always mounted: only visible once the value differs from the measurement */}
      <button type="button" className="undo" disabled={disabled} onClick={onReset}
              title={t('rep.resetHint')} style={{ visibility: edited ? 'visible' : 'hidden' }}>↺</button>
    </span>
  )
}

/** The focused row's live stereo meter, inline. Two bars; nothing when silent. */
function MiniMeter({ outputs }: { outputs: Record<string, MeterOutput> | null }): React.JSX.Element {
  const pair = outputs ? channels(outputs) : []
  const [l, r] = pair.length ? pair : [FLOOR, FLOOR]
  return (
    <span className="bt-mi" aria-hidden>
      <span><i style={{ width: `${pct(l)}%` }} /></span>
      <span><i style={{ width: `${pct(r)}%` }} /></span>
    </span>
  )
}

export interface BenchTableProps {
  slots: BenchSlot[]
  /** Keyed by slot id; empty until measured. */
  rows: Record<string, ReportRow>
  metric: 'lufs' | 'perceived'
  /** A run that owns the bench — measuring, applying, saving. */
  busy: boolean
  /** Name of the preset being measured right now, for the in-flight row. */
  progress: string | null
  listening: boolean
  /** Slot id -> what this session wrote to the device, and whether it was saved. */
  written: WrittenMap
  /** Slot id -> the correction that will be written, measured or edited. */
  proposals: Record<string, number>
  selected: string[]
  /** The preset loaded on the device, if it is one of ours. */
  focusId: string | null
  /** A recall in flight towards this slot: its row shows the drawer's skeleton. */
  pendingId: string | null
  drawerOpen: boolean
  /** The focused row's drawer, rendered under it by the table. */
  drawer: ReactNode
  /** Slot id -> where its fader is, for the focused row live and others as last seen. */
  nowDb: Record<string, number>
  meter: Record<string, MeterOutput> | null
  autoSave: boolean
  /** Which of this band's buttons is the screen's one primary, if any. */
  primary: Step
  /** One line of what went wrong, for the band's footline; null when nothing did. */
  footline: string | null
  onDismissFootline: () => void
  canSaveRow: (id: string) => boolean
  onAutoSave: (on: boolean) => void
  onToggle: (id: string) => void
  onPropose: (id: string, db: number) => void
  onNudge: (id: string, by: number) => void
  onResetProposal: (id: string) => void
  onUndoOne: (id: string) => void
  onSaveRow: (id: string) => void
  onFocus: (id: string) => void
  onOpen: (id: string) => void
  onMeasure: () => void
  onListen: () => void
  onApply: () => void
  onSave: () => void
  onRevert: () => void
  onAdd: () => void
}

export function BenchTable({
  slots, rows, metric, busy, progress, listening, written, proposals, selected,
  focusId, pendingId, drawerOpen, drawer, nowDb, meter, autoSave, primary, footline, onDismissFootline,
  canSaveRow, onAutoSave, onToggle, onPropose, onNudge, onResetProposal, onUndoOne, onSaveRow,
  onFocus, onOpen, onMeasure, onListen, onApply, onSave, onRevert, onAdd
}: BenchTableProps): React.JSX.Element {
  const ids = slots.map(slotId)
  const measured = slots.filter((s) => rows[slotId(s)]?.measured !== undefined)
  const vals = measured
    .map((s) => rows[slotId(s)].measured)
    .filter((v): v is number => v !== null && v !== undefined)
  const spread = vals.length > 1 ? Math.max(...vals) - Math.min(...vals) : null
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
    <>
      {/* ── the band's head: what the bench can do, and how it stands ── */}
      <div className="bt-head">
        <span className="eyebrow">{t('lvl.presets')}</span>
        {/* both badges always mounted; an empty one is invisible, not absent */}
        <Badge className={`${spread !== null && spread > 3 ? 'attn' : 'live'}${spread === null ? ' hide' : ''}`}>
          {t('rep.spread', { db: (spread ?? 0).toFixed(1) })}
        </Badge>
        <Badge className={`attn${unsaved.length ? '' : ' hide'}`}>
          <i className="dot-unsaved" />{t('rep.unsaved', { n: String(unsaved.length) })}
        </Badge>
        <span className="grow" />
        <Button size="sm" variant={primary === 'measure' ? 'primary' : undefined}
                disabled={busy || slots.length === 0} onClick={onMeasure}
                title={t('rep.measureHint')}>
          {t('lvl.measureAll')} <kbd>M</kbd>
        </Button>
        <Button size="sm" disabled={slots.length === 0 || busy} onClick={onListen}
                title={t('lvl.listenHint')}>
          {listening ? t('lvl.stop') : t('lvl.listen')} <kbd>L</kbd>
        </Button>
        <span className="bt-sep" />
        <Button size="sm" variant={primary === 'apply' ? 'primary' : undefined}
                disabled={busy || selected.length === 0 || measured.length === 0}
                onClick={onApply} title={t('rep.applyHint')}>
          {t('lvl.applyN', { n: String(selected.length) })} <kbd>{MOD}↵</kbd>
        </Button>
        <Button size="sm" variant="ghost" disabled={applied.length === 0} onClick={onRevert}>
          {t('lvl.undo')}
        </Button>
        <Button size="sm" variant={primary === 'save' ? 'primary' : undefined}
                disabled={busy || unsaved.length === 0} onClick={onSave}>
          {t('lvl.saveN', { n: String(unsaved.length) })} <kbd>{MOD}⇧S</kbd>
        </Button>
        <label className="lvl-auto" title={t('lvl.autosaveTitle')}>
          <input type="checkbox" checked={autoSave} onChange={(e) => onAutoSave(e.target.checked)} />
          {t('lvl.autosave')}
        </label>
      </div>
      {/* one footline for the band: a fixed track, ellipsised, the whole text in
          the title. It replaces both the row-wrapping error and the page-wide bar. */}
      <div className={`bt-foot${footline ? ' bad' : ''}`} title={footline ?? ''}>
        <span className="bt-foot-text">{footline ?? ''}</span>
        <button type="button" className="bt-foot-x" onClick={onDismissFootline}
                style={{ visibility: footline ? 'visible' : 'hidden' }}
                aria-label={t('lvl.dismiss')}>×</button>
      </div>

      {/* ── the only scroller on the page ── */}
      <div className="bt-body">
        {slots.length === 0 ? (
          <div className="empty">
            <h2>{t('lvl.emptyTitle')}</h2>
            <p className="fine">{t('lvl.emptyBody')}</p>
            <Button onClick={onAdd}>{t('lvl.addPreset')}</Button>
          </div>
        ) : (
          <>
            <div className="bt-cols bt-colhead">
              <span /><span /><span />
              <span className="grp">{t('lvl.col.in')}</span>
              <span className="grp r">{t('lvl.col.now')}</span>
              <span className="grp r">{metric === 'perceived' ? 'N5 rel' : t('lvl.col.lufs')}</span>
              <span className="grp r"><abbr title={t('rep.col.truePeakHint')}>{t('rep.col.truePeak')}</abbr></span>
              <span className="grp r">{t('lvl.col.offBy')}</span>
              <span className="grp r"><abbr title={t('lvl.col.changeHint')}>{t('lvl.col.change')}</abbr></span>
              <span className="grp r">{t('lvl.col.after')}</span>
              <span /><span />
            </div>
            {slots.map((s, i) => {
              const id = slotId(s)
              const r = rows[id]
              const on = selected.includes(id)
              const live = progress === s.name
              const focused = id === focusId
              const w = written[id]
              const isUnsaved = w !== undefined && !w.saved
              const now = nowDb[id]
              const prop = proposalFor(s)
              const after = now !== undefined && prop !== null && prop !== undefined ? now + prop : null
              const showDrawer = pendingId === id || (focused && drawerOpen && pendingId === null)
              return (
                <div key={id} className="bt-slot">
                  <div
                    className={`bt-cols bt-row${on ? '' : ' skip'}${live ? ' now' : ''}${focused ? ' on' : ''}${isUnsaved ? ' unsaved' : ''}`}
                    onClick={(e) => {
                      if ((e.target as HTMLElement).closest('button, input, label')) return
                      onFocus(id)
                    }}
                  >
                    <button type="button" className={`selbox${on ? ' on' : ''}`}
                            onClick={() => onToggle(id)}
                            aria-label={on ? t('rep.exclude', { name: s.name }) : t('rep.include', { name: s.name })}>
                      <svg width="11" height="11" viewBox="0 0 24 24" fill="none"
                           stroke="currentColor" strokeWidth="3.4" strokeLinecap="round"
                           strokeLinejoin="round" aria-hidden>
                        <path d="M20 6 9 17l-5-5" />
                      </svg>
                    </button>
                    <span className="bt-no">{i + 1}</span>
                    <span className="bt-name">
                      <button type="button" className="bt-chev"
                              onClick={() => onOpen(id)}
                              aria-label={showDrawer ? t('lvl.drawer.close', { name: s.name }) : t('lvl.drawer.open', { name: s.name })}>
                        {showDrawer ? '▾' : '▸'}
                      </button>
                      <span className="bt-nm" title={s.name}>{s.name}</span>
                      {/* the row's own error, on the row; the sentence is in the footline */}
                      <i className="bt-errdot" title={r?.error ?? ''}
                         style={{ visibility: r?.error ? 'visible' : 'hidden' }} />
                    </span>
                    <MiniMeter outputs={focused ? meter : null} />
                    <span className={`num${focused ? ' strong' : ''}`}>
                      {now === undefined ? '—' : `${fmt(now)} dB`}
                    </span>
                    <span className="num">
                      {live ? '…' : r?.measured !== undefined ? fmt(r.measured) : '—'}
                    </span>
                    <span className="num dim">{live ? '' : r ? fmt(r.true_peak) : '—'}</span>
                    <Correction db={prop} edited={isEdited(s)} />
                    <Proposal
                      db={prop}
                      edited={isEdited(s)}
                      disabled={busy || !on}
                      onChange={(db) => onPropose(id, db)}
                      onNudge={(by) => onNudge(id, by)}
                      onReset={() => onResetProposal(id)}
                    />
                    <span className="num after">{after === null ? '—' : `${fmt(after)} dB`}</span>
                    {/* The yellow dot is the only thing on screen that says this
                        trim lives in the device and not in the preset file. */}
                    <span className="bt-dotcell">
                      <i className="dot-unsaved" title={t('rep.dotHint', { db: fmt(w?.db) })}
                         style={{ visibility: isUnsaved ? 'visible' : 'hidden' }} />
                      <Badge className={`live${w?.saved ? '' : ' hide'}`}>
                        {t('rep.saved')}
                      </Badge>
                    </span>
                    <span className="bt-act">
                      {isUnsaved && w.db !== null ? (
                        <button type="button" className="rowundo" title={t('rep.undoOneHint')}
                                onClick={() => onUndoOne(id)}>{t('rep.undoOne')}</button>
                      ) : (
                        <Button size="sm" variant="ghost" disabled={!canSaveRow(id)}
                                onClick={() => onSaveRow(id)}>{t('lvl.save')}</Button>
                      )}
                    </span>
                  </div>
                  {showDrawer && (
                    pendingId === id
                      ? <div className="drw skeleton" aria-busy><span className="fine">{t('lvl.drawer.loading')}</span></div>
                      : drawer
                  )}
                </div>
              )
            })}
          </>
        )}
      </div>
    </>
  )
}
