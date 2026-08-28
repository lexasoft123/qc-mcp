/** The contract between the main process and the renderer. */

export type Platform = 'mac' | 'win'
export type Mode = 'auto' | 'bridge' | 'direct'
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

  window: {
    isMaximized(): Promise<boolean>
    onMaximized(cb: (v: boolean) => void): () => void
    minimize(): void
    maximizeToggle(): void
    close(): void
  }
}
