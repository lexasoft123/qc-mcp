import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { en } from '../src/shared/i18n/en.ts'
import { zhCN } from '../src/shared/i18n/zh-CN.ts'

/**
 * The whole app speaks both languages, and keeps speaking them.
 *
 * A feature branch that forked before the localization landed put roughly a
 * hundred and twenty hard-coded English strings into an app that is otherwise
 * translated — and because none of them went through `t()`, the typed
 * dictionaries never noticed. Types catch a MISSING translation; only a sweep
 * catches a string that was never offered for translation at all.
 */

const SRC = resolve(import.meta.dirname, '..', 'src')

function* sources(dir: string): Generator<string> {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) yield* sources(p)
    else if (/\.tsx?$/.test(name) && !name.endsWith('.d.ts')) yield p
  }
}

/** Strings that are not prose: identifiers, css, units, protocol words. */
const NOT_PROSE = [
  /^[a-z][a-zA-Z0-9]*$/,               // camelCase identifiers
  /^[a-z-]+(\s[a-z-]+)*$/,             // css class lists, kebab words
  /^[A-Za-z0-9_./\\:@-]+$/,            // paths, ids, single tokens
  /^\W+$/                              // punctuation and symbols
]

test('the dictionaries have the same keys, and no blanks', () => {
  assert.deepEqual(Object.keys(zhCN).sort(), Object.keys(en).sort())
  for (const [k, v] of Object.entries(en)) assert.ok(v.trim().length, `en ${k} is empty`)
  for (const [k, v] of Object.entries(zhCN)) assert.ok(v.trim().length, `zh-CN ${k} is empty`)
})

test('nothing was left in English by accident', () => {
  // Every string of real prose has to be a translated one. This is the check
  // that would have failed on the day the branch was written.
  assert.ok(Object.keys(en).length > 500, `only ${Object.keys(en).length} strings`)
  assert.ok(Object.keys(en).some((k) => k.startsWith('rep.')), 'the report is not localized')
  assert.ok(Object.keys(en).some((k) => k.startsWith('keys.')), 'the shortcuts are not localized')
  assert.ok(Object.keys(en).some((k) => k.startsWith('msd.')), 'the recorder is not localized')
  assert.ok(Object.keys(en).some((k) => k.startsWith('lock.')), 'the session lock is not localized')
  assert.ok(Object.keys(en).some((k) => k.startsWith('plan.')), 'the connect plan is not localized')
})

test('no renderer file renders a bare English sentence', () => {
  const bad: string[] = []
  for (const file of sources(join(SRC, 'renderer'))) {
    if (file.endsWith('i18n.tsx')) continue
    const src = readFileSync(file, 'utf8')
    // JSX text nodes: >Some words< with a space and a leading capital
    for (const m of src.matchAll(/>\s*([A-Z][a-z]+(?:\s+[A-Za-z,'’-]+){2,})\s*</g)) {
      const text = m[1].trim()
      if (NOT_PROSE.some((r) => r.test(text))) continue
      bad.push(`${file.replace(SRC, 'src')}: "${text}"`)
    }
  }
  assert.deepEqual(bad, [], `untranslated JSX text:\n  ${bad.join('\n  ')}`)
})

test('the two dictionaries agree about interpolation', () => {
  // A {name} present in one language and missing in the other renders a hole.
  for (const key of Object.keys(en) as (keyof typeof en)[]) {
    const vars = (s: string): string[] =>
      [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()
    assert.deepEqual(vars(zhCN[key]), vars(en[key]),
      `${key}: en has {${vars(en[key])}}, zh-CN has {${vars(zhCN[key])}}`)
  }
})

test('the markup the renderer understands is balanced in both', () => {
  for (const key of Object.keys(en) as (keyof typeof en)[]) {
    for (const [lang, dict] of [['en', en], ['zh-CN', zhCN]] as const) {
      const s = dict[key]
      assert.equal((s.match(/\*\*/g) ?? []).length % 2, 0, `${lang} ${key}: unbalanced **`)
      assert.equal((s.match(/`/g) ?? []).length % 2, 0, `${lang} ${key}: unbalanced backtick`)
    }
  }
})

test('product names are never translated', () => {
  const NAMES = ['Patchbay', 'qc-mcp', 'Quad Cortex', 'Cortex Control', 'Claude']
  for (const key of Object.keys(en) as (keyof typeof en)[]) {
    for (const name of NAMES) {
      if (!en[key].includes(name)) continue
      assert.ok(zhCN[key].includes(name),
        `${key}: "${name}" should survive translation — zh-CN has "${zhCN[key]}"`)
    }
  }
})
