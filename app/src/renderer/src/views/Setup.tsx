import { useState } from 'react'
import { Badge, Button } from '@singz/ui'
import type { CheckId, Snapshot } from '@shared/types'
import { isMac, setupPending } from '../derive.js'
import { act, say, useProgress } from '../store.js'
import { Detail } from '../components/Bits.js'

const COUNT: Record<number, string> = { 4: 'four', 5: 'five', 6: 'six', 7: 'seven' }

export function Setup({ snap }: { snap: Snapshot }): React.JSX.Element {
  const [running, setRunning] = useState<CheckId | 'all' | null>(null)
  const progress = useProgress()

  const pending = setupPending(snap)
  const deviceMissing = snap.checks.some((c) => c.id === 'device' && c.status !== 'ok')

  const run = async (only?: CheckId): Promise<void> => {
    setRunning(only ?? 'all')
    try {
      await act(() => window.patchbay.runSetup(only ? [only] : undefined))
      say(only ? 'Done' : 'Setup complete — qc-mcp is ready')
    } finally {
      setRunning(null)
    }
  }

  return (
    <div className="view">
      <div className="setup-head">
        <h1>Set up once, then forget it</h1>
        <p className="fine">
          Patchbay checks the {COUNT[snap.checks.length] ?? snap.checks.length} things qc-mcp needs,
          and fixes the ones it can.{' '}
          {isMac(snap)
            ? <>The instrumented build re-signs a <b>local copy</b> of Cortex Control — your installed app is never touched.</>
            : <>Nothing is copied or re-signed here: the daemon shares the device by opening a <b>second HID handle</b> beside Cortex Control.</>}
        </p>
      </div>

      <div className="steps">
        {snap.checks.map((c, i) => {
          const busy = running === c.id || (running === 'all' && c.status !== 'ok')
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
                  ? <Badge className="attn">working…</Badge>
                  : c.status === 'ok'
                    ? <Badge className="live">ok</Badge>
                    : <Badge className={c.fixable ? 'off' : 'bad'}>{c.fixable ? 'to do' : 'missing'}</Badge>}
                {c.status !== 'ok' && c.fixable && !running && (
                  <Button size="sm" onClick={() => void run(c.id)}>Fix</Button>
                )}
              </span>
            </div>
          )
        })}
      </div>

      <div className="setup-foot">
        <Button variant="primary" disabled={!pending || running !== null} onClick={() => void run()}>
          {running ? 'Working…' : pending ? 'Run setup' : 'Everything is set up'}
        </Button>
        <Button disabled={running !== null} onClick={() => void act(() => window.patchbay.runChecks())}>
          Re-check
        </Button>
        <span className="grow" />
        <span className="fine">
          {progress?.label
            ?? (deviceMissing
              ? 'Plug your Quad Cortex into USB to finish the last check.'
              : pending
                ? 'Nothing here needs your password.'
                : 'Re-check after a Cortex Control update.')}
        </span>
      </div>
    </div>
  )
}
