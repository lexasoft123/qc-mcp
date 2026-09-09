import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  appliedIds, attribute, focusIndex, forget, forgetApplied, isDirty, leave, markSaved, note,
  slotId, unsavedIds
} from '../src/renderer/src/bench.ts'
import type { BenchSlot, ReportRow } from '../src/shared/types.ts'

const slot = (p: Partial<BenchSlot> & { name: string }): BenchSlot =>
  ({ folderKey: 'f1', position: 0, cloudId: '', scene: null, ...p })

// Two Downloads presets: both position 0, told apart only by cloud id.
const dl1 = slot({ name: 'Cloud A', folderKey: 'downloads', position: 0, cloudId: 'c-aaa' })
const dl2 = slot({ name: 'Cloud B', folderKey: 'downloads', position: 0, cloudId: 'c-bbb' })
const s3 = slot({ name: 'Third', position: 3 })
const s7 = slot({ name: 'Seventh', position: 7 })

test('two Downloads presets are two slots, not one', () => {
  assert.notEqual(slotId(dl1), slotId(dl2))
  assert.equal(slotId(s3), 'f1:3')
  assert.equal(focusIndex([dl1, dl2, s3], slotId(dl2)), 1)
  assert.equal(focusIndex([dl1, dl2, s3], null), -1)
  assert.equal(focusIndex([dl1, s3], slotId(dl2)), -1, 'a dropped focus is no focus')
})

test('one written record: noted, saved, forgotten', () => {
  let w = note({}, 'a', 2.5, 'apply')
  w = note(w, 'b', null, 'ear')
  assert.deepEqual(unsavedIds(w).sort(), ['a', 'b'])
  assert.ok(isDirty(w, 'a') && isDirty(w, 'b'))
  assert.equal(isDirty(w, null), false)
  assert.equal(isDirty(w, 'nope'), false)

  w = markSaved(w, 'a')
  assert.deepEqual(unsavedIds(w), ['b'])
  assert.equal(isDirty(w, 'a'), false, 'saved is not dirty')
  assert.ok(w.a, 'but the record stays, so the row can say "saved"')
  assert.equal(markSaved(w, 'ghost'), w, 'saving nothing changes nothing')

  w = forget(w, 'b')
  assert.deepEqual(Object.keys(w), ['a'])
  assert.equal(forget(w, 'ghost'), w)
})

test('a fresh note on a saved id makes it unsaved again', () => {
  let w = markSaved(note({}, 'a', 1, 'apply'), 'a')
  w = note(w, 'a', 1.5, 'apply')
  assert.equal(w.a.saved, false)
  assert.equal(w.a.db, 1.5)
})

test('Undo trims forgets what Apply wrote and nothing else', () => {
  const w = note(note(note({}, 'a', 2, 'apply'), 'b', null, 'ear'), 'c', -1, 'apply')
  assert.deepEqual(appliedIds(w).sort(), ['a', 'c'])
  assert.deepEqual(Object.keys(forgetApplied(w)), ['b'])
})

test('leaving a preset loses by-ear edits and keeps Apply trims', () => {
  const w = note(note(note({}, 'a', 2, 'apply'), 'b', null, 'ear'), 'c', null, 'scenes')

  const kept = leave(w, 'a')
  assert.equal(kept.lost, null, 'an Apply trim is a number: re-writable before save')
  assert.equal(kept.next, w)

  const ear = leave(w, 'b')
  assert.equal(ear.lost?.source, 'ear')
  assert.equal(ear.next.b, undefined)

  const sc = leave(w, 'c')
  assert.equal(sc.lost?.source, 'scenes')

  assert.equal(leave(w, null).lost, null)
  assert.equal(leave(markSaved(w, 'b'), 'b').lost, null, 'saved edits are in the file')
})

const row = (position: number, extra: Partial<ReportRow> = {}): ReportRow =>
  ({ position, measured: -20, ...extra })

test('a measured event is attributed by the index the run announced', () => {
  const sent = [dl1, dl2, s3]
  assert.equal(attribute(sent, 0, row(0)), slotId(dl1))
  assert.equal(attribute(sent, 1, row(0)), slotId(dl2), 'same position, different slot')
  assert.equal(attribute(sent, 2, row(3)), slotId(s3))
})

test('when index and position disagree, a unique position still answers', () => {
  const sent = [dl1, s3, s7]
  // the measuring event for s7 was missed; the index still points at s3
  assert.equal(attribute(sent, 1, row(7)), slotId(s7))
  assert.equal(attribute(sent, null, row(3)), slotId(s3))
})

test('an ambiguous row is nobody\'s, never a guess', () => {
  const sent = [dl1, dl2, s3]
  assert.equal(attribute(sent, 2, row(0)), null, 'two Downloads slots share position 0')
  assert.equal(attribute(sent, null, row(0)), null)
  assert.equal(attribute(sent, 9, row(9)), null, 'nothing at that index or position')
  assert.equal(attribute([], 0, row(0)), null)
})
