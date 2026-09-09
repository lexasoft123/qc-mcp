import { useCallback, useEffect, useRef, useState } from 'react'
import { Button } from '@singz/ui'
import type {
  AutoStep, BenchSlot, LevelEvent, MeterOutput, PresetState, ReportRow, SceneRow, Snapshot
} from '@shared/types'
import { cleanError } from '../derive.js'
import {
  attribute, appliedIds, focusIndex, forget, forgetApplied, isDirty, leave, markSaved, nextStep,
  note, savePlan, slotId, unsavedIds
} from '../bench.js'
import type { Written, WrittenMap } from '../bench.js'
import { MOD, matches, shortcut, typing } from '../keys.js'
import { act, say } from '../store.js'
import { t } from '../i18n.js'
import { loudest } from '../components/Meter.js'
import { PresetPicker } from '../modals/PresetPicker.js'
import { RiffTransport } from '../components/RiffTransport.js'
import { useSampler } from '../sampler.js'
import { BenchTable } from '../components/BenchTable.js'
import { RowDrawer } from '../components/RowDrawer.js'
import { InputRail } from '../components/InputRail.js'
import { Dock } from '../components/Dock.js'
import { LevelingHelp } from '../modals/LevelingHelp.js'
import { Shortcuts } from '../modals/Shortcuts.js'

/** The last useful sentence of whatever went wrong, never the traceback. */
const cleanish = (m: string): string => cleanError(m) ?? m

const SCENES = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
const TARGETS = [-14, -16, -18, -20, -23]
/** The lane output range, calibrated against Cortex Control. */
const MIN_DB = -40
const MAX_DB = 12
/** While dragging, write no faster than this — you level by ear, so the device
 *  has to follow the knob, but every pointermove would flood the session. */
const WRITE_MS = 80

const clamp = (v: number, lo: number, hi: number): number => Math.max(lo, Math.min(hi, v))

interface Run { kind: 'measure' | 'apply' | 'save' | 'audition'; at?: string }
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
  /** Where each slot's fader was when it was last loaded, so a row keeps its
   *  "now" after the focus moves on. Not persisted: a session's memory. */
  const [lastLevel, setLastLevel] = useState<Record<string, number>>({})
  const [drawerOpen, setDrawerOpen] = useState(true)
  /** A recall in flight, and towards which slot — its row shows a skeleton. */
  const [pendingId, setPendingId] = useState<string | null>(null)
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

  const live = snap.daemon.state === 'running'
  /** The recorder. Owned here, not by the transport that draws it: the input
   *  rail follows the DI while it is armed or rolling, and the drawer's tools
   *  need to know a take exists. */
  const sampler = useSampler(live)
  const slot: BenchSlot | undefined = bench[focus]
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

  /** Remember the focused preset's level, so its row still says so later. */
  useEffect(() => {
    if (focusId === null || !preset) return
    setLastLevel((m) => (m[focusId] === level ? m : { ...m, [focusId]: level }))
  }, [focusId, preset, level])

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
    setPendingId(slotId(target))
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
    }).finally(() => setPendingId(null))
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

  /**
   * Save every unsaved trim — actually save it.
   *
   * `open` then `save` wrote nothing: a trim lives in the working grid, the
   * recall reloaded the file over it, and the file was saved onto itself.
   * `savePlan` says what each slot needs: the loaded preset saves as it
   * stands (first — its edits die at the next recall); an Apply trim elsewhere
   * is applied AGAIN after its recall, then saved.
   */
  const saveAll = useCallback((): void => {
    const plan = savePlan(writtenRef.current, focusRef.current, bench.map(slotId))
    if (plan.length === 0 || busyAll) return
    setRun({ kind: 'save' })
    void (async () => {
      let landed: BenchSlot | null = null
      for (const a of plan) {
        const b = bench.find((x) => slotId(x) === a.id)
        if (!b) continue
        try {
          if (a.how === 'reapply') {
            await window.patchbay.leveling.open(b.folderKey, b.position, false, b.cloudId)
            landed = b
            const res = await window.patchbay.leveling.applyTrim({
              folderKey: b.folderKey, position: b.position, isFactory: false, cloudId: b.cloudId,
              row: rows[a.id]?.row, db: a.db
            })
            if (res.error) { rowFail(b, res.error); continue }
          }
          await window.patchbay.leveling.save()
          putWritten((w) => markSaved(w, a.id))
        } catch (e) { rowFail(b, cleanish((e as Error).message)) }
      }
      // the device is on whichever preset was recalled last; follow it
      if (landed) {
        setFocusId(slotId(landed))
        void window.patchbay.leveling.state().then((p) => { liveLanes.current = p.lanes; setPreset(p) }).catch(() => undefined)
      }
      setRun(null)
    })()
  }, [busyAll, bench, rows, rowFail, putWritten])

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

      if (matches(e, key('keys.record'))) { e.preventDefault(); sampler.foot(); return }
      if (matches(e, key('keys.play'))) { e.preventDefault(); void togglePlay(); return }
      if (matches(e, key('keys.measure'))) { e.preventDefault(); measureAll(); return }
      if (matches(e, key('keys.listen'))) { e.preventDefault(); audition(); return }
      if (matches(e, key('keys.stopRun'))) { e.preventDefault(); stopRun(); return }
      if (matches(e, key('keys.apply'))) { e.preventDefault(); applyAll(); return }
      if (matches(e, key('keys.undo'))) { e.preventDefault(); undoAll(); return }
      if (matches(e, key('keys.saveAll'))) { e.preventDefault(); saveAll(); return }
      if (matches(e, key('keys.saveOne'))) { e.preventDefault(); save(); return }
      if (matches(e, key('keys.addPreset'))) { e.preventDefault(); setPicking(true); return }
      if (matches(e, key('keys.openRow'))) { e.preventDefault(); if (focus >= 0) setDrawerOpen((v) => !v); return }
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
  //
  // Four bands down the right of a full-height input rail: a thin top bar, the
  // recorder, one row per preset with a drawer under the focused one, and the
  // outputs dock. Only the rows scroll. Every band whose content varies keeps
  // its size — that is what the rail, the dock's run cell and the drawer's
  // fixed height are for.

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

  const inLive = sampler.state === 'armed' || sampler.state === 'recording'
  const runText = auditing !== null
    ? t('lvl.listening')
    : busyAll
      ? runAt
        ? t('lvl.measuringOf', {
            n: String(runAt.n), total: String(runAt.total),
            name: measuring ? ` · ${measuring}` : ''
          })
        : t('lvl.working')
      : null
  const rowErrors = bench.map((b) => rows[slotId(b)]?.error).filter((x): x is string => Boolean(x))
  const footline = error ?? (rowErrors.length ? rowErrors[rowErrors.length - 1] : null)
  const nowDb: Record<string, number> = focusId !== null && preset
    ? { ...lastLevel, [focusId]: level }
    : lastLevel
  const selectedIds = chosen ?? bench.map(slotId)
  const primary = nextStep({
    hasTake: Boolean(sampler.sample?.path),
    measured: bench.filter((b) => rows[slotId(b)]?.measured !== undefined).length,
    selected: selectedIds.length,
    unsaved: unsavedIds(written).filter((id) => bench.some((b) => slotId(b) === id)).length,
    running: busyAll || auditing !== null
  })

  return (
    <div className="view lvl">
      <InputRail
        inDbfs={inLive ? sampler.sample?.input_dbfs ?? null : null}
        outDb={meter ? loudest(meter) : null}
        armed={sampler.state === 'armed'}
        thresholdDbfs={sampler.sample?.threshold_dbfs ?? -40}
        live={live}
      />

      <div className="lvl-main">
        <div className="lvl-top">
          <h2>{t('lvl.title')}</h2>
          <label className="lvl-target">
            <span className="eyebrow"><abbr title={t('msd.targetHint')}>{t('msd.target')}</abbr></span>
            <select value={target} onChange={(e) => setTarget(Number(e.target.value))} disabled={busyAll}
                    aria-label={t('msd.target')}>
              {TARGETS.map((v) => <option key={v} value={v}>{v} LUFS</option>)}
            </select>
          </label>
          <span className="grow" />
          <Button size="sm" onClick={() => setPicking(true)}>{t('lvl.addPreset')} <kbd>{MOD}N</kbd></Button>
          <Button size="sm" variant="ghost" onClick={() => setHelping(true)}>{t('lvl.howThisWorks')}</Button>
          <Button size="sm" variant="ghost" onClick={() => setKeysOpen(true)}>{t('keys.all')} <kbd>?</kbd></Button>
        </div>

        <RiffTransport
          sampler={sampler}
          playing={playing}
          onPlay={() => void togglePlay()}
          presetName={preset?.name ?? slot?.name ?? null}
          running={busyAll || auditing !== null}
          primary={primary === 'rec'}
        />

        <BenchTable
          slots={bench}
          rows={rows}
          metric="lufs"
          busy={busyAll}
          progress={measuring}
          listening={auditing !== null}
          written={written}
          proposals={proposals}
          selected={selectedIds}
          focusId={focusId}
          primary={primary}
          pendingId={pendingId}
          drawerOpen={drawerOpen}
          nowDb={nowDb}
          meter={meter}
          autoSave={autoSave}
          footline={footline}
          onDismissFootline={() => {
            setError(null)
            // a row's error is dismissed off the row too, or it comes straight back
            setRows((r) => Object.fromEntries(Object.entries(r).map(([k, v]) => [k, { ...v, error: null }])))
          }}
          canSaveRow={(id) => id === focusId && dirty && busy === null}
          onAutoSave={(on) => void act(() => window.patchbay.setPrefs({ benchAutoSave: on }))}
          onToggle={(id) => {
            const now = chosen ?? bench.map(slotId)
            setChosen(now.includes(id) ? now.filter((p) => p !== id) : [...now, id])
          }}
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
          onSaveRow={(id) => { if (id === focusId) save() }}
          onFocus={(id) => goto(bench.findIndex((b) => slotId(b) === id))}
          onOpen={(id) => {
            // Never a recall the focus did not already pay for: the focused
            // row just toggles; another row's chevron IS the click that loads it.
            if (id === focusId) setDrawerOpen((v) => !v)
            else { setDrawerOpen(true); goto(bench.findIndex((b) => slotId(b) === id)) }
          }}
          onMeasure={measureAll}
          onListen={audition}
          onApply={applyAll}
          onSave={saveAll}
          onRevert={undoAll}
          onAdd={() => setPicking(true)}
          drawer={slot ? (
            <RowDrawer
              slot={slot}
              preset={preset}
              level={level}
              dirty={dirty}
              busy={busy !== null}
              saving={busy === 'save'}
              onLevel={applyLevel}
              onCommit={commit}
              onTrim={trim}
              onScene={pickScene}
              onSave={save}
              onRemove={() => drop(focus)}
              scenes={{
                rows: scenes, busy: sceneBusy, progress: sceneAt, selected: sceneSel,
                onToggle: (sc) =>
                  setSceneSel((v) => (v.includes(sc) ? v.filter((x) => x !== sc) : [...v, sc])),
                onMeasure: () => {
                  setSceneBusy(true); setScenes({}); setError(null)
                  void window.patchbay.leveling
                    .measureScenes({ target, scenes: sceneSel })
                    .catch((e: Error) => setError(e.message))
                    .finally(() => { setSceneBusy(false); setSceneAt(null) })
                },
                onApply: () => {
                  setSceneBusy(true)
                  void window.patchbay.leveling
                    .levelScenes({ target, scenes: sceneSel })
                    .then(() => window.patchbay.leveling.state().then(setPreset))
                    .catch((e: Error) => setError(e.message))
                    .finally(() => { setSceneBusy(false); mark(true, 'scenes') })
                }
              }}
              tools={{
                target, step: autoStep, ready: sampler.ready,
                onTrimmed: () => {
                  // The run moved the fader on the device; re-read so the bench agrees.
                  setAutoStep(null)
                  void window.patchbay.leveling.state().then(setPreset).catch(() => undefined)
                  mark(true, 'auto')
                }
              }}
            />
          ) : null}
        />

        <Dock outputs={meter} hpLimit={hpLimit}
              run={runText ? { text: runText, onStop: stopRun } : null} />
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
