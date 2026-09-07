import { Badge, Button } from '@singz/ui'
import type { SceneRow } from '@shared/types'

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
        <h3>{presetName ? `${presetName} · scenes` : 'Scenes'}</h3>
        {measured.length > 0 && <Badge className="live">{measured.length} measured</Badge>}
        <span className="fine">
          Each scene measured on its own, because a clean and a lead in one preset
          drift apart just like two presets do.
        </span>
        <span className="grow" />
        <Button size="sm" disabled={busy} onClick={onMeasure}>
          {busy ? `Measuring ${progress !== null ? LETTERS[progress] : ''}…` : 'Measure scenes'}
        </Button>
        <Button size="sm" variant="primary"
                disabled={busy || measured.length === 0 || selected.length === 0}
                onClick={onApply}>
          Apply to {selected.length} scenes
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
                      aria-label={`Scene ${letter}`}>
                {letter}
              </button>
              <span className="scn-name">{r?.name ?? (dead ? 'undefined' : '—')}</span>
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
              {r?.limited && <Badge className="bad">held</Badge>}
            </div>
          )
        })}
      </div>

      <p className="hint">
        Per-scene trims go to a <strong>Gain block</strong>, not the lane fader: a lane
        has one output level for the whole preset, while a Gain block&rsquo;s LEVEL can
        hold a value per scene. The block is added to the measured lane if it is not
        already there. Scenes with no data of their own are left alone
        {defined.length > 0 && ` — ${8 - defined.length} of 8 here`}.
      </p>
    </div>
  )
}
