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
  mode: 'bridge',
  repo: null,
  cortex: null
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
