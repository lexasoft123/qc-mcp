import type { ClientTarget, SessionMode, Snapshot } from '@shared/types'

export const isMac = (s: Snapshot): boolean => s.platform === 'mac'

/** Steps Patchbay can actually fix and has not. */
export const setupPending = (s: Snapshot): boolean =>
  s.checks.some((c) => c.fixable && c.status !== 'ok')

/**
 * The session the daemon actually opened.
 *
 * The *preference* cannot answer this: `auto` becomes bridge or direct
 * depending on whether Cortex Control was up at connect time, and everything
 * that branched on `prefs.mode !== 'direct'` therefore read `auto` as bridge —
 * so a perfectly healthy direct session kept being told to open Cortex Control.
 * Falls back to the preference's intent only until the daemon has reported.
 */
export const sessionMode = (s: Snapshot): SessionMode =>
  s.daemon.session ?? (s.prefs.mode === 'direct' ? 'direct' : isMac(s) ? 'bridge' : 'shared')

/**
 * Can the daemon really reach the device? Only a bridge session depends on the
 * app: it rides Cortex Control's own handle. Direct and Windows' shared handle
 * stand on their own.
 */
export const isLinked = (s: Snapshot): boolean =>
  s.daemon.state === 'running' &&
  s.device.present &&
  (sessionMode(s) !== 'bridge' || s.cortex.running)

export const regCount = (s: Snapshot): number => s.clients.filter((c) => c.installed).length

/** Registered before the daemon existed — they still seize the device. */
export const staleClients = (s: Snapshot): ClientTarget[] => s.clients.filter((c) => c.stale)

export const clash = (s: Snapshot): boolean =>
  s.daemon.state === 'running' && sessionMode(s) === 'direct' && isMac(s) && s.cortex.running

/** Two independent writers on one endpoint — the caution connect() returns. */
export const sharedWriters = (s: Snapshot): boolean =>
  s.daemon.state === 'running' && sessionMode(s) === 'shared' && s.cortex.running

export function uptime(startedAt: number | null): string {
  if (!startedAt) return '—'
  const secs = Math.max(0, Math.floor((Date.now() - startedAt) / 1000))
  const h = Math.floor(secs / 3600)
  const m = Math.floor((secs % 3600) / 60)
  return `${h ? `${h}h ` : ''}${m}m ${String(secs % 60).padStart(2, '0')}s`
}

/**
 * The last line of a Python failure, without the traceback.
 *
 * The daemon's stderr arrives whole. Putting that in a one-line bar produced
 * `...<2 lines>... "mode.") qc_mcp.backend.BridgeError: …` — the useful sentence
 * buried behind the wreckage of the frames above it.
 */
export function cleanError(raw: string | null): string | null {
  if (!raw) return null
  const line = raw
    .split('\n')
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('File "') && !l.startsWith('Traceback')
                     && !l.startsWith('~') && !l.startsWith('^'))
    .pop()
  if (!line) return null
  // "qc_mcp.backend.BridgeError: Cortex Control is holding…" -> the sentence
  const m = line.match(/^[\w.]*(?:Error|Exception):\s*(.+)$/)
  return (m ? m[1] : line).trim()
}

/** Which Cortex Control is up, if any — the stock app or the instrumented copy. */
export function cortexText(s: Snapshot): string {
  if (!s.cortex.running) return 'Cortex Control closed'
  return s.cortex.runningInstrumented
    ? 'instrumented Cortex Control open'
    : 'stock Cortex Control open'
}

export function railText(s: Snapshot): string {
  if (!s.device.present) return 'No Quad Cortex found on USB'
  if (s.daemon.state !== 'running') {
    const why = cleanError(s.daemon.error)
    return why
      ? `Not connected — ${why}`
      : 'Daemon stopped — no MCP client can reach the device'
  }
  if (clash(s)) return 'Direct mode blocked — Cortex Control is still holding the device'
  const who = s.daemon.external ? 'Adopted a daemon started outside Patchbay' : null
  const body = ((): string => {
    switch (sessionMode(s)) {
      case 'direct':
        return isMac(s)
          ? 'Direct session — the daemon holds the USB interface'
          : 'Direct session — exclusive HID handle'
      case 'shared':
        return s.cortex.running
          ? 'Sharing the device with Cortex Control · second HID handle'
          : 'Daemon has the device · Cortex Control is closed'
      default:
        return s.cortex.running
          ? "Bridge session — sharing Cortex Control's own connection"
          : 'Waiting for Cortex Control — bridge mode needs the instrumented app'
    }
  })()
  return `${who ? who + ' · ' : ''}${body} · ${cortexText(s)}`
}

export const sessionFact = (s: Snapshot): string => {
  switch (sessionMode(s)) {
    case 'direct':
      return isMac(s) ? 'direct · IOHIDDevice seized' : 'direct · exclusive HID handle'
    case 'shared':
      return 'shared · second HID handle, non-exclusive'
    default:
      return 'bridge · /tmp/qc_inject ⇄ /tmp/qc_in'
  }
}

/**
 * A bench slot's identity.
 *
 * Downloads presets all report position 0 — their real key is the cloud id — so
 * keying on folder+position alone collapses a whole cloud folder into one slot.
 */
export const slotId = (s: { folderKey: string; position: number; cloudId: string }): string =>
  s.cloudId || `${s.folderKey}:${s.position}`
