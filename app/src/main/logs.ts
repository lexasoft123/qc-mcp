import { closeSync, openSync, readSync, statSync, writeFileSync } from 'node:fs'
import type { LogDirection, LogLine } from '../shared/types.js'
import { exists } from './util.js'

/**
 * Parse the interposer's frame log.
 *
 * interpose.c writes one line per report:
 *   <ms> IN|OUT|INJECT id=<n> len=<n> <hex…>
 * plus bare OPEN / "interpose loaded" markers. Direction is what matters in
 * the UI, so anything that is not a report line is shown as a system note.
 */
const FRAME = /^([\d.]+)\s+(IN|OUT|INJECT)\s+id=(\d+)\s+len=(\d+)\s+([0-9a-f]*)/i

function clock(ms: string): string {
  const d = new Date(Number(ms))
  const p = (n: number, w = 2): string => String(n).padStart(w, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}.${p(d.getMilliseconds(), 3)}`
}

export function read(file: string, limit: number): LogLine[] {
  if (!exists(file)) return []
  // read only the tail — a session's frame log runs to megabytes
  let text = ''
  let fd: number | null = null
  try {
    const total = statSync(file).size
    const want = Math.min(total, 512 * 1024)
    const buf = Buffer.alloc(want)
    fd = openSync(file, 'r')
    readSync(fd, buf, 0, want, total - want)
    text = buf.toString('utf8')
  } catch {
    return []
  } finally {
    if (fd !== null) { try { closeSync(fd) } catch { /* already closed */ } }
  }
  const out: LogLine[] = []
  for (const raw of text.split('\n')) {
    const line = raw.trim()
    if (!line) continue
    const m = FRAME.exec(line)
    if (m) {
      const dir: LogDirection = m[2].toUpperCase() === 'IN' ? 'rx' : 'tx'
      const hex = m[5]
      out.push({
        t: clock(m[1]),
        dir,
        text: `report ${m[3]} · ${m[4]} bytes · ${hex.slice(0, 24)}${hex.length > 24 ? '…' : ''}`
      })
      continue
    }
    const err = /fail|error|0x[0-9a-f]*[1-9]/i.test(line) && /ret=/.test(line)
    out.push({ t: '', dir: err ? 'err' : 'sys', text: line.slice(0, 160) })
  }
  return out.slice(-limit)
}

export function size(file: string): number {
  try { return statSync(file).size } catch { return 0 }
}

export function clear(file: string): void {
  try { writeFileSync(file, '', 'utf8') } catch { /* nothing to clear */ }
}

/**
 * Reports per second, measured rather than guessed: the interposer stamps each
 * frame with a millisecond clock, so we count the frames inside the last two
 * seconds of the log and check that the log itself is still fresh.
 */
export function rate(file: string): number {
  if (!exists(file)) return 0
  let text = ''
  let fd: number | null = null
  try {
    const total = statSync(file).size
    const want = Math.min(total, 128 * 1024)
    const buf = Buffer.alloc(want)
    fd = openSync(file, 'r')
    readSync(fd, buf, 0, want, total - want)
    text = buf.toString('utf8')
  } catch {
    return 0
  } finally {
    if (fd !== null) { try { closeSync(fd) } catch { /* already closed */ } }
  }
  const stamps: number[] = []
  for (const line of text.split('\n')) {
    const m = FRAME.exec(line.trim())
    if (m) stamps.push(Number(m[1]))
  }
  if (stamps.length < 2) return 0
  const newest = stamps[stamps.length - 1]
  if (Date.now() - newest > 5000) return 0
  const window = 2000
  const inWindow = stamps.filter((s) => s > newest - window).length
  return Math.round((inWindow / window) * 1000)
}
