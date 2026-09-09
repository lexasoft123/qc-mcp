import { useState } from 'react'
import { Badge, Button } from '@singz/ui'
import type { AutoResult, AutoStep, BenchSlot, Measurement, PresetState, SceneRow } from '@shared/types'
import { t } from '../i18n.js'
import { Knob } from './Knob.js'
import { Scenes } from './Scenes.js'

/**
 * What only the LOADED preset has.
 *
 * Every row shows what can be known about a preset from a measurement. The
 * drawer under the focused row shows what needs the device to actually have it
 * loaded: where each output lane sits, which scene is active, the by-ear knob,
 * and the per-preset measure / suggest / level-to-target that used to live in
 * the recorder panel. Opening it never costs a recall the focus did not already
 * pay for — a non-focused row's chevron is the same six seconds as clicking it.
 *
 * Fixed minimum height: its content varies with the preset, the page must not.
 */

const SCENES = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
const MIN_DB = -40
const MAX_DB = 12

const show = (db: number): string => `${db > 0 ? '+' : ''}${db.toFixed(1)}`
const fmt = (v: number | null | undefined): string =>
  v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)}`

export function RowDrawer({
  slot, preset, level, dirty, busy, saving,
  onLevel, onCommit, onTrim, onScene, onSave, onRemove,
  scenes, tools
}: {
  slot: BenchSlot
  preset: PresetState | null
  /** The mean of the live output lanes, in dB. */
  level: number
  dirty: boolean
  /** Some gesture is in flight; the knob and steppers wait. */
  busy: boolean
  saving: boolean
  onLevel: (db: number, commit: boolean) => void
  onCommit: (db: number) => void
  onTrim: (row: number, delta: number) => void
  onScene: (index: number) => void
  onSave: () => void
  onRemove: () => void
  scenes: {
    rows: Record<number, SceneRow>
    busy: boolean
    progress: number | null
    selected: number[]
    onToggle: (scene: number) => void
    onMeasure: () => void
    onApply: () => void
  }
  tools: {
    target: number
    step: AutoStep | null
    /** There is a riff to measure with and the recorder is at rest. */
    ready: boolean
    onTrimmed: () => void
  }
}): React.JSX.Element {
  const lanes = preset?.lanes.filter((l) => l.physical) ?? []
  return (
    <div className="drw">
      <div className="drw-row">
        <span className="eyebrow">{t('lvl.drawer.byEar')}</span>
        <Knob db={level} min={MIN_DB} max={MAX_DB} size={44}
              disabled={!lanes.length || busy}
              onChange={(v) => onLevel(v, false)} onCommit={onCommit}
              label={t('lvl.drawer.laneOut')} />
        <span className="drw-lane">
          {t('lvl.drawer.laneOut')} <b>{lanes.length ? `${show(level)} dB` : '—'}</b>
          <button type="button" className="lvl-stp" disabled={!lanes.length || busy}
                  onClick={() => onCommit(level - 0.5)} aria-label={t('rep.quieter')}>−</button>
          <button type="button" className="lvl-stp" disabled={!lanes.length || busy}
                  onClick={() => onCommit(level + 0.5)} aria-label={t('rep.louder')}>+</button>
        </span>
        <span className="drw-hint">{t('lvl.drawer.hint')}</span>
        <span className="grow" />
        <Badge className={`attn${dirty ? '' : ' hide'}`}>{t('lvl.unsaved')}</Badge>
        <Button size="sm" variant="primary" disabled={!dirty || busy} onClick={onSave}>
          {saving ? t('lvl.saving') : t('lvl.save')}
        </Button>
      </div>

      <div className="drw-row">
        <span className="eyebrow">{t('lvl.drawer.outs')}</span>
        {/* four lane cells always: the QC has four rows, so the row never re-flows */}
        {Array.from({ length: 4 }, (_, k) => {
          const l = preset?.lanes[k]
          if (!l) return <span key={k} className="drw-lane empty" aria-hidden />
          return (
            <span key={l.row} className={`drw-lane${l.active ? '' : ' off'}`}>
              {l.out} <b>{l.active ? `${show(l.db)} dB` : t('lvl.silent')}</b>
              <button type="button" className="lvl-stp" disabled={!l.active || busy}
                      onClick={() => onTrim(l.row, -0.5)} aria-label={t('lvl.down', { out: l.out })}>−</button>
              <button type="button" className="lvl-stp" disabled={!l.active || busy}
                      onClick={() => onTrim(l.row, +0.5)} aria-label={t('lvl.up', { out: l.out })}>+</button>
            </span>
          )
        })}
        <span className="grow" />
        <button type="button" className="rowundo" onClick={onRemove}>{t('lvl.remove', { name: slot.name })}</button>
      </div>

      <div className="drw-row">
        <span className="eyebrow">{t('lvl.drawer.scenes')}</span>
        {SCENES.map((s, si) => {
          const label = preset?.sceneLabels?.[si] || ''
          const on = preset?.scene === si
          return (
            <button key={s} type="button"
                    className={`drw-chip${on ? ' on' : ''}${label ? '' : ' empty'}`}
                    disabled={busy} title={label || t('lvl.scene', { s })}
                    onClick={() => onScene(si)}>
              <b>{s}</b>{label ? ` ${label}` : ' ·'}
            </button>
          )
        })}
      </div>

      <Scenes
        rows={scenes.rows} busy={scenes.busy} progress={scenes.progress}
        selected={scenes.selected} presetName={preset?.name ?? slot.name}
        onToggle={scenes.onToggle} onMeasure={scenes.onMeasure} onApply={scenes.onApply}
      />

      <PresetTools {...tools} />
    </div>
  )
}

/**
 * Measure / suggest / level-to-target for the ONE loaded preset.
 *
 * Lifted out of the recorder panel, where it sat next to the riff because the
 * numbers only mean anything next to the knob they explain — and the knob is
 * here now.
 */
function PresetTools({ target, step, ready, onTrimmed }: {
  target: number
  step: AutoStep | null
  ready: boolean
  onTrimmed: () => void
}): React.JSX.Element {
  const [reading, setReading] = useState<Measurement | null>(null)
  const [result, setResult] = useState<AutoResult | null>(null)
  const [busy, setBusy] = useState<'measure' | 'auto' | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const measure = async (): Promise<void> => {
    setBusy('measure'); setErr(null); setResult(null)
    try {
      const m = await window.patchbay.leveling.measure()
      setReading(m)
      if (m.error) setErr(m.error)
    } catch (e) { setErr((e as Error).message) } finally { setBusy(null) }
  }
  const run = async (dryRun: boolean): Promise<void> => {
    setBusy('auto'); setErr(null); setReading(null)
    try {
      const r = await window.patchbay.leveling.autolevel({ target, dryRun })
      setResult(r)
      if (r.error) setErr(r.error)
      if (r.written) onTrimmed()
    } catch (e) { setErr((e as Error).message) } finally { setBusy(null) }
  }

  const running = busy === 'auto'
  const shown = step ?? result?.iterations.at(-1) ?? null

  // One line, always present, whatever it has to say.
  const line = ((): string => {
    if (err) return err
    if (reading && !reading.error) {
      return [
        `${fmt(reading.lufs_integrated)} LUFS`,
        t('lvl.tools.truePeak', { db: fmt(reading.true_peak_dbtp) }),
        t('lvl.tools.needs', { db: fmt(target - (reading.lufs_integrated ?? 0)) })
      ].join(' · ')
    }
    if (shown) {
      const parts = [
        t('lvl.tools.pass', { n: String(shown.n) }),
        `${fmt(shown.measured)} LUFS`,
        t('lvl.tools.offBy', { db: fmt(shown.delta_db) })
      ]
      if (shown.wrote_db !== null) parts.push(t('lvl.tools.trim', { db: fmt(shown.wrote_db) }))
      if (shown.limited_by === 'true_peak') parts.push(t('lvl.tools.held', { db: fmt(shown.backed_off_to_db) }))
      if (result && !running) {
        parts.push(result.suggested_db !== undefined
          ? t('lvl.tools.suggestion')
          : result.converged
            ? t('lvl.tools.landed', { db: Math.abs(result.final_delta_db ?? 0).toFixed(1) })
            : t('lvl.tools.notSettled', { db: fmt(result.final_delta_db) }))
      }
      return parts.join(' · ')
    }
    return ready ? '' : t('lvl.tools.needRiff')
  })()

  return (
    <div className="drw-row drw-tools">
      <Button size="sm" disabled={busy !== null || !ready} onClick={() => void measure()}>
        {busy === 'measure' ? t('msd.measuring') : t('msd.measure')}
      </Button>
      <Button size="sm" disabled={busy !== null || !ready} onClick={() => void run(true)}>
        {t('msd.suggest')}
      </Button>
      <Button size="sm" disabled={busy !== null || !ready} onClick={() => void run(false)}>
        {running ? t('msd.levelling') : t('msd.levelTo')}
      </Button>
      <span className={`drw-out${err ? ' bad' : ''}`} title={line}>{line}</span>
    </div>
  )
}
