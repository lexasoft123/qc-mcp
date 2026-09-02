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
  // 'auto', not 'bridge'. Bridge is the preferred mode WHEN Cortex Control is
  // up, but as a default it makes the daemon refuse to start on a fresh
  // install with the app closed — which is the first thing the Connect button
  // on Home does. Auto picks bridge/shared when the app is running and direct
  // when it is not.
  mode: 'auto',
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
    return { ...DEFAULTS, ...(JSON.parse(readFileSync(FILE(), 'utf8')) as Partial<Prefs>) }
  } catch {
    return { ...DEFAULTS }
  }
}

export function save(p: Prefs): void {
  try { writeFileSync(FILE(), JSON.stringify(p, null, 2) + '\n', 'utf8') } catch { /* read-only home */ }
  // "start at login" is a real OS setting, not just a stored flag
  try { app.setLoginItemSettings({ openAtLogin: p.login, openAsHidden: true }) } catch { /* unsupported */ }
}
