import { spawn, type SpawnOptions } from 'node:child_process'
import { existsSync } from 'node:fs'

export interface RunResult {
  code: number
  out: string
  err: string
}

/**
 * Run a command and collect its output. Never rejects: a missing binary comes
 * back as code -1 with the error in `err`, because every caller here is asking
 * "is this thing present and what does it say", not "did this succeed".
 */
export function run(
  cmd: string,
  args: string[],
  opts: SpawnOptions & { timeout?: number } = {}
): Promise<RunResult> {
  return new Promise((resolve) => {
    let child: ReturnType<typeof spawn>
    try {
      child = spawn(cmd, args, { ...opts, stdio: ['ignore', 'pipe', 'pipe'] })
    } catch (e) {
      resolve({ code: -1, out: '', err: String(e) })
      return
    }
    let out = ''
    let err = ''
    const timer = opts.timeout ? setTimeout(() => child.kill('SIGKILL'), opts.timeout) : null
    child.stdout?.on('data', (d) => { out += String(d) })
    child.stderr?.on('data', (d) => { err += String(d) })
    child.on('error', (e) => {
      if (timer) clearTimeout(timer)
      resolve({ code: -1, out, err: String(e) })
    })
    child.on('close', (code) => {
      if (timer) clearTimeout(timer)
      resolve({ code: code ?? -1, out, err })
    })
  })
}

/** Run a PowerShell one-liner. Windows only; returns '' anywhere else. */
export async function ps(script: string): Promise<string> {
  if (process.platform !== 'win32') return ''
  const r = await run('powershell', ['-NoProfile', '-NonInteractive', '-Command', script], { timeout: 8000 })
  return r.out.trim()
}

export const exists = (p: string): boolean => {
  try { return existsSync(p) } catch { return false }
}

export const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms))

/**
 * The sentence out of a Python traceback.
 *
 * This used to be `stderr.split('\n').slice(-3).join(' ')` — three arbitrary
 * lines of wreckage glued together, which is what the status bar was showing:
 * `...<2 lines>... "Windows") qc_mcp.backend.BridgeError: bridge mode needs…`.
 * The last `SomeError: message` line is the whole of what anyone wants.
 */
export function lastErrorLine(stderr: string): string | null {
  const lines = stderr.split('\n').map((l) => l.trimEnd()).filter((l) => l.trim())
  for (let i = lines.length - 1; i >= 0; i--) {
    // Any dotted exception class, not only the ones spelled …Error: the
    // session lock raises `qc_mcp.lockfile.Held`, whose whole point is that its
    // message is the sentence a person should read.
    const m = /^\s*(?:[\w]+\.)+([A-Z]\w*):\s*(.+)$/.exec(lines[i])
      ?? /^\s*(\w*(?:Error|Exception|Interrupt)):\s*(.+)$/.exec(lines[i])
    if (m) {
      // a message that wrapped onto following lines carries on until the next frame
      const tail = lines.slice(i + 1).filter((l) => !/^\s*(File "|\s{2,}|Traceback)/.test(l))
      return [m[2], ...tail].join(' ').trim()
    }
  }
  return lines.at(-1) ?? null
}
