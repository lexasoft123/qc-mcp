import { LanguageSwitcher } from '@singz/ui'
import type { Snapshot } from '@shared/types'
import type { Language as Lang } from '@shared/i18n'
import { act } from '../store.js'
import { LOCALES, t, type Key } from '../i18n.js'
import { isMac } from '../derive.js'
import { FLAGS } from './Flags.js'

/**
 * The app's language switcher: the kit's control fed with this app's
 * languages, flags and words. One definition, two places — the flag alone in
 * the title bar, the full pill in Preferences — so they cannot disagree about
 * what is offered or what "System" resolves to.
 */
export function Language({ snap, compact = false }: { snap: Snapshot; compact?: boolean }): React.JSX.Element {
  const os = isMac(snap) ? 'macOS' : 'Windows'
  // Each language by its own name, with its name in THIS language underneath
  // when the two differ — "English / 英语" on a Chinese screen, and no
  // redundant "English / English" on an English one.
  const options = LOCALES.map((l) => {
    const named = t(`lang.${l.value}` as Key)
    return { value: l.value, label: l.label, code: l.code, flag: FLAGS[l.value], hint: named !== l.label ? named : undefined }
  })
  const systemName = LOCALES.find((l) => l.value === snap.systemLocale)?.label ?? ''
  return (
    <LanguageSwitcher
      options={options}
      value={snap.prefs.language}
      onChange={(language) => { void act(() => window.patchbay.setPrefs({ language: language as Lang })) }}
      system={{
        label: t('lang.system'),
        hint: t('lang.systemHint', { os, name: systemName }),
        resolves: snap.systemLocale,
        badge: t('lang.auto')
      }}
      compact={compact}
      aria-label={t('prefs.language')}
    />
  )
}
