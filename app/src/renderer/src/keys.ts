/**
 * Every shortcut, declared once.
 *
 * The bindings and the legend used to be written in two places, and they had
 * already drifted: the strip along the bottom of Leveling listed five keys and
 * left out `space`, which is the one a player uses most and the only one that
 * works with a guitar in both hands. A list the handler and the help sheet both
 * read cannot say one thing and do another.
 */

import type { Key } from '@shared/i18n'

export type Scope = 'app' | 'leveling'

export interface Shortcut {
  /** What `KeyboardEvent.key` must be, lowercased. A list means any of them. */
  keys: string[]
  /** Needs ⌘ on macOS / Ctrl elsewhere. */
  mod?: boolean
  shift?: boolean
  /** How it is drawn in the sheet. */
  cap: string
  /** What it does, as a string key — the sheet and the legend both read it, so
   *  a shortcut cannot be described in one language and listed in another. */
  does: Key
  /** The two or three words the bottom-of-screen legend uses. */
  short: Key
  scope: Scope
  group: Key
  /** Shown in the sheet but handled by the control itself, not the map. */
  local?: boolean
}

const IS_MAC = typeof navigator !== 'undefined' && /Mac/i.test(navigator.platform)
export const MOD = IS_MAC ? '⌘' : 'Ctrl'

export const SHORTCUTS: Shortcut[] = [
  // ── getting around ────────────────────────────────────────────────────
  { keys: ['1', '2', '3', '4', '5'], mod: true, cap: `${MOD}1–${MOD}5`,
    does: 'keys.views', short: 'keys.views.short', scope: 'app', group: 'keys.group.around' },
  { keys: ['?', '/'], cap: '?', does: 'keys.thisList', short: 'keys.thisList', scope: 'app', group: 'keys.group.around' },
  { keys: [','], mod: true, cap: `${MOD},`, does: 'prefs.open', short: 'prefs.open', scope: 'app', group: 'keys.group.around' },

  // ── the recorder ──────────────────────────────────────────────────────
  { keys: [' '], cap: 'space', does: 'keys.record', short: 'keys.record.short', scope: 'leveling', group: 'keys.group.riff' },
  { keys: ['p'], cap: 'P', does: 'keys.play', short: 'keys.play.short', scope: 'leveling', group: 'keys.group.riff' },

  // ── the bench ─────────────────────────────────────────────────────────
  { keys: ['arrowleft', 'arrowright'], cap: '← →', does: 'keys.preset', short: 'keys.preset', scope: 'leveling', group: 'keys.group.bench' },
  { keys: ['1', '2', '3', '4', '5', '6', '7', '8', '9'], cap: '1–9',
    does: 'keys.slot', short: 'keys.slot.short', scope: 'leveling', group: 'keys.group.bench' },
  { keys: ['arrowup', 'arrowdown'], cap: '↑ ↓', does: 'keys.scene', short: 'keys.scene', scope: 'leveling', group: 'keys.group.bench' },
  { keys: ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'], cap: 'A–H', does: 'keys.jump', short: 'keys.jump', scope: 'leveling', group: 'keys.group.bench' },
  { keys: ['-', '_', '=', '+'], cap: '− +', does: 'keys.levelFull', short: 'keys.level', scope: 'leveling', group: 'keys.group.bench' },
  { keys: ['n'], mod: true, cap: `${MOD}N`, does: 'keys.addPreset', short: 'keys.addPreset.short', scope: 'leveling', group: 'keys.group.bench' },

  // ── measuring ─────────────────────────────────────────────────────────
  { keys: ['m'], cap: 'M', does: 'keys.measure', short: 'keys.measure.short', scope: 'leveling', group: 'keys.group.measuring' },
  { keys: ['escape'], cap: 'esc', does: 'keys.stopRun', short: 'keys.stopRun.short', scope: 'leveling', group: 'keys.group.measuring' },
  { keys: ['l'], cap: 'L', does: 'keys.listen', short: 'keys.listen.short', scope: 'leveling', group: 'keys.group.measuring' },

  // ── writing ───────────────────────────────────────────────────────────
  { keys: ['enter'], mod: true, cap: `${MOD}↵`, does: 'keys.apply', short: 'keys.apply.short', scope: 'leveling', group: 'keys.group.writing' },
  { keys: ['z'], mod: true, cap: `${MOD}Z`, does: 'keys.undo', short: 'keys.undo.short', scope: 'leveling', group: 'keys.group.writing' },
  { keys: ['s'], mod: true, cap: `${MOD}S`, does: 'keys.saveOne', short: 'keys.save', scope: 'leveling', group: 'keys.group.writing' },
  { keys: ['s'], mod: true, shift: true, cap: `${MOD}⇧S`, does: 'keys.saveAll', short: 'keys.saveAll.short', scope: 'leveling', group: 'keys.group.writing' },

  // ── in a field ────────────────────────────────────────────────────────
  { keys: [], cap: '↑ ↓', does: 'keys.nudge', short: 'keys.nudge', scope: 'leveling', group: 'keys.group.report', local: true },
  { keys: [], cap: '↵ esc', does: 'keys.commit', short: 'keys.commit', scope: 'leveling', group: 'keys.group.report', local: true }
]

/** Does this event mean that shortcut? */
export function matches(e: KeyboardEvent, s: Shortcut): boolean {
  if (s.local || s.keys.length === 0) return false
  const mod = IS_MAC ? e.metaKey : e.ctrlKey
  if (Boolean(s.mod) !== mod) return false
  if (s.shift !== undefined && Boolean(s.shift) !== e.shiftKey) return false
  if (!s.mod && (e.metaKey || e.ctrlKey || e.altKey)) return false
  return s.keys.includes(e.key.toLowerCase())
}

/**
 * Is the caret somewhere that owns the keystroke?
 *
 * A shortcut that fires while somebody is typing a preset name is a bug, and
 * `space` firing the footswitch mid-word is the worst of them.
 */
export function typing(e: KeyboardEvent): boolean {
  const el = e.target as HTMLElement | null
  if (!el) return false
  if (el.isContentEditable) return true
  return /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)
}

/** The sheet's shape: groups in declaration order, each with its rows. */
export function grouped(scope?: Scope): [Key, Shortcut[]][] {
  const out: [Key, Shortcut[]][] = []
  for (const s of SHORTCUTS) {
    if (scope && s.scope !== scope && s.scope !== 'app') continue
    const found = out.find(([g]) => g === s.group)
    if (found) found[1].push(s)
    else out.push([s.group, [s]])
  }
  return out
}
