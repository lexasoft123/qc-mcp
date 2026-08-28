import { spawn } from 'node:child_process'
import type { CheckId, Paths, Progress } from '../shared/types.js'
import { IS_MAC, buildScript } from './paths.js'
import { exists, run } from './util.js'

export type Emit = (p: Progress) => void

function stream(cmd: string, args: string[], cwd: string, label: string, emit: Emit): Promise<number> {
  return new Promise((resolve) => {
    const child = spawn(cmd, args, { cwd, stdio: ['ignore', 'pipe', 'pipe'] })
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

/** `python -m venv .venv` then an editable install of the package itself. */
export async function createVenv(paths: Paths, python: string, emit: Emit): Promise<string | null> {
  const [cmd, ...pre] = python.split(' ')
  const mk = await stream(cmd, [...pre, '-m', 'venv', '.venv'], paths.repo, 'Creating the virtual environment', emit)
  if (mk !== 0) return 'Could not create .venv — check that Python 3.10 or newer is installed.'
  const pip = IS_MAC ? `${paths.repo}/.venv/bin/pip` : `${paths.repo}\\.venv\\Scripts\\pip.exe`
  const code = await stream(pip, ['install', '-e', IS_MAC ? '.[gui]' : '.'], paths.repo, 'Installing qc-mcp', emit)
  if (code !== 0) return 'pip could not install qc-mcp. Open Logs for the output.'
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
