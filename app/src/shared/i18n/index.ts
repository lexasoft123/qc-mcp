/*
 * Patchbay's strings, in both processes.
 *
 * The main process sets the locale from the preference (or the system) and
 * translates what it originates — the checklist, progress labels, the errors
 * that end up in a toast. The renderer sets the same locale from each
 * snapshot and translates everything else. Both are this one module; each
 * process holds its own copy, and the snapshot carries the choice across.
 *
 * No library. The app has exactly one runtime dependency and a translation
 * table needs none: `t` is a lookup plus `{name}` interpolation, and the
 * dictionaries are typed against English so a missing string is a compile
 * error rather than an English line on a Chinese screen.
 */
import { en } from './en.js'
import { zhCN } from './zh-CN.js'
import { fill, type Locale, type Vars } from './rules.js'

export { fromTag, resolveLocale, type Language, type Locale, type Vars } from './rules.js'

export type Key = keyof typeof en
export const SYSTEM = 'system'

/** Each language by its OWN name — the reader who needs this list is the one
 *  who cannot read the current language. */
export const LOCALES: { value: Locale; label: string; code: string }[] = [
  { value: 'en', label: 'English', code: 'EN' },
  { value: 'zh-CN', label: '简体中文', code: 'ZH' }
]

const DICTS: Record<Locale, Record<Key, string>> = { en, 'zh-CN': zhCN }

let current: Locale = 'en'

export function setLocale(l: Locale): void { current = l }
export const getLocale = (): Locale => current

/** The string for `key` in the current locale, with `{name}` filled in. */
export function t(key: Key, vars?: Vars): string {
  return fill(DICTS[current][key] ?? en[key], vars)
}

/** The keys that come in `_one` / `_other` pairs, by their stem. */
export type PluralKey = { [K in Key]: K extends `${infer B}_one` ? B : never }[Key]

/** `tn('clients', 3)` → "3 clients". `n` is also available to the string as `{n}`. */
export function tn(key: PluralKey, n: number, vars?: Vars): string {
  const k = `${key}_${n === 1 ? 'one' : 'other'}` as Key
  return t(k, { n, ...vars })
}
