import { useEffect, useRef, useState } from 'react'
import type { InputLevel } from '@shared/types'
import { t } from '../i18n.js'

/**
 * Your guitar, at the full height of the window.
 *
 * A level is easier to judge over 600 pt than over 40, and this is the one
 * value that matters at every step — arming, recording, judging whether a take
 * was loud enough — so it lives on the left edge rather than inside whichever
 * panel happens to be open. Two sources share one geometry: the DI input while
 * the recorder is armed or rolling, the loudest output the rest of the time.
 * Only a two-letter cap changes between them; nothing resizes.
 *
 * The meter animates OUTSIDE React. Readings arrive at 4-7 Hz and React would
 * happily re-render the whole rail for each; instead one requestAnimationFrame
 * loop smooths the level, holds the peak (1.6 s, then 24 dB/s) and writes three
 * styles and two text nodes. It stops itself after two seconds of silence.
 */

const FLOOR = -60
const HOLD_MS = 1600
const FALL_DB_S = 24
const CLIP_DB = -0.5
const CLIP_MS = 900
const SILENT = -90
const TICKS = [0, -6, -12, -20, -30, -40, -50, -60]

const frac = (db: number): number => Math.max(0, Math.min(1, (db - FLOOR) / -FLOOR))
const say = (v: number): string =>
  v <= -85 ? '−∞' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(1)}`

export function InputRail({
  inDbfs, outDb, armed, thresholdDbfs, live
}: {
  /** The DI level while the recorder is armed or recording; null otherwise. */
  inDbfs: number | null
  /** The loudest output, in dB re full scale, when the input is not the story. */
  outDb: number | null
  /** Show the start threshold the first note has to cross. */
  armed: boolean
  thresholdDbfs: number
  /** A session is up: the trim can be read and written. */
  live: boolean
}): React.JSX.Element {
  const source: 'in' | 'out' = inDbfs !== null ? 'in' : 'out'
  const target = useRef(SILENT)
  target.current = source === 'in' ? (inDbfs ?? SILENT) : (outDb ?? SILENT)

  const mask = useRef<HTMLDivElement>(null)
  const peakEl = useRef<HTMLDivElement>(null)
  const clipEl = useRef<HTMLSpanElement>(null)
  const nowEl = useRef<HTMLSpanElement>(null)
  const pkEl = useRef<HTMLSpanElement>(null)

  // one loop for the life of the component; it parks itself when nothing moves
  useEffect(() => {
    let level = SILENT, peak = SILENT, peakAt = 0, clipAt = 0
    let last = performance.now(), quietSince: number | null = null
    let raf = 0, parked = false

    const paint = (): void => {
      if (mask.current) mask.current.style.height = `${(1 - frac(level)) * 100}%`
      if (peakEl.current) peakEl.current.style.bottom = `${frac(peak) * 100}%`
      if (nowEl.current) nowEl.current.textContent = say(level)
      if (pkEl.current) pkEl.current.textContent = say(peak)
    }
    const tick = (now: number): void => {
      const dt = Math.min(0.1, (now - last) / 1000)
      last = now
      const want = target.current
      // fast attack, slower release, like every meter worth reading
      level = want > level ? want : level + (want - level) * Math.min(1, dt * 7)
      level = Math.max(SILENT, level)
      if (level >= peak) { peak = level; peakAt = now }
      else if (now - peakAt > HOLD_MS) peak = Math.max(level, peak - FALL_DB_S * dt)
      if (level > CLIP_DB) clipAt = now
      clipEl.current?.classList.toggle('lit', now - clipAt < CLIP_MS)
      paint()

      const quiet = want <= -85 && peak <= -85
      if (quiet && quietSince === null) quietSince = now
      if (!quiet) quietSince = null
      if (quietSince !== null && now - quietSince > 2000) { parked = true; return }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    // wake the parked loop when a reading arrives
    const poll = setInterval(() => {
      if (parked && target.current > -85) {
        parked = false; quietSince = null; last = performance.now()
        raf = requestAnimationFrame(tick)
      }
    }, 150)
    return () => { cancelAnimationFrame(raf); clearInterval(poll) }
  }, [])

  return (
    <aside className="irail" aria-label={t('lvl.rail.input')}>
      <span className="eyebrow irail-cap">{t('lvl.rail.input')}</span>
      {/* the source cap: both mounted, one visible, so the width never changes */}
      <span className="irail-src">
        <span hidden={source !== 'in'}>{t('lvl.rail.in')}</span>
        <span hidden={source !== 'out'}>{t('lvl.rail.out')}</span>
      </span>
      <span className="irail-clip" ref={clipEl}>{t('lvl.rail.clip')}</span>

      <div className="irail-gauge">
        <div className={`irail-track${armed ? ' arming' : ''}`}>
          <div className="irail-bed" />
          <div className="irail-mask" ref={mask} style={{ height: '100%' }} />
          <div className="irail-peak" ref={peakEl} style={{ bottom: 0 }} />
          {/* always mounted: only its visibility follows `armed` */}
          <div className="irail-thr" style={{ bottom: `${frac(thresholdDbfs) * 100}%` }} />
        </div>
        <div className="irail-scale" aria-hidden>
          {TICKS.map((v) => <span key={v}>{v === 0 ? '0' : `−${-v}`}</span>)}
        </div>
      </div>

      <div className="irail-read">
        <span className="irail-now" ref={nowEl}>−∞</span>
        <span className="irail-hold">{t('lvl.rail.peak')} <b ref={pkEl}>−∞</b></span>
      </div>

      <Trim live={live} />
    </aside>
  )
}

/**
 * The QC's own INPUT 1 LEVEL, at the foot of the meter.
 *
 * A take that is "too quiet to measure with" is usually a low input trim, and
 * that control lived three screens away in I/O Settings. It is boxed off by a
 * rule and labelled global because, unlike everything else on this screen, it
 * is a device-wide setting, not a per-preset value — which is also why the
 * first write in a session asks.
 */
function Trim({ live }: { live: boolean }): React.JSX.Element {
  const [lvl, setLvl] = useState<InputLevel | null>(null)
  const [asking, setAsking] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(false)
  const agreed = useRef(false)

  useEffect(() => {
    if (!live) { setLvl(null); return }
    let gone = false
    window.patchbay.leveling.inputLevel()
      .then((v) => { if (!gone) setLvl(v) })
      .catch(() => { if (!gone) setErr(true) })
    return () => { gone = true }
  }, [live])

  const write = (db: number): void => {
    setBusy(true); setAsking(null)
    window.patchbay.leveling.setInputLevel(db)
      .then((v) => { setLvl(v); setErr(false) })
      .catch(() => setErr(true))
      .finally(() => setBusy(false))
  }
  const step = (by: number): void => {
    if (!lvl || busy) return
    const db = Math.round((lvl.db + by) * 2) / 2
    if (agreed.current) write(db)
    else setAsking(db)
  }
  const can = live && lvl !== null && !busy

  return (
    <div className="irail-trim">
      <span className="eyebrow irail-cap small">{t('lvl.rail.trim')}</span>
      {/* the stepper and the confirm share one cell: same height, one visible */}
      <div className="irail-trimrow" hidden={asking !== null}>
        <button type="button" className="lvl-stp" disabled={!can}
                onClick={() => step(-1)} aria-label={t('lvl.rail.trimDown')}>−</button>
        <b>{lvl ? say(lvl.db) : err ? '?' : '—'}</b>
        <button type="button" className="lvl-stp" disabled={!can}
                onClick={() => step(+1)} aria-label={t('lvl.rail.trimUp')}>+</button>
      </div>
      <div className="irail-trimrow ask" hidden={asking === null}
           title={t('lvl.rail.trimConfirm')}>
        <button type="button" className="lvl-stp ok"
                onClick={() => { agreed.current = true; if (asking !== null) write(asking) }}>
          {t('lvl.rail.ok')}
        </button>
        <button type="button" className="lvl-stp" onClick={() => setAsking(null)}>
          {t('lvl.rail.no')}
        </button>
      </div>
      <span className="irail-src" title={t('lvl.rail.trimConfirm')}>{t('lvl.rail.global')}</span>
    </div>
  )
}
