import type { ClientTarget, Mode, SessionMode, Snapshot } from '@shared/types'
import type { Facts } from '@shared/session'
import { planFor, planWords, resolveSession } from '@shared/session'
import { t } from '@shared/i18n'

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

/**
 * What a mode will actually DO, given what is running this second.
 *
 * This is not a second opinion — it calls the same planFor() the main process
 * executes, so an interface that promises a shared session and a main process
 * that seizes the device cannot happen. `auto` is the reason it exists: it
 * names a decision rather than a session, and the decision is made from state
 * the user can see but was never told was being read.
 */
export function modeFacts(s: Snapshot): Facts {
  return {
    heldBy: s.lock && {
      owner: s.lock.owner,
      mode: s.lock.mode,
      ours: s.lock.launchedBy === 'patchbay',
      // The renderer cannot open a socket. `daemon.state === 'running'` is the
      // main process's answer to the same question, and main re-checks it for
      // real before acting on any plan.
      serving: s.daemon.state === 'running',
      adoptable: s.lock.owner === 'daemon' && s.daemon.state === 'running'
    },
    platform: s.platform,
    devicePresent: s.device.present,
    cortexInstalled: s.cortex.installed,
    cortexRunning: s.cortex.running,
    cortexInstrumented: Boolean(s.cortex.runningInstrumented),
    instrumentedBuilt: Boolean(s.cortex.instrumented?.built),
    // The renderer cannot stat the FIFOs; the instrumented app being up is the
    // half of the test it can see, and main re-checks the other half.
    bridgeReady: isMac(s) ? Boolean(s.cortex.runningInstrumented) : s.cortex.running,
    daemonRunning: s.daemon.state === 'running',
    daemonSession: s.daemon.session
  }
}

export interface ModePlan {
  mode: Mode
  /** The session this mode opens, resolved against the snapshot. */
  will: SessionMode
  /** What pressing Connect does right now, in one sentence. */
  plan: string
  /** Set when it cannot work as things stand — the sentence is the fix. */
  blocked: string | null
}

export function modePlan(s: Snapshot, mode: Mode = s.prefs.mode): ModePlan {
  const f = modeFacts(s)
  const p = planFor('connect', mode, f, s.prefs.quitApp)
  return {
    mode,
    will: p.session ?? resolveSession(mode, f),
    plan: planWords('connect', mode, f, p),
    blocked: p.blocked
  }
}

/**
 * The session that is actually open, named and then explained.
 *
 * The mode alone will not do it here — `auto` is displayed as the selection, and
 * what it resolved to is the thing the user came to the front page to learn.
 */
/** The one-word name of the open session, for the titlebar. */
export const sessionWord = (s: Snapshot): string => t(`session.${sessionMode(s)}`)

export const sessionWords = (s: Snapshot, m: SessionMode): string =>
  m === 'direct'
    ? isMac(s) ? t('session.directMac') : t('session.directWin')
    : m === 'shared' ? t('session.shared') : t('session.bridge')

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
  const s = String(secs % 60).padStart(2, '0')
  return h ? t('uptime.hms', { h, m, s }) : t('uptime.ms', { m, s })
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

/**
 * Who holds the device, said plainly — for the strip that offers to take over.
 * Null when it is nobody, or us.
 */
export function heldByOther(s: Snapshot): string | null {
  const l = s.lock
  if (!l || l.launchedBy === 'patchbay') return null
  return t('lock.heldBy', {
    who: t(`lock.owner.${l.owner}` as Parameters<typeof t>[0]),
    how: t(`lock.how.${l.mode}` as Parameters<typeof t>[0])
  })
}

/**
 * A daemon that is alive and answering nobody.
 *
 * Its socket is gone — deleted under it, which is exactly what Patchbay's own
 * stop() used to do to a daemon it had adopted. The device stays held, no
 * client can reach it, and before the lock existed there was nothing to see:
 * the UI said 'stopped' while every connect failed on a busy device.
 */
export function heldButUnreachable(s: Snapshot): boolean {
  return Boolean(s.lock && s.lock.owner === 'daemon' && s.daemon.state !== 'running')
}

/** Which Cortex Control is up, if any — the stock app or the instrumented copy. */
export function cortexText(s: Snapshot): string {
  if (!s.cortex.running) return t('cortexText.closed')
  return s.cortex.runningInstrumented
    ? t('cortexText.instrumented')
    : t('cortexText.stock')
}

export function railText(s: Snapshot): string {
  if (!s.device.present) return t('rail.noDevice')
  if (s.daemon.state === 'starting') return t('rail.opening')
  // Somebody else's session comes first: it is why nothing else will work, and
  // it used to be the one thing nothing could see.
  if (heldButUnreachable(s)) {
    return t('rail.unreachable', { mode: s.lock!.mode, pid: String(s.lock!.pid) })
  }
  const other = heldByOther(s)
  if (other && s.daemon.state !== 'running') return `${other} ${t('rail.takeOverHint')}`
  if (s.daemon.state !== 'running') {
    const why = cleanError(s.daemon.error)
    return why ? t('rail.notConnected', { why }) : t('rail.daemonStopped')
  }
  if (clash(s)) return t('rail.clash')
  const who = s.daemon.external ? t('rail.adopted') : null
  const body = ((): string => {
    switch (sessionMode(s)) {
      case 'direct':
        return isMac(s) ? t('rail.directMac') : t('rail.directWin')
      case 'shared':
        return s.cortex.running ? t('rail.sharedApp') : t('rail.sharedNoApp')
      default:
        return s.cortex.running ? t('rail.bridgeApp') : t('rail.bridgeNoApp')
    }
  })()
  return `${who ? who + ' · ' : ''}${body} · ${cortexText(s)}`
}

export const sessionFact = (s: Snapshot): string => {
  switch (sessionMode(s)) {
    case 'direct':
      return isMac(s) ? t('fact.directMac') : t('fact.directWin')
    case 'shared':
      return t('fact.shared')
    default:
      return t('fact.bridge')
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
