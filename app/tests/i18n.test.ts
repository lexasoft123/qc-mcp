/*
 * The locale rules and the two dictionaries. Run with `npm test`.
 *
 * The dictionaries are imported directly rather than through index.ts, whose
 * `.js` specifiers only a bundler resolves; zh-CN.ts's own import is
 * type-only and stripped.
 */
import { deepEqual, equal, ok } from 'node:assert/strict'
import { describe, it } from 'node:test'
import { fill, fromTag, resolveLocale } from '../src/shared/i18n/rules.ts'
import { en } from '../src/shared/i18n/en.ts'
import { zhCN } from '../src/shared/i18n/zh-CN.ts'

describe('fromTag', () => {
  it('maps English and Simplified Chinese in every spelling the platforms use', () => {
    equal(fromTag('en'), 'en')
    equal(fromTag('en-US'), 'en')
    equal(fromTag('en_GB'), 'en')
    equal(fromTag('zh'), 'zh-CN')
    equal(fromTag('zh-CN'), 'zh-CN')
    equal(fromTag('zh_CN'), 'zh-CN')
    equal(fromTag('zh-Hans'), 'zh-CN')
    equal(fromTag('zh-Hans-CN'), 'zh-CN')
    equal(fromTag('zh-Hans-SG'), 'zh-CN')
    equal(fromTag('zh-SG'), 'zh-CN')
  })

  it('does not hand Simplified to a Traditional reader', () => {
    equal(fromTag('zh-TW'), null)
    equal(fromTag('zh-Hant'), null)
    equal(fromTag('zh-Hant-TW'), null)
    equal(fromTag('zh-HK'), null)
    equal(fromTag('zh-MO'), null)
  })

  it('has nothing for other languages', () => {
    equal(fromTag('ja'), null)
    equal(fromTag('de-DE'), null)
    equal(fromTag(''), null)
  })
})

describe('resolveLocale', () => {
  it('honours an explicit choice over the system', () => {
    equal(resolveLocale('en', ['zh-CN']), 'en')
    equal(resolveLocale('zh-CN', ['en-US']), 'zh-CN')
  })

  it('walks the system list in order and takes the first it can speak', () => {
    equal(resolveLocale('system', ['zh-Hans-CN', 'en-US']), 'zh-CN')
    equal(resolveLocale('system', ['ja', 'zh-CN']), 'zh-CN')
    equal(resolveLocale('system', ['zh-TW', 'en']), 'en')
  })

  it('falls back to English', () => {
    equal(resolveLocale('system', []), 'en')
    equal(resolveLocale('system', ['ja', 'ko']), 'en')
  })
})

describe('fill', () => {
  it('interpolates and leaves an unknown name visible', () => {
    equal(fill('{n} of {total}', { n: 1, total: 3 }), '1 of 3')
    equal(fill('{n} of {total}', { n: 1 }), '1 of {total}')
    equal(fill('plain'), 'plain')
  })
})

describe('the dictionaries', () => {
  const keys = Object.keys(en) as (keyof typeof en)[]
  const holes = (s: string): string[] => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort()

  it('zh-CN has every English key, and nothing empty', () => {
    for (const k of keys) ok(typeof zhCN[k] === 'string' && zhCN[k].trim() !== '', `zh-CN lacks ${k}`)
    deepEqual(Object.keys(zhCN).sort(), [...keys].sort())
  })

  it('every {placeholder} in English is in the translation', () => {
    for (const k of keys) deepEqual(holes(zhCN[k]), holes(en[k]), `placeholders differ on ${k}`)
  })

  it('keeps the markup pairs balanced', () => {
    for (const k of keys) {
      for (const s of [en[k], zhCN[k]]) {
        equal((s.match(/\*\*/g) ?? []).length % 2, 0, `unbalanced ** in ${k}`)
        equal((s.match(/`/g) ?? []).length % 2, 0, `unbalanced \` in ${k}`)
        equal((s.match(/<code>/g) ?? []).length, (s.match(/<\/code>/g) ?? []).length, `unbalanced <code> in ${k}`)
      }
    }
  })

  it('every plural key has its pair', () => {
    for (const k of keys) {
      if (k.endsWith('_one')) ok(keys.includes(`${k.slice(0, -4)}_other` as keyof typeof en), `${k} has no _other`)
      if (k.endsWith('_other')) ok(keys.includes(`${k.slice(0, -6)}_one` as keyof typeof en), `${k} has no _one`)
    }
  })
})
