/*
 * The locale decisions, with no imports: which system tag maps to which
 * dictionary, and how a preference resolves against the machine's list.
 * Split from index.ts so `node --test` can load them without resolving the
 * dictionaries' import specifiers.
 */

export type Locale = 'en' | 'zh-CN'
/** What the preference stores: a locale, or `system` to follow the machine. */
export type Language = Locale | 'system'

/**
 * The locale a system tag maps to, or null when Patchbay has nothing for it.
 *
 * Simplified Chinese is offered to `zh`, `zh-CN`, `zh-SG` and any `zh-Hans`
 * form. Traditional variants (`zh-Hant`, `zh-TW`, `zh-HK`, `zh-MO`) are
 * deliberately NOT mapped: a Traditional reader gets Simplified only by
 * choosing it, not by being handed it.
 */
export function fromTag(tag: string): Locale | null {
  const parts = tag.toLowerCase().replace(/_/g, '-').split('-')
  const lang = parts[0]
  if (lang === 'en') return 'en'
  if (lang === 'zh') {
    if (parts.includes('hant')) return null
    if (parts.includes('hans')) return 'zh-CN'
    const region = parts.slice(1).find((p) => p.length === 2)
    return region === undefined || region === 'cn' || region === 'sg' ? 'zh-CN' : null
  }
  return null
}

/**
 * The locale to use: the preference when it names one, else the first of the
 * system's preferred languages Patchbay can speak, else English.
 */
export function resolveLocale(language: Language, systemTags: readonly string[]): Locale {
  if (language !== 'system') return language
  for (const tag of systemTags) {
    const l = fromTag(tag)
    if (l) return l
  }
  return 'en'
}

export type Vars = Record<string, string | number>

/** `{name}` → vars.name. An unknown name is left as it was, visibly. */
export function fill(text: string, vars?: Vars): string {
  if (!vars) return text
  return text.replace(/\{(\w+)\}/g, (m, name: string) => (name in vars ? String(vars[name]) : m))
}
