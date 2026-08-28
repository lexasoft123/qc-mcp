import { homedir } from 'node:os'
import { cpSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
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

let staged: string | null | undefined

/**
 * A packaged Patchbay carries the qc-mcp sources as extraResources, but that
 * copy sits inside the app bundle: read-only, and on macOS covered by the
 * signature. Everything qc-mcp needs to do writes NEXT TO those sources —
 * `python -m venv .venv`, and interceptor/build.sh dropping interpose.dylib and
 * the instrumented app beside itself.
 *
 * So the payload is staged into userData once, and used from there. What comes
 * out is the layout of an ordinary checkout, which is the point: the launcher
 * then has exactly one code path whether it was installed or run from the repo.
 */
function stagedRepo(): string | null {
  if (staged !== undefined) return staged
  staged = null
  if (!app.isPackaged) return staged
  const src = join(process.resourcesPath, 'qc-mcp')
  if (!existsSync(src)) return staged
  const dest = join(app.getPath('userData'), 'qc-mcp')
  const stamp = join(dest, '.staged')
  const version = app.getVersion()
  try {
    if (!existsSync(stamp) || readFileSync(stamp, 'utf8').trim() !== version) {
      mkdirSync(dest, { recursive: true })
      // Only the payload's own entries are copied, so a .venv already sitting
      // in dest survives an upgrade — re-running setup after every update
      // would be a poor trade for a source refresh.
      cpSync(src, dest, { recursive: true, dereference: true, force: true })
      writeFileSync(stamp, version + '\n', 'utf8')
    }
    staged = dest
  } catch {
    staged = existsSync(dest) ? dest : null
  }
  return staged
}

/**
 * Find the qc-mcp checkout: an explicit override first, then the staged payload
 * of a packaged build, then a walk up from the app path (which is what finds it
 * in development, running from <repo>/app).
 */
export function findRepo(override?: string | null): string {
  if (override && isRepo(override)) return override
  const bundled = stagedRepo()
  if (bundled && isRepo(bundled)) return bundled
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

/**
 * The interposer's two FIFOs, as bridge.py names them (INJECT_PATH / OUT_PATH).
 * Their mere existence does not mean the bridge is live — they are plain
 * filesystem objects and outlive the app — so they are only ever checked
 * together with the instrumented process being alive.
 */
export const BRIDGE_FIFOS = ['/tmp/qc_inject', '/tmp/qc_in']

/** The device, as the transport already knows it (iohid.py: vid 0x152A, pid 0x880A). */
export const QC_VID = 0x152a
export const QC_PID = 0x880a

/**
 * The bundled `uv`, or null when this build has none.
 *
 * Patchbay ships uv instead of a Python environment: the environment itself is
 * built on the machine (docs/PACKAGING.md). Packaged, afterPack has already put
 * the arch-matching binary at Resources/uv/; in development it comes from
 * app/resources/uv/<target>/, which `npm run fetch:uv` fills.
 */
export function uvBin(): string | null {
  if (uv !== undefined) return uv
  const exe = IS_MAC ? 'uv' : 'uv.exe'
  const target = IS_MAC
    ? process.arch === 'x64' ? 'x86_64-apple-darwin' : 'aarch64-apple-darwin'
    : 'x86_64-pc-windows-msvc'
  const candidates = app.isPackaged
    ? [join(process.resourcesPath, 'uv', exe)]
    : [join(app.getAppPath(), 'resources', 'uv', target, exe)]
  uv = candidates.find((p) => existsSync(p)) ?? null
  return uv
}
let uv: string | null | undefined

/** The Python uv installs when it builds the environment. */
export const UV_PYTHON = '3.12'
