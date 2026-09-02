import { useCallback, useEffect, useRef, useState } from 'react'
import { Badge, Button, StatusDot } from '@singz/ui'
import type { BenchSlot, LevelEvent, MeterOutput, PresetState, Snapshot } from '@shared/types'
import { isMac, slotId } from '../derive.js'
import { act, say } from '../store.js'
import { T, t } from '../i18n.js'
import { Knob } from '../components/Knob.js'
import { Meter, loudest } from '../components/Meter.js'
import { PresetPicker } from '../modals/PresetPicker.js'

const SCENES = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
/** The QC grid is four rows, so every column reserves four lane slots and the
 *  cards stay the same size whatever is loaded in them. */
const LANE_SLOTS = 4
/** The lane output range, calibrated against Cortex Control. */
const MIN_DB = -40
const MAX_DB = 12
/** While dragging, write no faster than this — you level by ear, so the device
 *  has to follow the knob, but every pointermove would flood the session. */
const WRITE_MS = 80

const clamp = (v: number, lo: number, hi: number): number => Math.max(lo, Math.min(hi, v))
const show = (db: number): string => `${db > 0 ? '+' : ''}${db.toFixed(1)}`

/**
 * The lanes that carry the preset out of the box.
 *
 * Only these are levelled together: a merge bus is a lane too, and trimming it
 * alongside the row it feeds would apply the same change twice.
 */
const outs = (p: PresetState | null): PresetState['lanes'] =>
  p ? p.lanes.filter((l) => l.physical) : []

/** One number for a preset's loudness: the mean of its live output lanes. */
function levelOf(p: PresetState | null): number {
  const l = outs(p)
  return l.length ? l.reduce((a, x) => a + x.db, 0) / l.length : 0
}

export function Leveling({ snap }: { snap: Snapshot }): React.JSX.Element {
  const bench = snap.prefs.bench
  const autoSave = snap.prefs.benchAutoSave

  // -1 = nothing on the bench is the loaded preset, so no column claims to be live
  const [focus, setFocus] = useState(-1)
  const [preset, setPreset] = useState<PresetState | null>(null)
  const [meter, setMeter] = useState<Record<string, MeterOutput> | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  /** …and as a ref: auto-save fires from inside the same gesture that sets it,
   *  so a closure would still be reading the pre-change value. */
  const dirtyRef = useRef(false)
  const mark = useCallback((v: boolean): void => { dirtyRef.current = v; setDirty(v) }, [])
  const [picking, setPicking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  /** Loudest dB seen per slot this session. Deliberately not persisted: it
   *  describes a performance, not the preset. */
  const [peaks, setPeaks] = useState<Record<string, number>>({})

  const live = snap.daemon.state === 'running'
  const slot: BenchSlot | undefined = bench[focus]
  const lanes = outs(preset)
  const level = levelOf(preset)

  const saveBench = useCallback(
    (next: BenchSlot[]) => act(() => window.patchbay.setPrefs({ bench: next })),
    []
  )

  // ── the device session ────────────────────────────────────────────────

  /**
   * The bench is a *session* resource, not a component one: it lives as long as
   * the daemon it attached to, and the main process stops it when the daemon
   * stops or the app quits. Tearing it down on unmount would also break under
   * StrictMode's double-mount, whose cleanup closed the child's stdin and left
   * the second mount holding a dead process.
   */
  useEffect(() => {
    if (!live) {
      void window.patchbay.leveling.stop()
      return
    }
    let gone = false
    void window.patchbay.leveling.start()
    const off = window.patchbay.leveling.onEvent((e: LevelEvent) => {
      if (e.event === 'meter') setMeter(e.outputs)
      else if (e.error) setError(e.error)
    })
    void window.patchbay.leveling.meter(true).catch(() => undefined)
    // Adopt the preset the device already has, but only if it is one of ours —
    // showing the loaded preset's lanes under a different column's name would be
    // a lie, and silently recalling on open would change the rig underfoot.
    window.patchbay.leveling
      .state()
      .then((p) => {
        if (gone) return
        const i = bench.findIndex(
          (b) => b.folderKey === p.folderKey && b.position === p.position
        )
        if (i >= 0) { setFocus(i); setPreset(p) }
      })
      .catch((e: Error) => { if (!gone) setError(e.message) })
    return () => { gone = true; off() }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live])

  /** Peak-hold per slot, so columns you are not on still say how loud they were. */
  useEffect(() => {
    if (!meter || !slot) return
    const now = loudest(meter)
    if (now === null) return
    const id = slotId(slot)
    setPeaks((p) => (p[id] !== undefined && now <= p[id] ? p : { ...p, [id]: now }))
  }, [meter, slot])

  // ── actions ───────────────────────────────────────────────────────────

  const guard = async (what: string, fn: () => Promise<void>): Promise<void> => {
    setBusy(what)
    setError(null)
    try {
      await fn()
    } catch (e) {
      const msg = (e as Error).message
      setError(msg)
      say(msg, true)
    } finally {
      setBusy(null)
    }
  }

  const goto = (index: number): void => {
    const target = bench[index]
    if (!target || index === focus || busy) return
    void guard('load', async () => {
      const p = await window.patchbay.leveling.open(
        target.folderKey, target.position, false, target.cloudId
      )
      setFocus(index)
      mark(false)
      setMeter(null)
      // Come back to the scene you left on — but only if you ever left one.
      // A slot you have not visited keeps whatever scene the preset loads with.
      if (target.scene !== null && target.scene !== p.scene) {
        await window.patchbay.leveling.scene(target.scene)
        setPreset({ ...p, scene: target.scene })
      } else {
        setPreset(p)
      }
    })
  }

  const pickScene = (index: number): void => {
    if (!preset || !slot) return
    void guard('scene', async () => {
      const s = await window.patchbay.leveling.scene(index)
      setPreset((p) => (p ? { ...p, scene: s } : p))
      await saveBench(bench.map((b, i) => (i === focus ? { ...b, scene: s } : b)))
      // scene levels are per-preset, not per-scene, but the lanes may differ
      const fresh = await window.patchbay.leveling.state()
      setPreset(fresh)
    })
  }

  /**
   * The lanes as last written, updated synchronously.
   *
   * A drag fires `onChange` far faster than React re-renders, so every handler
   * in a burst would otherwise read the same stale `preset` and compute its
   * delta from the same base — the UI ends up somewhere the device never went.
   */
  const liveLanes = useRef<PresetState['lanes']>([])
  useEffect(() => { liveLanes.current = preset?.lanes ?? [] }, [preset])

  const writeAt = useRef(0)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  /** A throttled write that has not gone out yet. */
  const pending = useRef(false)

  /** Send the lanes as they currently stand. Reads the ref, not a closure, so
   *  a flush at the end of a drag sends the latest values. */
  const write = useCallback((): void => {
    writeAt.current = Date.now()
    pending.current = false
    if (timer.current) { clearTimeout(timer.current); timer.current = null }
    for (const x of liveLanes.current) {
      if (x.physical) void window.patchbay.leveling.level(x.row, x.db).catch(() => undefined)
    }
  }, [])

  /** Move every live lane by the same delta, so their balance survives. */
  const applyLevel = useCallback((target: number, commit: boolean): void => {
    const lanesNow = liveLanes.current
    const phys = lanesNow.filter((x) => x.physical)
    if (!phys.length) return
    const mean = phys.reduce((a, x) => a + x.db, 0) / phys.length
    // clamp the DELTA, not each lane, so the balance between them survives the ends
    const head = Math.min(...phys.map((x) => MAX_DB - x.db))
    const foot = Math.min(...phys.map((x) => x.db - MIN_DB))
    const delta = clamp(target - mean, -foot, head)

    if (!delta) {
      // Nothing moved. Two ways to get here: the commit that ends a drag whose
      // last move already landed — flush anything still throttled — or a plain
      // click on the knob, which must NOT mark the preset unsaved or write a
      // value the device already holds.
      if (commit && pending.current) write()
      return
    }

    const moved = lanesNow.map((x) =>
      x.physical ? { ...x, db: Math.round((x.db + delta) * 10) / 10 } : x
    )
    liveLanes.current = moved
    setPreset((p) => (p ? { ...p, lanes: moved } : p))
    mark(true)

    if (timer.current) { clearTimeout(timer.current); timer.current = null }
    if (commit || Date.now() - writeAt.current > WRITE_MS) write()
    else { pending.current = true; timer.current = setTimeout(write, WRITE_MS) }
  }, [write, mark])

  const trim = (row: number, delta: number): void => {
    const cur = liveLanes.current.find((x) => x.row === row)
    if (!cur) return
    const next = Math.round(clamp(cur.db + delta, MIN_DB, MAX_DB) * 10) / 10
    liveLanes.current = liveLanes.current.map((x) => (x.row === row ? { ...x, db: next } : x))
    setPreset((p) => (p ? { ...p, lanes: liveLanes.current } : p))
    mark(true)
    void window.patchbay.leveling.level(row, next).catch(() => undefined)
  }

  const save = useCallback((): void => {
    if (!preset || !dirtyRef.current) return
    void guard('save', async () => {
      const r = await window.patchbay.leveling.save(preset.name)
      mark(false)
      say(t('lvl.saved', { name: r.name }))
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preset, mark])

  /** Re-read the device so the number on screen is the device's, not ours. */
  const reconcile = useCallback((): void => {
    window.patchbay.leveling
      .state()
      .then((p) => { liveLanes.current = p.lanes; setPreset(p) })
      .catch(() => undefined)
  }, [])

  /**
   * Follow the device.
   *
   * The bench shares its session with Cortex Control and Claude, so the loaded
   * preset can change under it — a stale level is worse than no level in a tool
   * whose entire job is telling you what the device is set to. Held off while a
   * write is in flight or a gesture is running, so it never fights the user.
   */
  useEffect(() => {
    if (!live || focus < 0) return
    const t = setInterval(() => {
      if (busy || dirtyRef.current || timer.current) return
      if (Date.now() - writeAt.current < 1500) return
      reconcile()
    }, 6000)
    return () => clearInterval(t)
  }, [live, focus, busy, reconcile])

  const commit = (db: number): void => {
    applyLevel(db, true)
    if (!dirtyRef.current) return   // the gesture changed nothing: don't save, don't re-read
    if (autoSave) setTimeout(save, 250)
    else setTimeout(reconcile, 350)
  }

  const drop = (index: number): void => {
    void saveBench(bench.filter((_, i) => i !== index))
    if (focus >= bench.length - 1) setFocus(Math.max(0, bench.length - 2))
  }

  const add = (slots: BenchSlot[]): void => {
    void saveBench([...bench, ...slots.filter(
      (s) => !bench.some((b) => slotId(b) === slotId(s))
    )])
    setPicking(false)
  }

  // ── keyboard: the reason this tool exists ─────────────────────────────

  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      const el = e.target as HTMLElement
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA')) return
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); save(); return }
      if (e.metaKey || e.ctrlKey || e.altKey) return

      if (e.key === 'ArrowLeft') { e.preventDefault(); goto(focus <= 0 ? bench.length - 1 : focus - 1) }
      else if (e.key === 'ArrowRight') { e.preventDefault(); goto(focus + 1 >= bench.length ? 0 : focus + 1) }
      else if (e.key === 'ArrowUp') { e.preventDefault(); pickScene(Math.max(0, (preset?.scene ?? 0) - 1)) }
      else if (e.key === 'ArrowDown') { e.preventDefault(); pickScene(Math.min(7, (preset?.scene ?? 0) + 1)) }
      else if (e.key === '-' || e.key === '_') { e.preventDefault(); commit(level - (e.shiftKey ? 0.1 : 0.5)) }
      else if (e.key === '=' || e.key === '+') { e.preventDefault(); commit(level + (e.shiftKey ? 0.1 : 0.5)) }
      else {
        const s = SCENES.indexOf(e.key.toUpperCase())
        if (s >= 0) { e.preventDefault(); pickScene(s) }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  // ── render ────────────────────────────────────────────────────────────

  if (!live) {
    return (
      <div className="view">
        <div className="empty">
          <h2>{t('lvl.needSession')}</h2>
          <p className="fine">{t('lvl.needSessionBody')}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="view lvl">
      <div className="lvl-bar">
        <div>
          <h2>{t('lvl.title')}</h2>
          <div className="fine"><T k="lvl.intro" /></div>
        </div>
        <span className="grow" />
        <label className="lvl-auto" title={t('lvl.autosaveTitle')}>
          <input
            type="checkbox"
            checked={autoSave}
            onChange={(e) => void act(() => window.patchbay.setPrefs({ benchAutoSave: e.target.checked }))}
          />
          {t('lvl.autosave')}
        </label>
        <Button size="sm" onClick={() => setPicking(true)}>{t('lvl.addPreset')}</Button>
      </div>

      {error && (
        <div className="strip bad lvl-err">
          <span className="grow">{error}</span>
          <Button size="sm" onClick={() => setError(null)}>{t('lvl.dismiss')}</Button>
        </div>
      )}

      {bench.length === 0 ? (
        <div className="empty">
          <h2>{t('lvl.emptyTitle')}</h2>
          <p className="fine">{t('lvl.emptyBody')}</p>
          <Button onClick={() => setPicking(true)}>{t('lvl.addPreset')}</Button>
        </div>
      ) : (
        <div className="lvl-strip">
          {bench.map((b, i) => {
            const on = i === focus
            const p = on ? preset : null
            const db = on ? level : null
            return (
              <article
                key={slotId(b)}
                className={on ? 'lvl-col on' : 'lvl-col'}
                onClick={() => goto(i)}
              >
                <header>
                  <StatusDot tone={on ? 'ok' : 'idle'} />
                  <span className="lvl-name" title={b.name}>{b.name}</span>
                  <button className="lvl-x" onClick={(e) => { e.stopPropagation(); drop(i) }}
                    aria-label={t('lvl.remove', { name: b.name })}>×</button>
                </header>

                <div className="lvl-db">
                  <span>{db === null ? <span className="off">—</span> : `${show(db)} dB`}</span>
                  {on && dirty && <Badge className="attn">{t('lvl.unsaved')}</Badge>}
                </div>

                {/* meter beside the knob: stacked, a 150px meter plus a knob
                    pushes the scenes off the bottom of an 716pt window */}
                <div className="lvl-body">
                  <Meter
                    outputs={on ? meter : null}
                    peak={peaks[slotId(b)] ?? null}
                    live={on}
                  />
                  <Knob
                    db={db ?? 0}
                    min={MIN_DB}
                    max={MAX_DB}
                    size={76}
                    disabled={!on || !lanes.length || busy !== null}
                    onChange={(v) => applyLevel(v, false)}
                    onCommit={commit}
                    label={t('lvl.level', { name: b.name })}
                  />
                </div>

                <div className="lvl-lanes">
                  {!p && <div className="lvl-lanes-hint">{t('lvl.clickToLoad')}</div>}
                  {p && Array.from({ length: LANE_SLOTS }, (_, k) => {
                    const l = p?.lanes[k]
                    if (!l) return <div key={k} className="lvl-lane empty" aria-hidden />
                    return (
                      <div key={l.row} className={l.active ? 'lvl-lane' : 'lvl-lane off'}>
                        <span className="lvl-out">{l.out}</span>
                        <span className="mono">{l.active ? `${show(l.db)} dB` : t('lvl.silent')}</span>
                        {l.active && (
                          <span className="lvl-trim">
                            <button onClick={(e) => { e.stopPropagation(); trim(l.row, -0.5) }}
                              aria-label={t('lvl.down', { out: l.out })}>−</button>
                            <button onClick={(e) => { e.stopPropagation(); trim(l.row, +0.5) }}
                              aria-label={t('lvl.up', { out: l.out })}>+</button>
                          </span>
                        )}
                      </div>
                    )
                  })}
                </div>

                <div className="lvl-scenes">
                  {SCENES.map((s, si) => {
                    const label = p?.sceneLabels?.[si] || ''
                    const used = Boolean(label)
                    return (
                      <button
                        key={s}
                        className={
                          (on && p?.scene === si ? 'lvl-scene on' : 'lvl-scene') +
                          (on && !used ? ' empty' : '')
                        }
                        disabled={!on}
                        title={label || t('lvl.scene', { s })}
                        onClick={(e) => { e.stopPropagation(); pickScene(si) }}
                      >
                        <b>{s}</b>
                        <em>{on ? (label || '—') : (b.scene === si ? '·' : '')}</em>
                      </button>
                    )
                  })}
                </div>

                <Button
                  size="sm"
                  onClick={(e) => { e.stopPropagation(); save() }}
                  disabled={!on || !dirty || busy !== null}
                >
                  {busy === 'save' && on ? t('lvl.saving') : t('lvl.save')}
                </Button>
              </article>
            )
          })}

          <button className="lvl-add" onClick={() => setPicking(true)} aria-label={t('aria.addPreset')}>
            <span>+</span>
            {t('lvl.addPresetPlain')}
          </button>
        </div>
      )}

      <div className="lvl-keys fine">
        <kbd>←</kbd><kbd>→</kbd> {t('keys.preset')} · <kbd>↑</kbd><kbd>↓</kbd> {t('keys.scene')} ·
        <kbd>A</kbd>–<kbd>H</kbd> {t('keys.jump')} · <kbd>−</kbd><kbd>+</kbd> {t('keys.level')}
        (<kbd>⇧</kbd> {t('keys.fine')}) · <kbd>{isMac(snap) ? '⌘S' : 'Ctrl+S'}</kbd> {t('keys.save')}
      </div>

      {picking && (
        <PresetPicker
          onClose={() => setPicking(false)}
          onAdd={add}
        />
      )}
    </div>
  )
}
