import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import type { SessionLock } from '../shared/types.js'

/**
 * Reading the one record that says who holds the device.
 *
 * The mirror of qc_mcp/lockfile.py — same file, same rules. Patchbay used to
 * assemble this out of `pgrep`, the existence of two FIFOs and a socket connect,
 * and those three could each be true while describing different worlds. This
 * asks the owner.
 *
 * A record whose process is gone is not an owner. That test is the whole reason
 * the file can be advisory: nothing has to be cleaned up for the truth to come
 * back, so a daemon that was SIGKILLed leaves no lasting lie.
 */

export const lockPath = (socket: string): string => join(dirname(socket), 'session.json')

function alive(pid: number): boolean {
  if (!pid || pid <= 0) return false
  try {
    process.kill(pid, 0)
    return true
  } catch (e) {
    // EPERM means it exists and belongs to somebody else
    return (e as NodeJS.ErrnoException).code === 'EPERM'
  }
}

/** The live owner, or null. Never throws: an unreadable lock is no lock. */
export function read(socket: string): SessionLock | null {
  let raw: unknown
  try {
    raw = JSON.parse(readFileSync(lockPath(socket), 'utf8'))
  } catch {
    return null
  }
  if (!raw || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>
  const pid = Number(r.pid)
  if (!alive(pid)) return null
  const owner = r.owner
  const mode = r.mode
  if (owner !== 'daemon' && owner !== 'mcp' && owner !== 'bench') return null
  if (mode !== 'bridge' && mode !== 'shared' && mode !== 'direct') return null
  return {
    pid,
    owner,
    mode,
    socket: typeof r.socket === 'string' ? r.socket : '',
    startedAt: typeof r.started_at === 'number' ? r.started_at * 1000 : null,
    firmware: typeof r.firmware === 'string' ? r.firmware : null,
    launchedBy: typeof r.launched_by === 'string' ? r.launched_by : null,
    appPid: typeof r.app_pid === 'number' ? r.app_pid : null
  }
}

/** Did WE start this one? Anything else is somebody else's to take over. */
export const isOurs = (lock: SessionLock | null): boolean =>
  lock?.launchedBy === 'patchbay'
