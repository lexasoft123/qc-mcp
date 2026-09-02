import type { ClientTarget, SessionMode, Snapshot } from '@shared/types'
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

/** "bridge" / "shared" / "direct", in the current language. */
export const sessionWord = (s: Snapshot): string => t(`session.${sessionMode(s)}`)

export function railText(s: Snapshot): string {
  if (!s.device.present) return t('rail.noDevice')
  if (s.daemon.state !== 'running') return s.daemon.error ?? t('rail.daemonStopped')
  if (clash(s)) return t('rail.clash')
  switch (sessionMode(s)) {
    case 'direct':
      return isMac(s) ? t('rail.directMac') : t('rail.directWin')
    case 'shared':
      return s.cortex.running ? t('rail.sharedApp') : t('rail.sharedNoApp')
    default:
      return s.cortex.running ? t('rail.bridgeApp') : t('rail.bridgeNoApp')
  }
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
