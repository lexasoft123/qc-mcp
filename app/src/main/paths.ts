import { homedir } from 'node:os'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { app } from 'electron'
import type { Paths, Platform } from '../shared/types.js'

export const PLATFORM: Platform = process.platform === 'win32' ? 'win' : 'mac'
export const IS_MAC = PLATFORM === 'mac'
const HOME = homedir()

function isRepo(dir: string): boolean {
  try {
    const f = join(dir, 'pyproject.toml')
    return existsSync(f) && readFileSync(f, 'utf8').includes('name = "qc-mcp"')
  } catch {
    return false
  }
}

/**
 * Find the qc-mcp checkout. In development the app runs from <repo>/app, so
 * walking up finds it; a packaged build has no such luck and falls back to the
 * conventional location, which Preferences can override.
 */
export function findRepo(override?: string | null): string {
  if (override && isRepo(override)) return override
  let dir = app.getAppPath()
  for (let i = 0; i < 6; i++) {
    if (isRepo(dir)) return dir
    const up = dirname(dir)
    if (up === dir) break
    dir = up
  }
  return join(HOME, 'Dev', 'qc-mcp')
}

export const DEFAULT_CORTEX = IS_MAC
  ? '/Applications/Neural DSP/Cortex Control.app'
  : 'C:\\Program Files\\Neural DSP\\Cortex Control\\Cortex Control.exe'

export function pathsFor(repo: string, cortexOverride?: string | null): Paths {
  return {
    repo,
    bin: IS_MAC ? join(repo, '.venv', 'bin', 'qc-mcp') : join(repo, '.venv', 'Scripts', 'qc-mcp.exe'),
    python: IS_MAC ? join(repo, '.venv', 'bin', 'python') : join(repo, '.venv', 'Scripts', 'python.exe'),
    cortex: cortexOverride || DEFAULT_CORTEX,
    // macOS logs through the interposer next to it; Windows has no interposer,
    // so the daemon writes its own log under LocalAppData.
    logPath: IS_MAC
      ? join(repo, 'interceptor', 'hid_log.txt')
      : join(process.env.LOCALAPPDATA || join(HOME, 'AppData', 'Local'), 'qc-mcp', 'hid_log.txt'),
    // the same default `qc-mcp --daemon` picks when given no --socket, so a
    // hand-run daemon and a Patchbay-run one are the same endpoint
    socket: IS_MAC
      ? join(HOME, 'Library', 'Application Support', 'qc-mcp', 'daemon.sock')
      : join(process.env.LOCALAPPDATA || join(HOME, 'AppData', 'Local'), 'qc-mcp', 'daemon.sock')
  }
}

export const instrumentedApp = (repo: string): string =>
  join(repo, 'interceptor', 'CortexControl-instrumented.app')
export const buildScript = (repo: string): string => join(repo, 'interceptor', 'build.sh')
export const bridgeScript = (repo: string): string => join(repo, 'interceptor', 'run-bridge.sh')

/** The device, as the transport already knows it (iohid.py: vid 0x152A, pid 0x880A). */
export const QC_VID = 0x152a
export const QC_PID = 0x880a
