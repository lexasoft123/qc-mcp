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

/** One preset's line in the level report. */
export interface ReportRow {
  position: number
  name?: string
  row?: number
  lufs?: number | null
  n5?: number | null
  true_peak?: number | null
  /** The metric being levelled by — LUFS, or relative sones when perceived. */
  measured?: number | null
  correction_db?: number | null
  error?: string | null
}

export interface ReportResult {
  target: number
  metric: string
  rows: ReportRow[]
  spread: number | null
}

export type LevelEvent =
  | { event: 'meter'; at: number; outputs: Record<string, MeterOutput> }
  | { event: 'stopped'; error: string | null }
  | { event: 'fatal'; error: string }
  | { event: 'autolevel'; row: number; step: AutoStep }
  | { event: 'measuring'; index: number; name: string; total: number }
  | { event: 'measured'; row: ReportRow }
  | { event: 'play'; done?: boolean; error?: string }

/** One measure->correct pass of the automatic loop. */
export interface AutoStep {
  n: number
  /** LUFS, or relative sones when levelling by perceived loudness. */
  measured: number | null
  /** How far off target this reading was, in dB. */
  delta_db: number | null
  true_peak_dbtp: number | null
  /** The trim written after this reading; null on the first, measure-only pass. */
  wrote_db: number | null
  limited_by?: 'true_peak'
  backed_off_to_db?: number
}

/** What one capture measured. Everything is null when it could not be measured. */
export interface Measurement {
  duration_s: number
  silent: boolean
  sample_peak_dbfs: number | null
  lufs_integrated?: number | null
  true_peak_dbtp?: number | null
  rms_dbfs?: number | null
  zwicker_n5_rel?: number | null
  /** Present when the capture cannot be trusted — silence, below the gate, too short. */
  error?: string
}

export interface AutoResult {
  target: number
  metric: string
  iterations: AutoStep[]
  written: boolean
  converged?: boolean
  limited?: boolean
  final_delta_db?: number | null
  final_trim_db?: number | null
  suggested_db?: number
  trim_block?: { row: number; knob: string; column?: number; added?: boolean }
  error?: string
}

/** The reference riff the measured half plays into every preset. */
export interface SampleState {
  state: 'idle' | 'armed' | 'recording' | 'done'
  seconds_recorded: number
  input_dbfs: number | null
  threshold_dbfs: number
  max_seconds: number
  silence_seconds: number
  error?: string | null
  /** Peak envelope of the take, 0..1 — what the waveform draws. The audio itself
   *  runs to tens of megabytes and this crosses a socket on every poll. */
  peaks?: number[]
  /** Set once the take is kept. */
  path?: string
  duration_s?: number
  peak_dbfs?: number | null
  lufs?: number | null
}

/** Whether the optional audio extra is installed, and what it can see. */
export interface AudioState {
  available: boolean
  error?: string
  hint?: string
  quadCortex: { index: number; name: string; inputs: number; outputs: number } | null
  /** Path of the stored reference riff, or null when none has been recorded. */
  sample: string | null
}

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
  cortexFocus(): Promise<Snapshot>
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

    /** The measured half. Needs the optional audio extra; `audio()` says whether. */
    audio(): Promise<AudioState>
    sampleArm(o?: { thresholdDbfs?: number; maxSeconds?: number }): Promise<SampleState>
    sampleStatus(): Promise<SampleState>
    sampleInfo(): Promise<SampleState>
    sampleStop(): Promise<SampleState>
    sampleDiscard(): Promise<SampleState>
    measure(perceived?: boolean): Promise<Measurement>
    autolevel(o?: { target?: number; tolerance?: number; dryRun?: boolean }): Promise<AutoResult>
    samplePlay(): Promise<{ playing: boolean; seconds: number }>
    sampleStopPlay(): Promise<void>
    revertLevels(): Promise<{ reverted: { row: number; db: number }[] }>
    measureMany(
      presets: { folder_key: string; position: number; name: string; cloud_id?: string }[],
      o?: { target?: number; metric?: string; }
    ): Promise<ReportResult>
  }

  window: {
    isMaximized(): Promise<boolean>
    onMaximized(cb: (v: boolean) => void): () => void
    minimize(): void
    maximizeToggle(): void
    close(): void
  }
}
