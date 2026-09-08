import { Badge, Button } from '@singz/ui'
import type { SceneRow } from '@shared/types'
import { T, t } from '../i18n.js'

/**
 * Levelling the scenes of one preset against each other.
 *
 * A preset with a clean, a crunch and a lead is really three presets sharing a
 * grid, and they drift apart the same way whole presets do. This measures each
 * one and shows what it needs.
 *
 * Scenes need a different knob from the whole-preset case: a lane's output volume
 * has one value for the preset, so per-scene trims go to a Gain block's LEVEL,
 * which can hold eight. The panel says so rather than leaving it to be discovered.
 */

const LETTERS = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
const SCALE_DB = 12

const fmt = (v: number | null | undefined): string =>
  v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)}`

export function Scenes({
  rows, busy, progress, selected, presetName, onToggle, onMeasure, onApply
}: {
  /** Indexed 0-7. Undefined where the scene has not been measured. */
  rows: Record<number, SceneRow>
  busy: boolean
  /** Scene index being measured right now. */
  progress: number | null
  selected: number[]
  presetName: string | null
  onToggle: (scene: number) => void
  onMeasure: () => void
  onApply: () => void
}): React.JSX.Element {
  const measured = LETTERS.map((_l, i) => rows[i]).filter((r) => r?.measured != null)
  const defined = LETTERS.map((_l, i) => rows[i]).filter((r) => r && !r.undefinedScene)

  return (
    <div className="scn">
      <div className="scn-head">
        <h3>{presetName ? t('scn.titleOf', { name: presetName }) : t('scn.title')}</h3>
        {measured.length > 0 && <Badge className="live">{t('scn.measured', { n: String(measured.length) })}</Badge>}
        <span className="fine">{t('scn.lede')}</span>
        <span className="grow" />
        <Button size="sm" disabled={busy} onClick={onMeasure}>
          {busy ? t('scn.measuring', { at: progress !== null ? LETTERS[progress] : '' }) : t('scn.measure')}
        </Button>
        <Button size="sm" variant="primary"
                disabled={busy || measured.length === 0 || selected.length === 0}
                onClick={onApply}>
          {t('scn.apply', { n: String(selected.length) })}
        </Button>
      </div>

      <div className="scn-grid">
        {LETTERS.map((letter, i) => {
          const r = rows[i]
          const dead = r?.undefinedScene ?? false
          const on = selected.includes(i) && !dead
          const live = progress === i
          return (
            <div key={letter} className={`scn-cell${dead ? ' dead' : ''}${live ? ' now' : ''}`}>
              <button type="button" className={`chip${on ? ' active' : ''}`}
                      disabled={dead} onClick={() => onToggle(i)}
                      aria-label={t('scn.sceneN', { letter })}>
                {letter}
              </button>
              <span className="scn-name">{r?.name ?? (dead ? t('scn.undefined') : '—')}</span>
              <span className="scn-lufs">{live ? '…' : fmt(r?.measured)}</span>
              <span className="scn-corr">
                <span className="scn-track">
                  <i className="scn-zero" />
                  {r?.correction_db != null && (
                    <i
                      className="scn-bar"
                      style={{
                        width: `${(Math.min(Math.abs(r.correction_db), SCALE_DB) / SCALE_DB) * 50}%`,
                        left: r.correction_db >= 0
                          ? '50%'
                          : `${50 - (Math.min(Math.abs(r.correction_db), SCALE_DB) / SCALE_DB) * 50}%`
                      }}
                    />
                  )}
                </span>
                <b>{fmt(r?.correction_db)}</b>
              </span>
              {r?.limited && <Badge className="bad">{t('scn.held')}</Badge>}
            </div>
          )
        })}
      </div>

      <p className="hint">
        <T k="scn.hint" />
        {defined.length > 0 && t('scn.hintCount', { n: String(8 - defined.length) })}
      </p>
    </div>
  )
}
