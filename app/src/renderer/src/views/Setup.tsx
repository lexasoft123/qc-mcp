import { useState } from 'react'
import { Badge, Button } from '@singz/ui'
import type { CheckId, Snapshot } from '@shared/types'
import { isMac, setupPending } from '../derive.js'
import { act, say, useProgress } from '../store.js'
import { T, t, type Key } from '../i18n.js'
import { Detail } from '../components/Bits.js'

/** "seven" in English, "7" in Chinese — the dictionary decides. */
const count = (n: number): string => (n >= 4 && n <= 7 ? t(`num.${n}` as Key) : String(n))

export function Setup({ snap }: { snap: Snapshot }): React.JSX.Element {
  const [running, setRunning] = useState<CheckId | 'all' | null>(null)
  const progress = useProgress()

  const pending = setupPending(snap)
  const deviceMissing = snap.checks.some((c) => c.id === 'device' && c.status !== 'ok')

  const run = async (only?: CheckId): Promise<void> => {
    setRunning(only ?? 'all')
    try {
      await act(() => window.patchbay.runSetup(only ? [only] : undefined))
      say(only ? t('setup.done') : t('setup.completeToast'))
    } finally {
      setRunning(null)
    }
  }

  return (
    <div className="view">
      <div className="setup-head">
        <h1>{t('setup.title')}</h1>
        <p className="fine">
          {t('setup.intro', { count: count(snap.checks.length) })}{' '}
          <T k={isMac(snap) ? 'setup.introMac' : 'setup.introWin'} />
        </p>
      </div>

      <div className="steps">
        {snap.checks.map((c, i) => {
          const busy = running === c.id || progress?.step === c.id
          const cls = busy ? 'busy' : c.status === 'ok' ? 'ok' : 'missing'
          return (
            <div className={`step ${cls}`} key={c.id}>
              <span className="num">{busy ? '·' : c.status === 'ok' ? '✓' : i + 1}</span>
              <span className="txt">
                <h3>{c.title}</h3>
                <p><Detail html={c.detail} /></p>
              </span>
              <span className="right">
                {busy
                  ? <Badge className="attn">{t('setup.working')}</Badge>
                  : c.status === 'ok'
                    ? <Badge className="live">{t('setup.ok')}</Badge>
                    : <Badge className={c.fixable ? 'off' : 'bad'}>{c.fixable ? t('setup.todo') : t('setup.missing')}</Badge>}
                {c.status !== 'ok' && c.fixable && !running && (
                  <Button size="sm" onClick={() => void run(c.id)}>{t('setup.fix')}</Button>
                )}
              </span>
            </div>
          )
        })}
      </div>

      <div className="setup-foot">
        <Button variant="primary" disabled={!pending || running !== null} onClick={() => void run()}>
          {running ? t('setup.busy') : pending ? t('setup.run') : t('setup.allDone')}
        </Button>
        <Button disabled={running !== null} onClick={() => void act(() => window.patchbay.runChecks())}>
          {t('setup.recheck')}
        </Button>
        <span className="grow" />
        <span className="fine">
          {progress?.label
            ?? (deviceMissing
              ? t('setup.plugHint')
              : pending
                ? t('setup.noPassword')
                : t('setup.recheckHint'))}
        </span>
      </div>
    </div>
  )
}
