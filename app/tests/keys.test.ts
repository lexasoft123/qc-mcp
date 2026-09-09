import { test } from 'node:test'
import assert from 'node:assert/strict'
import { SHORTCUTS, grouped, matches, shortcut, typing } from '../src/renderer/src/keys.ts'
import { t } from '../src/shared/i18n/index.ts'

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
    assert.ok(t(s.does).length > 4, `${s.cap}: "${s.does}" resolves to nothing`)
    assert.ok(t(s.short).length > 1, `${s.cap}: no short label`)
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
  assert.match(t(play.does), /play/i)
  const rec = SHORTCUTS.find((s) => s.cap === 'space')!
  assert.match(t(rec.does), /record/i)
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
  const saveAll = SHORTCUTS.find((s) => s.does === 'keys.saveAll')!
  const saveOne = SHORTCUTS.find((s) => s.does === 'keys.saveOne')!
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

test('every shortcut the views look up actually exists', () => {
  // The bug this catches: `does` became a translation key, and a lookup written
  // against the old English prose silently returned undefined — which took the
  // whole keydown handler down with it, so ⌘1–⌘5, ? and ⌘, all died at once
  // with nothing on screen to say why. `find(...)!` hid it from the compiler.
  const LOOKED_UP = [
    'keys.views', 'keys.thisList', 'prefs.open',          // App.tsx
    'keys.record', 'keys.play', 'keys.measure', 'keys.listen', 'keys.stopRun',
    'keys.apply', 'keys.undo', 'keys.saveAll', 'keys.saveOne', 'keys.addPreset'
  ] as const
  for (const k of LOOKED_UP) {
    assert.doesNotThrow(() => shortcut(k), `${k} is looked up but not declared`)
  }
})

test('an undeclared shortcut throws instead of returning undefined', () => {
  assert.throws(() => shortcut('home.connect'), /no shortcut declared/)
})

test('a shortcut that does not ask for shift does not accept it', () => {
  // ⌘⇧S used to match plain ⌘S as well, so which one ran depended on the order
  // of the `if`s in the handler — a reorder would have quietly turned
  // "save every unsaved trim" into "save this one preset".
  const saveOne = shortcut('keys.saveOne')
  const withShift = ev({ key: 's', metaKey: true, ctrlKey: true, shiftKey: true })
  assert.equal(matches(withShift, saveOne), false)
  assert.equal(matches(ev({ key: 's', metaKey: true, ctrlKey: true }), saveOne), true)
  // and the unmodified ones too: ⇧P is not Play
  assert.equal(matches(ev({ key: 'p', shiftKey: true }), shortcut('keys.play')), false)
  assert.equal(matches(ev({ key: 'p' }), shortcut('keys.play')), true)
})

test('no two shortcuts can fire on one keystroke, whatever the handler order', () => {
  // The property the ordering was standing in for.
  const combos = [false, true].flatMap((mod) => [false, true].map((shift) => ({ mod, shift })))
  for (const { mod, shift } of combos) {
    for (const k of new Set(SHORTCUTS.flatMap((s) => s.keys))) {
      const e = ev({ key: k, metaKey: mod, ctrlKey: mod, shiftKey: shift })
      const hits = SHORTCUTS.filter((s) => matches(e, s))
      const byScope = new Map<string, number>()
      for (const h of hits) byScope.set(h.scope, (byScope.get(h.scope) ?? 0) + 1)
      for (const [scope, n] of byScope) {
        assert.ok(n <= 1,
          `${mod ? 'mod+' : ''}${shift ? 'shift+' : ''}${k} fires ${n} shortcuts in ${scope}: ` +
          hits.filter((h) => h.scope === scope).map((h) => h.cap).join(', '))
      }
    }
  }
})
