import { spawn } from 'node:child_process'
import { join } from 'node:path'
import type { CheckId, Paths, Progress } from '../shared/types.js'
import { IS_MAC, UV_PYTHON, buildScript } from './paths.js'
import type { PythonInfo } from './system.js'
import { exists, run } from './util.js'

export type Emit = (p: Progress) => void

function stream(
  cmd: string, args: string[], cwd: string, label: string, emit: Emit,
  env?: NodeJS.ProcessEnv
): Promise<number> {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, { cwd, env, stdio: ['ignore', 'pipe', 'pipe'] })
    const tail = (d: unknown): void => {
      const line = String(d).trim().split('\n').filter(Boolean).pop()
      if (line) emit({ label: `${label} — ${line.slice(0, 90)}`, done: 0, total: 0 })
    }
    child.stdout?.on('data', tail)
    child.stderr?.on('data', tail)
    child.on('error', () => resolve(-1))
    child.on('close', (code) => resolve(code ?? -1))
  })
}

/**
 * Build `<repo>/.venv` and install qc-mcp into it editable.
 *
 * Two routes to the same layout, so nothing downstream has to care which ran:
 * bundled uv (fetches its own CPython, ~8s from nothing on a machine with no
 * usable Python), or a system interpreter that already satisfies >=3.10.
 *
 * No extras either way. `[gui]` is pyobjc for the tools/gui harness — ~35 MB,
 * and tools/ is not part of what a packaged Patchbay carries. Nothing under
 * src/qc_mcp imports it; the macOS HID transport is ctypes against IOKit.
 */
export async function createVenv(paths: Paths, python: PythonInfo, emit: Emit): Promise<string | null> {
  const venv = join(paths.repo, '.venv')
  if (python.uv) {
    // VIRTUAL_ENV so `uv pip` cannot pick some other environment, and
    // UV_PYTHON_DOWNLOADS so an interpreter is fetched when none matches —
    // that fetch is the entire reason uv is bundled.
    const env = { ...process.env, VIRTUAL_ENV: venv, UV_PYTHON_DOWNLOADS: 'automatic' }
    const mk = await stream(
      python.path, ['venv', '--python', UV_PYTHON, venv],
      paths.repo, `Installing Python ${UV_PYTHON}`, emit, env
    )
    if (mk !== 0) return `uv could not build the environment. It downloads Python ${UV_PYTHON} on first run, so this needs a network connection.`
    const code = await stream(
      python.path, ['pip', 'install', '--python', paths.python, '-e', '.'],
      paths.repo, 'Installing qc-mcp', emit, env
    )
    if (code !== 0) return 'uv could not install qc-mcp. Open Logs for the output.'
  } else {
    const [cmd, ...pre] = python.path.split(' ')
    const mk = await stream(cmd, [...pre, '-m', 'venv', venv], paths.repo, 'Creating the virtual environment', emit)
    if (mk !== 0) return 'Could not create .venv — check that Python 3.10 or newer is installed.'
    const pip = IS_MAC ? join(venv, 'bin', 'pip') : join(venv, 'Scripts', 'pip.exe')
    const code = await stream(pip, ['install', '-e', '.'], paths.repo, 'Installing qc-mcp', emit)
    if (code !== 0) return 'pip could not install qc-mcp. Open Logs for the output.'
  }
  return exists(paths.bin) ? null : `The install finished but ${paths.bin} is missing.`
}

/**
 * The Xcode command line tools cannot be installed silently — this opens
 * Apple's own installer and returns immediately, so the check stays 'missing'
 * until the user finishes it and re-checks.
 */
export async function installClang(): Promise<string | null> {
  if (!IS_MAC) return null
  await run('xcode-select', ['--install'], { timeout: 5000 })
  return 'macOS is installing the command line tools. Press Re-check when it finishes.'
}

/** interceptor/build.sh — compile the dylib, copy the app, re-sign, verify. */
export async function buildInstrumented(paths: Paths, emit: Emit): Promise<string | null> {
  if (!IS_MAC) return null
  const script = buildScript(paths.repo)
  if (!exists(script)) return `${script} is missing from this checkout.`
  const code = await stream(script, [], paths.repo, 'Building the instrumented copy', emit)
  return code === 0 ? null : 'The instrumented build failed. Open Logs for the output.'
}

export const FIXABLE: CheckId[] = ['venv', 'clang', 'instrumented', 'register']
