import { test } from 'node:test'
import assert from 'node:assert/strict'
import { SHORTCUTS, grouped, matches, typing } from '../src/renderer/src/keys.ts'

/**
 * The legend and the handlers used to be written separately, and had already
 * drifted: the strip along the bottom of Leveling listed five bindings and
 * omitted `space`, the one a player uses most. These hold the single list
 * honest — every claim it makes about itself has to be true.
 */

const ev = (init: Partial<KeyboardEvent> & { key: string }): KeyboardEvent =>
  ({ metaKey: false, ctrlKey: false, altKey: false, shiftKey: false,
     ...init } as KeyboardEvent)

test('every shortcut has a cap, a description and a group', () => {
  for (const s of SHORTCUTS) {
    assert.ok(s.cap.length > 0, JSON.stringify(s))
    assert.ok(s.does.length > 8, `${s.cap}: "${s.does}" says too little`)
    assert.ok(s.group.length > 0, s.cap)
    assert.ok(s.local || s.keys.length > 0, `${s.cap} claims a binding with no keys`)
  }
})

test('the keys a player actually needs are all bound', () => {
  // The three the review named, plus the one the old legend forgot.
  const caps = SHORTCUTS.filter((s) => !s.local).map((s) => s.cap)
  for (const need of ['space', 'P', 'M', '?']) {
    assert.ok(caps.includes(need), `${need} is not bound: ${caps.join(' ')}`)
  }
  const play = SHORTCUTS.find((s) => s.cap === 'P')!
  assert.match(play.does, /play/i)
  const rec = SHORTCUTS.find((s) => s.cap === 'space')!
  assert.match(rec.does, /record/i)
})

test('space is in the list the legend is generated from', () => {
  // The actual regression: it existed as a binding and not as a claim.
  const legend = SHORTCUTS.filter((s) => s.scope === 'leveling' && !s.local).slice(0, 7)
  assert.ok(legend.some((s) => s.cap === 'space'),
    'the legend would omit the binding a player uses most')
})

test('no two bindings collide on the same keystroke', () => {
  const seen = new Map<string, string>()
  for (const s of SHORTCUTS) {
    if (s.local) continue
    for (const k of s.keys) {
      const id = `${s.scope}|${s.mod ? 'mod+' : ''}${s.shift ? 'shift+' : ''}${k}`
      // 1-9 legitimately appear in both an app-level and a bench-level binding,
      // told apart by the modifier; anything else sharing a keystroke is a bug.
      const prev = seen.get(id)
      assert.equal(prev, undefined, `${id} is claimed by both "${prev}" and "${s.does}"`)
      seen.set(id, s.does)
    }
  }
})

test('a modified key never fires an unmodified binding', () => {
  const play = SHORTCUTS.find((s) => s.cap === 'P')!
  assert.equal(matches(ev({ key: 'p' }), play), true)
  assert.equal(matches(ev({ key: 'p', metaKey: true }), play), false)
  assert.equal(matches(ev({ key: 'p', ctrlKey: true }), play), false)
  assert.equal(matches(ev({ key: 'p', altKey: true }), play), false)
})

test('shift is respected where a binding asks for it', () => {
  const saveAll = SHORTCUTS.find((s) => s.does.startsWith('Save every'))!
  const saveOne = SHORTCUTS.find((s) => s.does.startsWith('Save the open'))!
  const withShift = ev({ key: 's', metaKey: true, ctrlKey: true, shiftKey: true })
  const without = ev({ key: 's', metaKey: true, ctrlKey: true })
  assert.equal(matches(withShift, saveAll), true)
  assert.equal(matches(without, saveAll), false)
  assert.equal(matches(without, saveOne), true)
})

test('a caret in a field owns the keystroke', () => {
  for (const tag of ['INPUT', 'TEXTAREA', 'SELECT']) {
    assert.equal(typing({ target: { tagName: tag } } as unknown as KeyboardEvent), true, tag)
  }
  assert.equal(typing({ target: { tagName: 'DIV', isContentEditable: true } } as unknown as KeyboardEvent), true)
  assert.equal(typing({ target: { tagName: 'BUTTON' } } as unknown as KeyboardEvent), false)
  assert.equal(typing({ target: null } as unknown as KeyboardEvent), false)
})

test('the sheet groups every shortcut, in declaration order', () => {
  const rows = grouped().flatMap(([, r]) => r)
  assert.equal(rows.length, SHORTCUTS.length, 'the sheet would hide a binding')
  const groups = grouped().map(([g]) => g)
  assert.deepEqual(groups, [...new Set(SHORTCUTS.map((s) => s.group))])
})

test('scoping the sheet keeps the app-wide keys visible', () => {
  const bench = grouped('leveling').flatMap(([, r]) => r)
  assert.ok(bench.some((s) => s.scope === 'app'), 'a bench-only sheet loses ⌘1–⌘5 and ?')
  assert.ok(bench.every((s) => s.scope !== 'app' ? s.scope === 'leveling' : true))
})
