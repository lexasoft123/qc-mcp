import type { ReactNode } from 'react'
import { Button, StatusDot } from '@singz/ui'
import type { UpdateState } from '@shared/types'
import { t } from '../i18n.js'
import { Alert } from './Icons.js'

export type FactTone = '' | 'muted' | 'off'

export function Facts({ rows }: { rows: [string, string, FactTone][] }): React.JSX.Element {
  return (
    <dl className="facts">
      {rows.map(([label, value, tone]) => (
        <div className="fact" key={label}>
          <dt>{label}</dt>
          <dd className={tone}>{value}</dd>
        </div>
      ))}
    </dl>
  )
}

export function Strip({ bad, children }: { bad?: boolean; children: ReactNode }): React.JSX.Element {
  return (
    <div className={bad ? 'strip bad' : 'strip'}>
      <Alert />
      {children}
    </div>
  )
}

export function Tag({ label, value, on }: { label: string; value: string; on: boolean }): React.JSX.Element {
  return (
    <span className="chip-tag">
      <StatusDot tone={on ? 'ok' : 'idle'} />
      {label} <code>{value}</code>
    </span>
  )
}

/** `<code>` is the only markup the main process puts in a check's detail. */
export function Detail({ html }: { html: string }): React.JSX.Element {
  const parts = html.split(/(<code>[^<]*<\/code>)/g)
  return (
    <>
      {parts.map((p, i) =>
        p.startsWith('<code>')
          ? <code key={i}>{p.slice(6, -7)}</code>
          : <span key={i}>{p}</span>
      )}
    </>
  )
}

/**
 * The rail's update affordance.
 *
 * Silent unless there is something to press: 'checking', 'none' and a failed
 * check all belong in Preferences, where someone went looking. The corner of
 * the window is for "there is a newer Patchbay", nothing else.
 */
export function UpdateChip({ update }: { update: UpdateState }): React.JSX.Element | null {
  switch (update.state) {
    case 'available':
      return (
        <Button
          size="sm"
          className="update-chip"
          title={t('update.getTitle')}
          onClick={() => { void window.patchbay.update.download() }}
        >
          {t('update.get', { version: update.version })}
        </Button>
      )
    case 'downloading':
      return (
        <span className="chip-tag update-chip">
          <StatusDot tone="idle" />
          {t('update.downloading', { percent: update.percent })}
        </span>
      )
    case 'ready':
      return (
        <Button
          size="sm"
          variant="primary"
          className="update-chip"
          title={t('update.restartTitle', { version: update.version })}
          onClick={() => window.patchbay.update.install()}
        >
          {t('update.restart')}
        </Button>
      )
    default:
      return null
  }
}
