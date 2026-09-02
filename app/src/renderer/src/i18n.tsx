import { Fragment, type ReactNode } from 'react'
import { t } from '@shared/i18n'

export { t, tn, LOCALES } from '@shared/i18n'
export type { Key } from '@shared/i18n'

/**
 * The strings' only markup, turned into elements: `**bold**` → <b>, and
 * `` `code` `` → <code>. Anything else in a string is text. Kept this small on
 * purpose — a translator should never have to write JSX, and a string with an
 * unbalanced marker still renders, just with the marker showing.
 */
export function rich(text: string): ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)
  if (parts.length === 1) return text
  return parts.map((p, i) =>
    p.startsWith('**') && p.endsWith('**')
      ? <b key={i}>{p.slice(2, -2)}</b>
      : p.startsWith('`') && p.endsWith('`')
        ? <code key={i}>{p.slice(1, -1)}</code>
        : <Fragment key={i}>{p}</Fragment>
  )
}

/** `<T k="home.sharedWarn" />` — t() with the markup rendered. */
export function T({ k, vars }: { k: Parameters<typeof t>[0]; vars?: Parameters<typeof t>[1] }): React.JSX.Element {
  return <>{rich(t(k, vars))}</>
}
