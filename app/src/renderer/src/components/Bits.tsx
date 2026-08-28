import type { ReactNode } from 'react'
import { StatusDot } from '@singz/ui'
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
