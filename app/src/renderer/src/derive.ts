import type { ClientTarget, Snapshot } from '@shared/types'

export const isMac = (s: Snapshot): boolean => s.platform === 'mac'

/** Steps Patchbay can actually fix and has not. */
export const setupPending = (s: Snapshot): boolean =>
  s.checks.some((c) => c.fixable && c.status !== 'ok')

/**
 * Can the daemon really reach the device? Direct always; bridge needs the app
 * on macOS (it rides its session) but not on Windows (its own HID handle).
 */
export const isLinked = (s: Snapshot): boolean =>
  s.daemon.state === 'running' &&
  s.device.present &&
  (s.prefs.mode === 'direct' || !isMac(s) || s.cortex.running)

export const regCount = (s: Snapshot): number => s.clients.filter((c) => c.installed).length

/** Registered before the daemon existed — they still seize the device. */
export const staleClients = (s: Snapshot): ClientTarget[] => s.clients.filter((c) => c.stale)

export const clash = (s: Snapshot): boolean =>
  s.daemon.state === 'running' && s.prefs.mode === 'direct' && s.cortex.running

/** Two independent writers on one endpoint — the caution connect() returns. */
export const sharedWriters = (s: Snapshot): boolean =>
  !isMac(s) && s.daemon.state === 'running' && s.prefs.mode !== 'direct' && s.cortex.running

export function uptime(startedAt: number | null): string {
  if (!startedAt) return '—'
  const secs = Math.max(0, Math.floor((Date.now() - startedAt) / 1000))
  const h = Math.floor(secs / 3600)
  const m = Math.floor((secs % 3600) / 60)
  return `${h ? `${h}h ` : ''}${m}m ${String(secs % 60).padStart(2, '0')}s`
}

export function railText(s: Snapshot): string {
  if (!s.device.present) return 'No Quad Cortex found on USB'
  if (s.daemon.state !== 'running') {
    return s.daemon.error ?? 'Daemon stopped — no MCP client can reach the device'
  }
  if (clash(s)) return 'Direct mode blocked — Cortex Control is still holding the device'
  if (s.prefs.mode === 'direct') {
    return isMac(s) ? 'Direct session — the daemon holds the USB interface' : 'Direct session — exclusive HID handle'
  }
  if (isMac(s)) {
    return s.cortex.running
      ? "Sharing Cortex Control's session · FIFOs healthy"
      : 'Waiting for Cortex Control — bridge mode needs the instrumented app'
  }
  return s.cortex.running
    ? 'Sharing the device with Cortex Control · second HID handle'
    : 'Daemon has the device · Cortex Control is closed'
}

export const sessionFact = (s: Snapshot): string =>
  s.prefs.mode === 'direct'
    ? isMac(s) ? 'direct · IOHIDDevice seized' : 'direct · exclusive HID handle'
    : isMac(s) ? 'bridge · /tmp/qc_inject ⇄ /tmp/qc_in' : 'bridge · second HID handle, non-exclusive'
