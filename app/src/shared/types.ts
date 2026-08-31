/** The contract between the main process and the renderer. */

export type Platform = 'mac' | 'win'
export type Mode = 'auto' | 'bridge' | 'direct'
/**
 * What the daemon actually opened. `auto` resolves at connect time, so the
 * preference alone never says whether the session needs Cortex Control —
 * 'shared' is the Windows second-handle case.
 */
export type SessionMode = 'bridge' | 'shared' | 'direct'
export type DaemonState = 'stopped' | 'starting' | 'running'
export type CheckStatus = 'ok' | 'missing' | 'checking'

/**
 * The preflight checks. `clang` and `instrumented` exist only on macOS: the
 * DYLD interposer is what lets the MCP share a session there, and Windows
 * needs none of it (the HID stack hands out a second, non-exclusive handle).
 */
export type CheckId = 'python' | 'venv' | 'clang' | 'app' | 'instrumented' | 'register' | 'device'

export interface Check {
  id: CheckId
  title: string
  /** May contain a single level of <code> markup. */
  detail: string
  status: CheckStatus
  /** false for the things Patchbay can only observe: Python, the app, the device. */
  fixable: boolean
}

export interface ClientTarget {
  id: string
  name: string
  path: string
  /** the client itself is installed on this machine */
  found: boolean
  /** our server entry is present in its config */
  installed: boolean
  /** installed, but from before the daemon: it still opens the device itself,
   *  so it will fail while the daemon holds one */
  stale: boolean
}

export interface InstrumentedInfo {
  built: boolean
  version: string | null
  /** injection is only permitted with the hardened runtime OFF */
  hardenedRuntimeOff: boolean
  libraryValidationOff: boolean
}

export interface CortexInfo {
  installed: boolean
  path: string
  version: string | null
  running: boolean
  pid: number | null
  /** macOS only */
  instrumented: InstrumentedInfo | null
  /** the source app moved past the instrumented copy (macOS only) */
  needsRebuild: boolean
}

export interface DaemonInfo {
  state: DaemonState
  pid: number | null
  startedAt: number | null
  socket: string
  mode: Mode
  /** The mode the running daemon reported, or null before it has said. */
  session: SessionMode | null
  /** false once we have proven this qc-mcp build has no daemon entry point */
  supported: boolean
  error: string | null
  reportsPerSecond: number
  clients: string[]
}

export interface DeviceInfo {
  present: boolean
  /** which model answered - 'Quad Cortex' / 'Quad Cortex Mini' */
  model: string | null
  serial: string | null
  firmware: string | null
}

export interface Paths {
  repo: string
  bin: string
  python: string
  cortex: string
  logPath: string
  socket: string
  /** The same paths with $HOME collapsed to `~`, for display. */
  show: {
    repo: string
    bin: string
    cortex: string
    logPath: string
    socket: string
  }
}

export interface Prefs {
  login: boolean
  autoconnect: boolean
  quitApp: boolean
  verbose: boolean
  autoRebuild: boolean
  mode: Mode
  repo: string | null
  cortex: string | null
  /** The presets parked on the leveling bench, in column order. */
  bench: BenchSlot[]
  /** Write each level change straight into the preset file. */
  benchAutoSave: boolean
}

export interface Snapshot {
  platform: Platform
  paths: Paths
  checks: Check[]
  clients: ClientTarget[]
  daemon: DaemonInfo
  cortex: CortexInfo
  device: DeviceInfo
  prefs: Prefs
}

// ── the preset-leveling bench ───────────────────────────────────────────

/** One grid row's output block — the QC's Lane Output Control (#23000). */
export interface Lane {
  row: number
  /** VOLUME, in dB on the calibrated -40..+12 range. */
  db: number
  /** PAN as the device shows it: -50 = hard left, 0 = centre, +50 = hard right. */
  pan: number
  mute: boolean
  solo: boolean
  outPortId: number
  /** Human label for the destination, e.g. "Multi Out". */
  out: string
  /** false when the lane is routed nowhere — in the preset, but silent. */
  active: boolean
  /** Reaches a real output (not unrouted, not an internal merge bus). */
  physical: boolean
  blocks: number
}

export interface PresetState {
  name: string
  folderKey: string
  position: number | null
  isFactory: boolean
  /** 0-7 = scenes A-H */
  scene: number
  sceneLabels: string[]
  lanes: Lane[]
}

/** A slot on the bench: a preset the user parked there to balance. */
export interface BenchSlot {
  folderKey: string
  position: number
  name: string
  /** Downloads are recalled by cloud id, not folder+position. "" for the rest. */
  cloudId: string
  /** The scene this preset was last left on, so returning to it restores it.
   *  null until you have actually picked one here — the bench must not override
   *  a preset's own default scene the first time it loads it. */
  scene: number | null
}

export interface PresetRef {
  position: number
  name: string
  cloudId: string
}

export interface PresetFolder {
  key: string
  name: string
  isFactory: boolean
  /** A cloud Downloads folder: its presets have no usable position. */
  isDownloads: boolean
  presets: PresetRef[]
}

/** One output's live reading, straight from the device's IOMeter stream. */
export interface MeterOutput {
  level: number
  limit?: number
}

export type LevelEvent =
  | { event: 'meter'; at: number; outputs: Record<string, MeterOutput> }
  | { event: 'stopped'; error: string | null }
  | { event: 'fatal'; error: string }

export type LogDirection = 'tx' | 'rx' | 'sys' | 'err'

export interface LogLine {
  t: string
  dir: LogDirection
  text: string
}

export interface Progress {
  /** the step currently running */
  label: string
  /** which check it belongs to, so the list can mark just that row */
  step?: CheckId
  done: number
  total: number
  /** set when the run finished */
  finished?: boolean
  error?: string
}

export interface Api {
  snapshot(): Promise<Snapshot>
  onSnapshot(cb: (s: Snapshot) => void): () => void
  onProgress(cb: (p: Progress) => void): () => void

  runChecks(): Promise<Snapshot>
  runSetup(ids?: CheckId[]): Promise<Snapshot>

  setClients(ids: string[]): Promise<Snapshot>

  daemonStart(): Promise<Snapshot>
  daemonStop(): Promise<Snapshot>
  setMode(mode: Mode): Promise<Snapshot>

  cortexLaunch(): Promise<Snapshot>
  cortexQuit(): Promise<Snapshot>
  /** Bring a running Cortex Control forward WITHOUT relaunching it. */
  cortexRebuild(): Promise<Snapshot>

  readLog(limit: number): Promise<LogLine[]>
  clearLog(): Promise<void>
  logSize(): Promise<number>

  getPrefs(): Promise<Prefs>
  setPrefs(patch: Partial<Prefs>): Promise<Snapshot>

  choosePath(what: 'repo' | 'cortex'): Promise<Snapshot>
  reveal(path: string): Promise<void>

  /** The preset-leveling bench. Every call rides the daemon's live session. */
  leveling: {
    start(): Promise<void>
    stop(): Promise<void>
    state(): Promise<PresetState>
    folders(refresh?: boolean): Promise<PresetFolder[]>
    open(folderKey: string, position: number, isFactory: boolean, cloudId: string): Promise<PresetState>
    level(row: number, db: number): Promise<number>
    toggle(row: number, which: 'mute' | 'solo', on: boolean): Promise<boolean>
    scene(index: number): Promise<number>
    save(name?: string): Promise<{ name: string; position: number }>
    meter(on: boolean): Promise<boolean>
    onEvent(cb: (e: LevelEvent) => void): () => void
  }

  window: {
    isMaximized(): Promise<boolean>
    onMaximized(cb: (v: boolean) => void): () => void
    minimize(): void
    maximizeToggle(): void
    close(): void
  }
}
