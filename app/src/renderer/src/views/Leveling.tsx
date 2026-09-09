import { useCallback, useEffect, useRef, useState } from 'react'
import { Badge, Button, StatusDot } from '@singz/ui'
import type {
  AutoStep, BenchSlot, LevelEvent, MeterOutput, PresetState, ReportRow, SceneRow,
  Snapshot
} from '@shared/types'
import { cleanError } from '../derive.js'
import {
  attribute, appliedIds, focusIndex, forget, forgetApplied, isDirty, leave, markSaved, note,
  slotId, unsavedIds
} from '../bench.js'
import type { Written, WrittenMap } from '../bench.js'
import { SHORTCUTS, matches, shortcut, typing } from '../keys.js'
import { act, say } from '../store.js'
import { T, t } from '../i18n.js'
import { Knob } from '../components/Knob.js'
import { Meter, loudest } from '../components/Meter.js'
import { PresetPicker } from '../modals/PresetPicker.js'
import { Measured } from '../components/Measured.js'
import { LevelReport } from '../components/LevelReport.js'
import { Scenes } from '../components/Scenes.js'
import { Dock } from '../components/Dock.js'
import { LevelingHelp } from '../modals/LevelingHelp.js'
import { Shortcuts } from '../modals/Shortcuts.js'

/** The last useful sentence of whatever went wrong, never the traceback. */
const cleanish = (m: string): string => cleanError(m) ?? m

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

interface Run { kind: 'measure' | 'apply' | 'save' | 'audition'; at?: string }
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

  /** The slot whose preset is loaded on the device; null = none of ours, so no
   *  column claims to be live. An id, not an index: two Downloads presets share
   *  position 0 and a dropped column must not hand its focus to a neighbour. */
  const [focusId, setFocusId] = useState<string | null>(null)
  const focus = focusIndex(bench, focusId)
  const focusRef = useRef<string | null>(null)
  focusRef.current = focusId
  const [preset, setPreset] = useState<PresetState | null>(null)
  const [meter, setMeter] = useState<Record<string, MeterOutput> | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  /**
   * Everything this session wrote to the device that a preset file does not
   * hold yet, by slot id — the by-ear knob, an Apply, a scene pass. One record,
   * one yellow dot: `dirty`, `applied` and `saved` used to be three states that
   * meant overlapping things and were never reconciled.
   */
  const [written, setWritten] = useState<WrittenMap>({})
  /** …and as a ref: auto-save fires from inside the same gesture that sets it,
   *  so a closure would still be reading the pre-change value. */
  const writtenRef = useRef<WrittenMap>({})
  const putWritten = useCallback((f: (w: WrittenMap) => WrittenMap): void => {
    writtenRef.current = f(writtenRef.current)
    setWritten(writtenRef.current)
  }, [])
  /** The loaded preset now differs from its file — or, `false`, was just saved. */
  const mark = useCallback((v: boolean, source: Written['source'] = 'ear'): void => {
    const id = focusRef.current
    if (id === null) return
    putWritten((w) => (v ? note(w, id, null, source) : markSaved(w, id)))
  }, [putWritten])
  const dirtyNow = (): boolean => isDirty(writtenRef.current, focusRef.current)
  const dirty = isDirty(written, focusId)
  const [picking, setPicking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  /** Loudest dB seen per slot this session. Deliberately not persisted: it
   *  describes a performance, not the preset. */
  const [peaks, setPeaks] = useState<Record<string, number>>({})
  /** The newest pass of a measured run, so the strip can be watched rather than
   *  sat in front of: every pass replays the whole riff. */
  const [autoStep, setAutoStep] = useState<AutoStep | null>(null)
  const [target, setTarget] = useState(-18)
  /** The report, keyed by slot id. Empty until Measure all is pressed. */
  const [rows, setRows] = useState<Record<string, ReportRow>>({})
  /** What the running measurement was sent, and which index it is on: the
   *  service names a row only by position, and Downloads all have position 0. */
  const sent = useRef<BenchSlot[]>([])
  const runIndex = useRef<number | null>(null)
  const [measuring, setMeasuring] = useState<string | null>(null)
  /** What will be written, by slot id — seeded from the measurement and
   *  overwritten the moment somebody disagrees with it. What actually reached
   *  the device is `written`. */
  const [proposals, setProposals] = useState<Record<string, number>>({})
  const [chosen, setChosen] = useState<string[] | null>(null)
  const [helping, setHelping] = useState(false)
  const [scenes, setScenes] = useState<Record<number, SceneRow>>({})
  const [sceneBusy, setSceneBusy] = useState(false)
  const [sceneAt, setSceneAt] = useState<number | null>(null)
  const [sceneSel, setSceneSel] = useState<number[]>([0, 1, 2, 3, 4, 5, 6, 7])
  const [hpLimit, setHpLimit] = useState(false)
  /** Set by Stop; the run checks it between presets. A run used to be five
   *  presets of nothing you could do, and realising at the second that the
   *  riff was wrong meant waiting out three more. */
  const abort = useRef(false)
  const [runAt, setRunAt] = useState<{ n: number; total: number } | null>(null)
  /** The one long-running thing, if any. `at` is the slot an audition is on. */
  const [run, setRun] = useState<Run | null>(null)
  const busyAll = run !== null && run.kind !== 'audition'
  const auditing = run?.kind === 'audition' ? run.at ?? null : null
  const [keysOpen, setKeysOpen] = useState(false)
  /**
   * One playback model. `playing` goes true when we ask and comes back false
   * ONLY on the service's `play` event: `do_play` returns the moment it has
   * spawned its thread, so awaiting the call said nothing about the sound.
   * Two flags used to disagree about this — `P` twice started two playbacks
   * and the Stop button was `disabled={playing}`, so it could not be pressed.
   */
  const [playing, setPlaying] = useState(false)
  const playWaiters = useRef<Array<() => void>>([])
  /** Resolves when the riff has finished (or failed) — the `play` event. */
  const played = (): Promise<void> => new Promise((res) => playWaiters.current.push(res))
  /** What the space bar does right now: the recorder's foot, owned by Measured. */
  const foot = useRef<(() => void) | null>(null)

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
      if (e.event === 'meter') { setMeter(e.outputs); setHpLimit(Boolean(e.hp_limit)) }
      else if (e.event === 'autolevel') setAutoStep(e.step)
      else if (e.event === 'measuring') {
        setMeasuring(e.name)
        runIndex.current = e.index
        setRunAt({ n: e.index + 1, total: e.total })
      }
      else if (e.event === 'measured') {
        const id = attribute(sent.current, runIndex.current, e.row)
        if (id === null) setError(t('lvl.unattributed', { name: e.row.name ?? String(e.row.position) }))
        else setRows((r) => ({ ...r, [id]: e.row }))
      } else if (e.event === 'cancelled') {
        setRunAt(null)
        say(t('lvl.stopped', { n: String(e.done), total: String(e.total) }), false)
      } else if (e.event === 'play') {
        setPlaying(false)
        if (e.error) setError(cleanish(e.error))
        const w = playWaiters.current; playWaiters.current = []
        w.forEach((f) => f())
      }
      else if (e.event === 'scene_measuring') setSceneAt(e.scene)
      else if (e.event === 'scene_measured') {
        setScenes((r) => ({ ...r, [e.row.scene]: e.row }))
      }
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
        if (i >= 0) { setFocusId(slotId(bench[i])); setPreset(p) }
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
      // The recall reloaded the device from flash: a by-ear trim on the preset
      // we just left lived only in the working grid, and is gone. Say so.
      const left = leave(writtenRef.current, focusRef.current)
      if (left.lost) {
        putWritten(() => left.next)
        say(t('lvl.lostEdit', { name: slot?.name ?? '' }), true)
      }
      setFocusId(slotId(target))
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
    if (!preset || !dirtyNow()) return
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
      if (busy || dirtyNow() || timer.current) return
      if (Date.now() - writeAt.current < 1500) return
      reconcile()
    }, 6000)
    return () => clearInterval(t)
  }, [live, focus, busy, reconcile])

  const commit = (db: number): void => {
    applyLevel(db, true)
    if (!dirtyNow()) return   // the gesture changed nothing: don't save, don't re-read
    if (autoSave) setTimeout(save, 250)
    else setTimeout(reconcile, 350)
  }

  const drop = (index: number): void => {
    const gone = bench[index]
    if (!gone) return
    void saveBench(bench.filter((_, i) => i !== index))
    // A dropped focus is no focus: the preset is still loaded, but showing its
    // lanes under a neighbour's name would be a lie.
    putWritten((w) => forget(w, slotId(gone)))
  }

  const add = (slots: BenchSlot[]): void => {
    void saveBench([...bench, ...slots.filter(
      (s) => !bench.some((b) => slotId(b) === slotId(s))
    )])
    setPicking(false)
  }

  /**
   * An error belongs on the row that produced it.
   *
   * Every failure path used to call setError on one string, so a run that
   * failed on three presets showed the last one and lost the other two — in a
   * bar at the bottom of the page, away from the thing that caused it.
   */
  const rowFail = useCallback((b: BenchSlot, why: string): void => {
    const id = slotId(b)
    setRows((r) => ({
      ...r, [id]: { ...(r[id] ?? { position: b.position, name: b.name }), error: why }
    }))
  }, [])

  /** Play the riff through the preset in front of you, or stop it. */
  const togglePlay = useCallback(async (): Promise<void> => {
    try {
      // Stop clears nothing itself: the service's `play` event does, once the
      // stream has actually stopped.
      if (playing) { await window.patchbay.leveling.sampleStopPlay(); return }
      setPlaying(true)
      await window.patchbay.leveling.samplePlay()
    } catch (e) {
      setPlaying(false)
      setError(cleanish((e as Error).message))
    }
  }, [playing])

  // ── the report's actions, as functions ────────────────────────────────
  //
  // Named rather than inline, because the keyboard has to reach the same ones
  // the buttons do. Every core action in this view was mouse-only, on a screen
  // whose whole premise is that your hands are busy.

  const wanted = useCallback(
    (): string[] => chosen ?? bench.map(slotId), [chosen, bench])

  const measureAll = useCallback((): void => {
    if (busyAll || bench.length === 0) return
    abort.current = false
    const want = wanted()
    const list = bench.filter((b) => want.includes(slotId(b)))
    sent.current = list
    runIndex.current = null
    setRun({ kind: 'measure' }); setRows({}); setProposals({}); setError(null)
    setRunAt({ n: 0, total: list.length })
    void window.patchbay.leveling
      .measureMany(
        list.map((b) => ({
          folder_key: b.folderKey, position: b.position, name: b.name, cloud_id: b.cloudId
        })),
        { target }
      )
      .catch((e: Error) => setError(cleanish(e.message)))
      .finally(() => { setRun(null); setMeasuring(null); setRunAt(null) })
  }, [busyAll, bench, wanted, target])

  const applyAll = useCallback((): void => {
    if (busyAll) return
    // Clear the flag a previous Stop left set, or this run gives up on its
    // first preset for a reason nobody can see.
    abort.current = false
    const want = wanted()
    setRun({ kind: 'apply' })
    void (async () => {
      for (const b of bench.filter((x) => want.includes(slotId(x)))) {
        if (abort.current) break
        const id = slotId(b)
        const r = rows[id]
        const db = proposals[id] ?? r?.correction_db
        if (db === null || db === undefined) continue
        try {
          const res = await window.patchbay.leveling.applyTrim({
            folderKey: b.folderKey, position: b.position,
            isFactory: false, cloudId: b.cloudId, row: r?.row, db
          })
          if (res.error) { rowFail(b, res.error); continue }
          putWritten((w) => note(w, id, res.applied_db, 'apply'))
          if (res.limited_by === 'range') {
            rowFail(b,
              `fader ran out — ${res.applied_db.toFixed(1)} of ${db.toFixed(1)} dB given`)
          }
        } catch (e) { rowFail(b, cleanish((e as Error).message)) }
      }
      setRun(null)
      void window.patchbay.leveling.state().then(setPreset).catch(() => undefined)
    })()
  }, [busyAll, bench, wanted, rows, proposals, rowFail, putWritten])

  const saveAll = useCallback((): void => {
    const pending = unsavedIds(written)
    if (pending.length === 0 || busyAll) return
    setRun({ kind: 'save' })
    void (async () => {
      for (const b of bench.filter((x) => pending.includes(slotId(x)))) {
        try {
          await window.patchbay.leveling.open(b.folderKey, b.position, false, b.cloudId)
          await window.patchbay.leveling.save()
          putWritten((w) => markSaved(w, slotId(b)))
        } catch (e) { rowFail(b, cleanish((e as Error).message)) }
      }
      setRun(null)
    })()
  }, [written, busyAll, bench, rowFail, putWritten])

  const undoAll = useCallback((): void => {
    if (appliedIds(written).length === 0) return
    void window.patchbay.leveling
      .revertLevels()
      .then(() => {
        putWritten(forgetApplied)
        return window.patchbay.leveling.state().then(setPreset)
      })
      .catch((e: Error) => setError(cleanish(e.message)))
  }, [written, putWritten])

  /**
   * Listen to the set, one preset after another.
   *
   * The reason anyone levels a setlist is so it sounds even, and until now
   * there was no way to hear whether it did.
   */
  const audition = useCallback((): void => {
    if (auditing !== null) { abort.current = true; return }
    if (bench.length === 0) return
    abort.current = false
    const want = wanted()
    const list = bench.filter((b) => want.includes(slotId(b)))
    void (async () => {
      for (const b of list) {
        if (abort.current) break
        setRun({ kind: 'audition', at: slotId(b) })
        try {
          await window.patchbay.leveling.open(b.folderKey, b.position, false, b.cloudId)
          // Wait for the riff to END, not for the call to return — do_play
          // answers as soon as its thread is up, so without this the loop
          // recalled the next preset a few hundred ms into each one.
          const done = played()
          setPlaying(true)
          try { await window.patchbay.leveling.samplePlay() }
          catch (e) { setPlaying(false); playWaiters.current = []; throw e }
          await done
        } catch (e) { rowFail(b, cleanish((e as Error).message)); break }
      }
      setRun(null)
      abort.current = false
    })()
  }, [auditing, bench, wanted, rowFail])

  const stopRun = useCallback((): void => {
    if (!busyAll && auditing === null) return
    // `abort` stops the loops THIS side runs — apply, and the audition walk.
    // A measurement is one call that loops on the bench, so it has to be told.
    abort.current = true
    void window.patchbay.leveling.cancel().catch(() => undefined)
    if (auditing !== null) void window.patchbay.leveling.sampleStopPlay().catch(() => undefined)
    say(t('lvl.stopping'), false)
  }, [busyAll, auditing])

  // ── keyboard: the reason this tool exists ─────────────────────────────

  useEffect(() => {
    const key = shortcut
    const onKey = (e: KeyboardEvent): void => {
      if (typing(e) || e.repeat) return

      if (matches(e, key('keys.record'))) { e.preventDefault(); foot.current?.(); return }
      if (matches(e, key('keys.play'))) { e.preventDefault(); void togglePlay(); return }
      if (matches(e, key('keys.measure'))) { e.preventDefault(); measureAll(); return }
      if (matches(e, key('keys.listen'))) { e.preventDefault(); audition(); return }
      if (matches(e, key('keys.stopRun'))) { e.preventDefault(); stopRun(); return }
      if (matches(e, key('keys.apply'))) { e.preventDefault(); applyAll(); return }
      if (matches(e, key('keys.undo'))) { e.preventDefault(); undoAll(); return }
      if (matches(e, key('keys.saveAll'))) { e.preventDefault(); saveAll(); return }
      if (matches(e, key('keys.saveOne'))) { e.preventDefault(); save(); return }
      if (matches(e, key('keys.addPreset'))) { e.preventDefault(); setPicking(true); return }
      if (e.metaKey || e.ctrlKey || e.altKey) return

      if (e.key === 'ArrowLeft') { e.preventDefault(); goto(focus <= 0 ? bench.length - 1 : focus - 1) }
      else if (e.key === 'ArrowRight') { e.preventDefault(); goto(focus + 1 >= bench.length ? 0 : focus + 1) }
      else if (e.key === 'ArrowUp') { e.preventDefault(); pickScene(Math.max(0, (preset?.scene ?? 0) - 1)) }
      else if (e.key === 'ArrowDown') { e.preventDefault(); pickScene(Math.min(7, (preset?.scene ?? 0) + 1)) }
      else if (e.key === '-' || e.key === '_') { e.preventDefault(); commit(level - (e.shiftKey ? 0.1 : 0.5)) }
      else if (e.key === '=' || e.key === '+') { e.preventDefault(); commit(level + (e.shiftKey ? 0.1 : 0.5)) }
      else if (/^[1-9]$/.test(e.key)) {
        // Straight to a slot. Arrows only walk, so the fifth preset was four
        // presses away.
        const i = Number(e.key) - 1
        if (i < bench.length) { e.preventDefault(); goto(i) }
      } else {
        const s2 = SCENES.indexOf(e.key.toUpperCase())
        if (s2 >= 0) { e.preventDefault(); pickScene(s2) }
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
        <Button size="sm" onClick={() => setHelping(true)}>{t('lvl.howThisWorks')}</Button>
        {/* A run you can watch and stop. Five presets used to be seventy-five
            seconds of nothing you could do, with a name as the only sign of
            life. */}
        {(busyAll || auditing !== null) && (
          <span className="lvl-run">
            <span className="lvl-run-dot" />
            {auditing !== null
              ? t('lvl.listening')
              : runAt
                ? t('lvl.measuringOf', {
                    n: String(runAt.n), total: String(runAt.total),
                    name: measuring ? ` · ${measuring}` : ''
                  })
                : t('lvl.working')}
            <button type="button" onClick={stopRun}>{t('lvl.stop')}</button>
          </span>
        )}
        <Button size="sm" disabled={bench.length === 0 || busyAll}
                onClick={audition} title={t('lvl.listenHint')}>
          {auditing !== null ? t('lvl.stop') : t('lvl.listenToSet')}
        </Button>
        <Button size="sm" onClick={() => setPicking(true)}>{t('lvl.addPreset')}</Button>
      </div>

      <Measured
        live={live}
        presetName={preset?.name ?? slot?.name ?? null}
        target={target}
        onTarget={setTarget}
        step={autoStep}
        playing={playing}
        onPlay={() => void togglePlay()}
        foot={foot}
        onTrimmed={() => {
          // The run moved the fader on the device; re-read so the bench agrees.
          setAutoStep(null)
          void window.patchbay.leveling.state().then(setPreset).catch(() => undefined)
          mark(true, 'auto')
        }}
      />

      <LevelReport
        slots={bench}
        rows={rows}
        target={target}
        metric="lufs"
        busy={busyAll}
        progress={measuring}
        written={written}
        proposals={proposals}
        selected={chosen ?? bench.map(slotId)}
        onPropose={(id, db) => setProposals((p) => ({ ...p, [id]: db }))}
        onNudge={(id, by) => setProposals((p) => {
          // Functional, and against whatever is in there now: two clicks in one
          // tick have to be two steps.
          const base = p[id] ?? rows[id]?.correction_db ?? 0
          return { ...p, [id]: Math.round((base + by) * 10) / 10 }
        })}
        onResetProposal={(id) => setProposals((p) => {
          const { [id]: _gone, ...rest } = p
          return rest
        })}
        onToggle={(id) => {
          const now = chosen ?? bench.map(slotId)
          setChosen(now.includes(id) ? now.filter((p) => p !== id) : [...now, id])
        }}
        onUndoOne={(id) => {
          const b = bench.find((x) => slotId(x) === id)
          const db = written[id]?.db
          if (!b || db === null || db === undefined) return
          void (async () => {
            try {
              await window.patchbay.leveling.applyTrim({
                folderKey: b.folderKey, position: b.position,
                isFactory: false, cloudId: b.cloudId, row: rows[id]?.row, db: -db
              })
              putWritten((w) => forget(w, id))
            } catch (e) { rowFail(b, cleanish((e as Error).message)) }
          })()
        }}
        onMeasure={measureAll}
        onApply={applyAll}
        onSave={saveAll}
        onRevert={undoAll}
      />

      {/* Two tables that look alike and answer different questions. */}
      <p className="lvl-divider fine">{t('lvl.divider')}</p>

      <Scenes
        rows={scenes}
        busy={sceneBusy}
        progress={sceneAt}
        selected={sceneSel}
        presetName={preset?.name ?? slot?.name ?? null}
        onToggle={(sc) =>
          setSceneSel((v) => (v.includes(sc) ? v.filter((x) => x !== sc) : [...v, sc]))}
        onMeasure={() => {
          setSceneBusy(true); setScenes({}); setError(null)
          void window.patchbay.leveling
            .measureScenes({ target, scenes: sceneSel })
            .catch((e: Error) => setError(e.message))
            .finally(() => { setSceneBusy(false); setSceneAt(null) })
        }}
        onApply={() => {
          setSceneBusy(true)
          void window.patchbay.leveling
            .levelScenes({ target, scenes: sceneSel })
            .then(() => window.patchbay.leveling.state().then(setPreset))
            .catch((e: Error) => setError(e.message))
            .finally(() => { setSceneBusy(false); mark(true, 'scenes') })
        }}
      />

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

      <Dock outputs={meter} hpLimit={hpLimit} />

      {/* Written from keys.ts, not by hand. The hand-written version listed
          five bindings and left out `space`, which is the one you use most and
          the only one that works with both hands on the guitar. */}
      <div className="lvl-keys fine">
        {SHORTCUTS.filter((k) => k.scope === 'leveling' && !k.local).slice(0, 7).map((k) => (
          <span key={k.cap}>
            {k.cap.split(' ').map((c) => <kbd key={c}>{c}</kbd>)}
            {' '}{t(k.short)}
          </span>
        ))}
        <button type="button" className="lvl-keys-all" onClick={() => setKeysOpen(true)}>
          {t('keys.all')} <kbd>?</kbd>
        </button>
      </div>

      {helping && <LevelingHelp onClose={() => setHelping(false)} />}
      {keysOpen && <Shortcuts onClose={() => setKeysOpen(false)} />}
      {picking && (
        <PresetPicker
          onClose={() => setPicking(false)}
          onAdd={add}
        />
      )}
    </div>
  )
}
