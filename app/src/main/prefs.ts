import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { app } from 'electron'
import type { Prefs } from '../shared/types.js'

const FILE = (): string => join(app.getPath('userData'), 'prefs.json')

export const DEFAULTS: Prefs = {
  login: false,
  autoconnect: false,
  quitApp: false,
  verbose: true,
  autoRebuild: false,
  // Bridge, because Direct's plan QUITS Cortex Control when it is running and
  // no default should close somebody's app. The old objection to bridge — that
  // it failed on a fresh install with the app closed — is gone: the connect
  // plan opens the app itself as its first step.
  mode: 'bridge',
  repo: null,
  cortex: null,
  bench: [],
  // Off by default: levelling writes into real preset files, so the first save
  // is always a deliberate one.
  benchAutoSave: false,
  // On: the check is one request to GitHub every six hours and it never
  // installs anything on its own — macOS opens a page, Windows waits for a
  // quit. Turning it off silences the chip in the rail as well.
  updates: true,
  // Follow the machine. Someone whose Mac or PC is in Chinese gets Patchbay
  // in Chinese on first launch, with nothing to find first — the switcher in
  // Preferences is for the case where the two should differ.
  language: 'system'
}

export function load(): Prefs {
  try {
    const saved = JSON.parse(readFileSync(FILE(), 'utf8')) as Partial<Prefs>
    // 'auto' was a third mode until it was removed for naming a decision
    // rather than a session. Anyone who had it stored gets bridge, the half of
    // its behaviour that leaves Cortex Control alone.
    if ((saved.mode as string) === 'auto') saved.mode = 'bridge'
    return { ...DEFAULTS, ...saved }
  } catch {
    return { ...DEFAULTS }
  }
}

export function save(p: Prefs): void {
  try { writeFileSync(FILE(), JSON.stringify(p, null, 2) + '\n', 'utf8') } catch { /* read-only home */ }
  // "start at login" is a real OS setting, not just a stored flag
  try { app.setLoginItemSettings({ openAtLogin: p.login, openAsHidden: true }) } catch { /* unsupported */ }
}
