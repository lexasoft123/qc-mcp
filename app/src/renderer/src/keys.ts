/**
 * Every shortcut, declared once.
 *
 * The bindings and the legend used to be written in two places, and they had
 * already drifted: the strip along the bottom of Leveling listed five keys and
 * left out `space`, which is the one a player uses most and the only one that
 * works with a guitar in both hands. A list the handler and the help sheet both
 * read cannot say one thing and do another.
 */

export type Scope = 'app' | 'leveling'

export interface Shortcut {
  /** What `KeyboardEvent.key` must be, lowercased. A list means any of them. */
  keys: string[]
  /** Needs ⌘ on macOS / Ctrl elsewhere. */
  mod?: boolean
  shift?: boolean
  /** How it is drawn in the sheet. */
  cap: string
  does: string
  scope: Scope
  group: string
  /** Shown in the sheet but handled by the control itself, not the map. */
  local?: boolean
}

const IS_MAC = typeof navigator !== 'undefined' && /Mac/i.test(navigator.platform)
export const MOD = IS_MAC ? '⌘' : 'Ctrl'

export const SHORTCUTS: Shortcut[] = [
  // ── getting around ────────────────────────────────────────────────────
  { keys: ['1', '2', '3', '4', '5'], mod: true, cap: `${MOD}1–${MOD}5`,
    does: 'Home, Console, Leveling, Setup, Logs', scope: 'app', group: 'Getting around' },
  { keys: ['?', '/'], cap: '?', does: 'This list', scope: 'app', group: 'Getting around' },
  { keys: [','], mod: true, cap: `${MOD},`, does: 'Preferences', scope: 'app', group: 'Getting around' },

  // ── the recorder ──────────────────────────────────────────────────────
  { keys: [' '], cap: 'space', does: 'Arm · stop · discard the recorder',
    scope: 'leveling', group: 'The riff' },
  { keys: ['p'], cap: 'P', does: 'Play the riff through this preset — again to stop',
    scope: 'leveling', group: 'The riff' },

  // ── the bench ─────────────────────────────────────────────────────────
  { keys: ['arrowleft', 'arrowright'], cap: '← →', does: 'Previous / next preset',
    scope: 'leveling', group: 'The bench' },
  { keys: ['1', '2', '3', '4', '5', '6', '7', '8', '9'], cap: '1–9',
    does: 'Jump straight to a bench slot', scope: 'leveling', group: 'The bench' },
  { keys: ['arrowup', 'arrowdown'], cap: '↑ ↓', does: 'Scene down / up',
    scope: 'leveling', group: 'The bench' },
  { keys: ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'], cap: 'A–H', does: 'Jump to a scene',
    scope: 'leveling', group: 'The bench' },
  { keys: ['-', '_', '=', '+'], cap: '− +', does: 'Lane level ∓0.5 dB (⇧ for 0.1)',
    scope: 'leveling', group: 'The bench' },
  { keys: ['n'], mod: true, cap: `${MOD}N`, does: 'Add a preset to the bench',
    scope: 'leveling', group: 'The bench' },

  // ── measuring ─────────────────────────────────────────────────────────
  { keys: ['m'], cap: 'M', does: 'Measure every preset on the bench',
    scope: 'leveling', group: 'Measuring' },
  { keys: ['escape'], cap: 'esc', does: 'Stop after the preset being measured',
    scope: 'leveling', group: 'Measuring' },
  { keys: ['l'], cap: 'L', does: 'Listen to the set, one preset after another',
    scope: 'leveling', group: 'Measuring' },

  // ── writing ───────────────────────────────────────────────────────────
  { keys: ['enter'], mod: true, cap: `${MOD}↵`, does: 'Apply the proposals to the device',
    scope: 'leveling', group: 'Writing' },
  { keys: ['z'], mod: true, cap: `${MOD}Z`, does: 'Undo every trim this session',
    scope: 'leveling', group: 'Writing' },
  { keys: ['s'], mod: true, cap: `${MOD}S`, does: 'Save the open preset',
    scope: 'leveling', group: 'Writing' },
  { keys: ['s'], mod: true, shift: true, cap: `${MOD}⇧S`, does: 'Save every unsaved trim',
    scope: 'leveling', group: 'Writing' },

  // ── in a field ────────────────────────────────────────────────────────
  { keys: [], cap: '↑ ↓', does: 'Nudge a proposal ±0.5 dB', scope: 'leveling',
    group: 'In the report', local: true },
  { keys: [], cap: '↵ esc', does: 'Commit / cancel an edit', scope: 'leveling',
    group: 'In the report', local: true }
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
export function grouped(scope?: Scope): [string, Shortcut[]][] {
  const out: [string, Shortcut[]][] = []
  for (const s of SHORTCUTS) {
    if (scope && s.scope !== scope && s.scope !== 'app') continue
    const found = out.find(([g]) => g === s.group)
    if (found) found[1].push(s)
    else out.push([s.group, [s]])
  }
  return out
}
