import type { BenchSlot, ReportRow } from '@shared/types'

/**
 * The bench's bookkeeping, without React.
 *
 * Everything the Leveling view knows about a preset — its report row, the
 * correction proposed for it, whether a trim reached the device, whether that
 * trim reached the file — is keyed here by ONE identity, and the record of
 * what was written has ONE shape. It used to be five maps keyed by bench
 * position next to a `focus` that was an index into the bench array, and two
 * of those maps (`dirty`, `applied`) meant the same thing without ever being
 * reconciled.
 *
 * No `window`, no i18n: `npm test` reaches this file directly.
 */

/**
 * A bench slot's identity.
 *
 * Downloads presets all report position 0 — their real key is the cloud id —
 * so keying on folder+position alone collapses a whole cloud folder into one
 * slot, in selection AND in React's reconciler.
 */
export const slotId = (s: { folderKey: string; position: number; cloudId: string }): string =>
  s.cloudId || `${s.folderKey}:${s.position}`

export const focusIndex = (bench: BenchSlot[], focusId: string | null): number =>
  focusId === null ? -1 : bench.findIndex((b) => slotId(b) === focusId)

/**
 * Something this session changed on the device that the preset FILE does not
 * yet hold. One record, one yellow dot, one meaning.
 *
 * `db` is the trim as a delta from the file, when it is known: an Apply knows
 * exactly what it wrote and can write it again after a reload; a by-ear knob
 * gesture or a scene-levelling pass only knows that the device now differs.
 */
export interface Written {
  db: number | null
  source: 'apply' | 'ear' | 'auto' | 'scenes'
  /** The trim has since been saved into the preset. Stays in the record so the
   *  row can say "saved" — a reload would then find it in the file. */
  saved: boolean
}

export type WrittenMap = Record<string, Written>

export const note = (w: WrittenMap, id: string, db: number | null, source: Written['source']): WrittenMap =>
  ({ ...w, [id]: { db, source, saved: false } })

export const markSaved = (w: WrittenMap, id: string): WrittenMap =>
  w[id] ? { ...w, [id]: { ...w[id], saved: true } } : w

export const forget = (w: WrittenMap, id: string): WrittenMap => {
  if (!w[id]) return w
  const { [id]: _gone, ...rest } = w
  return rest
}

/** Drop every record that came from an Apply — what `revertLevels` undoes. */
export const forgetApplied = (w: WrittenMap): WrittenMap =>
  Object.fromEntries(Object.entries(w).filter(([, x]) => x.source !== 'apply'))

/** The loaded preset differs from its file. */
export const isDirty = (w: WrittenMap, id: string | null): boolean =>
  id !== null && w[id] !== undefined && !w[id].saved

export const unsavedIds = (w: WrittenMap): string[] =>
  Object.keys(w).filter((id) => !w[id].saved)

export const appliedIds = (w: WrittenMap): string[] =>
  Object.keys(w).filter((id) => w[id].source === 'apply')

/**
 * Leaving a preset: a recall reloads the next one from flash, so whatever this
 * one held only in the device's working grid is gone.
 *
 * An Apply's trim is a known number and can be written again before a save
 * (that is what Save-all does), so it stays. A by-ear or per-scene edit cannot
 * be reconstructed — it is dropped, and returned so the caller can say so.
 */
export const leave = (w: WrittenMap, id: string | null): { next: WrittenMap; lost: Written | null } => {
  if (id === null) return { next: w, lost: null }
  const x = w[id]
  if (!x || x.saved || x.source === 'apply') return { next: w, lost: null }
  return { next: forget(w, id), lost: x }
}

/**
 * Which slot a `measured` event is about.
 *
 * The service names a row only by `position` (leveling.py builds it from the
 * request), and every Downloads preset has position 0. But the run measures
 * `sent` in order and announces each index in a `measuring` event first, so the
 * index is the primary key and the position is the check on it. When the two
 * disagree — an event arrived out of order — a position that names exactly one
 * slot still answers; anything else is `null`, never a guess.
 */
export function attribute(sent: BenchSlot[], index: number | null, row: ReportRow): string | null {
  const at = index !== null ? sent[index] : undefined
  if (at && at.position === row.position) return slotId(at)
  const byPos = sent.filter((b) => b.position === row.position)
  return byPos.length === 1 ? slotId(byPos[0]) : null
}

/**
 * The one accented button.
 *
 * Exactly one control on the screen is primary at a time, and it walks the
 * job: record a riff, measure, apply, save. A first-time user and a returning
 * one get the same obvious next action without a paragraph explaining the
 * screen — and nothing else may claim the accent.
 */
export type Step = 'rec' | 'measure' | 'apply' | 'save' | null

export function nextStep(f: {
  hasTake: boolean
  measured: number
  selected: number
  unsaved: number
  /** A run owns the device: nothing is the next thing to do. */
  running: boolean
}): Step {
  if (f.running) return null
  if (f.unsaved > 0) return 'save'
  if (!f.hasTake) return 'rec'
  if (f.measured === 0 || f.selected === 0) return 'measure'
  return 'apply'
}

/**
 * What Save-all has to do, per slot.
 *
 * The naive version — open each preset, save it — writes nothing: the trim
 * lives in the device's working grid, so recalling the preset reloads the
 * file OVER the trim and saves it back onto itself. The loaded preset can be
 * saved as it stands. Any other slot's Apply trim is a known number and is
 * applied again after the recall, then saved. A by-ear or per-scene edit
 * elsewhere cannot be reconstructed and is not offered — it is only ever
 * saved while its preset is loaded.
 */
export type SaveAction =
  | { id: string; how: 'save' }
  | { id: string; how: 'reapply'; db: number }

export function savePlan(w: WrittenMap, loadedId: string | null, ids: string[]): SaveAction[] {
  const out: SaveAction[] = []
  // the loaded one first: its edits exist only until the next recall
  if (loadedId !== null && ids.includes(loadedId) && isDirty(w, loadedId)) {
    out.push({ id: loadedId, how: 'save' })
  }
  for (const id of ids) {
    if (id === loadedId) continue
    const x = w[id]
    if (!x || x.saved) continue
    if (x.source === 'apply' && x.db !== null) out.push({ id, how: 'reapply', db: x.db })
  }
  return out
}
