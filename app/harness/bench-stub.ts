/*
 * The whole Leveling view, against a fake bench.
 *
 *   bench.html?s=fresh|measured|applied|error|empty|drawer&h=620|788|1000
 *
 * The stub answers every call the view makes and streams a meter, so the rail,
 * the rows and the dock move. `measured` presses M for you and the stub emits a
 * measurement per preset; `applied` then presses ⌘↵; `error` fails one preset;
 * `drawer` opens the focused row. Nothing here is typechecked by `npm run
 * typecheck` — build it (`npx vite build -c harness/vite.config.ts`) and look.
 */
/* The fake bench behind harness/bench.tsx. Its own module because the renderer
   store subscribes to `window.patchbay` at import time, so this has to have run
   before the view is imported — a static import from bench.tsx, listed first. */
import type { BenchSlot, LevelEvent, PresetState, ReportRow, SampleState } from '../src/shared/types'

const q = new URLSearchParams(location.search)
export const scenario = q.get('s') ?? 'fresh'
export const height = Number(q.get('h') ?? 788)

const SLOTS: BenchSlot[] = [
  { folderKey: 'f', position: 0, name: 'LONESTAR COOL', cloudId: '', scene: null },
  { folderKey: 'f', position: 2, name: 'SOLDANO', cloudId: '', scene: null },
  { folderKey: 'f', position: 6, name: 'NOLLY', cloudId: '', scene: 2 },
  { folderKey: 'f', position: 25, name: 'The Shadows', cloudId: '', scene: null },
  { folderKey: 'downloads', position: 0, name: 'Blackmore', cloudId: 'c-blackmore', scene: null }
]
const LEVELS: Record<string, number> = { 'f:0': -3.0, 'f:2': 1.5, 'f:6': 0.0, 'f:25': -1.0, 'c-blackmore': -0.5 }
const MEASURED: Record<string, Partial<ReportRow>> = {
  'f:0': { measured: -21.4, true_peak: -0.8, correction_db: 3.4 },
  'f:2': { measured: -18.2, true_peak: -1.2, correction_db: -0.2 },
  'f:6': { measured: -15.9, true_peak: -0.3, correction_db: -2.1 },
  'f:25': { measured: -24.0, true_peak: -3.1, correction_db: 6.0 },
  'c-blackmore': { measured: -19.6, true_peak: -1.9, correction_db: 1.6 }
}
const id = (b: { folderKey: string; position: number; cloudId: string }): string =>
  b.cloudId || `${b.folderKey}:${b.position}`

let loaded = SLOTS[2]
const presetOf = (b: BenchSlot): PresetState => ({
  name: b.name, folderKey: b.folderKey, position: b.cloudId ? null : b.position, isFactory: false,
  scene: b.scene ?? 0,
  sceneLabels: b.name === 'NOLLY' ? ['Clean', 'Crunch', 'Lead', '', '', '', '', ''] : ['', '', '', '', '', '', '', ''],
  lanes: [
    { row: 0, db: LEVELS[id(b)], pan: 0, mute: false, solo: false, outPortId: 19, out: 'Multi Out', active: true, physical: true, blocks: 6 },
    { row: 1, db: LEVELS[id(b)] - 6, pan: 0, mute: false, solo: false, outPortId: 3, out: 'Out 3/4', active: true, physical: true, blocks: 2 }
  ]
})

let listeners: ((e: LevelEvent) => void)[] = []
const emit = (e: LevelEvent): void => listeners.forEach((f) => f(e))
export const wait = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms))

let sample: SampleState = scenario === 'empty'
  ? { state: 'idle', seconds_recorded: 0, input_dbfs: null, threshold_dbfs: -40, max_seconds: 30, silence_seconds: 1.5 }
  : { state: 'done', seconds_recorded: 6.8, input_dbfs: null, threshold_dbfs: -40, max_seconds: 30, silence_seconds: 1.5,
      path: '/riff.wav', duration_s: 6.8, peak_dbfs: -6.0, lufs: -21.4,
      peaks: Array.from({ length: 160 }, (_, i) => Math.max(0.05, Math.sin(i / 160 * Math.PI) ** 0.6 * (Math.abs(Math.sin(i * 0.7)) ** 2 * 0.75 + 0.3))) }

export const setSample = (s: SampleState): void => { sample = s }
export const getSample = (): SampleState => sample
export let prefs = { bench: scenario === 'empty' ? [] : SLOTS, benchAutoSave: false, mode: 'direct', language: 'en' }
let rerender: (() => void) | null = null
/** What the main process would push: enough Snapshot for the Leveling view. */
export const snapOf = (): unknown => ({
  platform: 'mac', locale: 'en', daemon: { state: 'running' }, prefs, checks: [], cortex: { running: true }
})
export const setRerender = (f: () => void): void => { rerender = f }

const leveling = {
  start: async () => undefined, stop: async () => undefined,
  state: async () => presetOf(loaded),
  folders: async () => [],
  open: async (folderKey: string, position: number, _f: boolean, cloudId: string) => {
    await wait(700)
    loaded = SLOTS.find((b) => id(b) === id({ folderKey, position, cloudId })) ?? loaded
    return presetOf(loaded)
  },
  level: async (_row: number, db: number) => db,
  toggle: async () => true,
  scene: async (i: number) => i,
  save: async (name = '') => ({ name: name || loaded.name, position: loaded.position }),
  meter: async () => true,
  audio: async () => ({ available: true, sample: sample.path ?? null, quadCortex: { index: 0, name: 'Quad Cortex', inputs: 8, outputs: 8 } }),
  sampleArm: async () => { sample = { ...sample, state: 'armed', input_dbfs: -41 }; return sample },
  sampleStatus: async () => {
    if (sample.state === 'armed') sample = { ...sample, input_dbfs: -30 + Math.random() * 8 }
    return sample
  },
  sampleInfo: async () => sample,
  sampleStop: async () => { sample = { ...sample, state: 'done', input_dbfs: null }; return sample },
  sampleDiscard: async () => { sample = { ...sample, state: sample.path ? 'done' : 'idle', input_dbfs: null }; return sample },
  measure: async () => { await wait(1200); return { duration_s: 6.8, silent: false, sample_peak_dbfs: -3, lufs_integrated: -17.2, true_peak_dbtp: -1.1 } },
  autolevel: async (o: { dryRun?: boolean }) => {
    await wait(1200)
    return { target: -18, metric: 'lufs', iterations: [{ n: 1, measured: -15.9, delta_db: -2.1, true_peak_dbtp: -0.3, wrote_db: o.dryRun ? null : -2.1 }],
             written: !o.dryRun, converged: true, final_delta_db: 0.2, suggested_db: o.dryRun ? -2.1 : undefined }
  },
  cancel: async () => ({ cancelling: true }),
  riffs: async () => ({ riffs: [{ name: 'chords', seconds: 4.5, why: 'Open chords, sustained.' }, { name: 'chug', seconds: 4.6, why: 'Palm-muted eighths.' }], loaded: null, recorded: true }),
  useRiff: async () => sample,
  applyTrim: async (o: { db: number; row?: number }) => { await wait(400); return { row: o.row ?? 0, applied_db: o.db, from_db: 0, db: o.db } },
  samplePlay: async () => { setTimeout(() => emit({ event: 'play', done: true }), 2500); return { playing: true, seconds: 6.8 } },
  sampleStopPlay: async () => { emit({ event: 'play', done: true }) },
  revertLevels: async () => ({ reverted: [] }),
  measureMany: async (presets: { position: number; name: string; cloud_id?: string; folder_key: string }[]) => {
    for (const [i, p] of presets.entries()) {
      emit({ event: 'measuring', index: i, name: p.name, total: presets.length })
      await wait(500)
      const k = id({ folderKey: p.folder_key, position: p.position, cloudId: p.cloud_id ?? '' })
      const row: ReportRow = scenario === 'error' && p.name === 'SOLDANO'
        ? { position: p.position, name: p.name, error: 'capture came back as digital silence — is the microphone permission granted?' }
        : { position: p.position, name: p.name, row: 0, ...MEASURED[k] }
      emit({ event: 'measured', row })
    }
    return { rows: [] }
  },
  measureScenes: async () => ({ rows: [] }),
  levelScenes: async () => undefined,
  inputLevel: async () => ({ port: 1, db: 0, minDb: -12, maxDb: 60 }),
  setInputLevel: async (db: number) => { await wait(900); return { port: 1, db, minDb: -12, maxDb: 60 } },
  onEvent: (cb: (e: LevelEvent) => void) => { listeners.push(cb); return () => { listeners = listeners.filter((f) => f !== cb) } }
}

;(window as unknown as { patchbay: unknown }).patchbay = {
  setMode: async () => null, cortexFocus: async () => null, snapshot: async () => null,
  onSnapshot: () => () => undefined, onProgress: () => () => undefined,
  setPrefs: async (p: Record<string, unknown>) => { prefs = { ...prefs, ...p }; rerender?.(); return snapOf() },
  update: { onState: () => () => undefined, state: async () => ({}), check: async () => undefined },
  leveling
}

// a meter stream: the loaded preset "plays" at a level that breathes
setInterval(() => {
  const t = Date.now() / 1000
  const amp = 0.25 + 0.2 * Math.abs(Math.sin(t * 1.7)) + Math.random() * 0.05
  emit({ event: 'meter', at: Date.now(), outputs: { xlr_1: { level: amp }, xlr_2: { level: amp * 0.96 }, hp_l: { level: amp * 0.4 }, hp_r: { level: amp * 0.4 } } })
}, 180)

