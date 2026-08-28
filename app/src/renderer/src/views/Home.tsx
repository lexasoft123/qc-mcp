import { useState } from 'react'
import { Button } from '@singz/ui'
import type { Snapshot } from '@shared/types'
import { isLinked, isMac, setupPending } from '../derive.js'
import { publish, say, useProgress } from '../store.js'
import { SignalPath } from '../components/SignalPath.js'
import { Strip } from '../components/Bits.js'

/**
 * The quick start: whatever is missing, in order. Each call returns the fresh
 * snapshot, so the sequence never decides a step from stale state.
 */
async function connect(): Promise<void> {
  let s = await window.patchbay.snapshot()
  if (setupPending(s)) s = await window.patchbay.runSetup()
  if (s.daemon.state !== 'running') s = await window.patchbay.daemonStart()
  if (isMac(s) && s.prefs.mode !== 'direct' && !s.cortex.running) s = await window.patchbay.cortexLaunch()
  publish(s)
  if (s.daemon.state === 'running') say('Connected — Claude can reach your Quad Cortex')
  else if (s.daemon.error) say(s.daemon.error, true)
}

export function Home({ snap, goto }: { snap: Snapshot; goto: (v: string) => void }): React.JSX.Element {
  const [busy, setBusy] = useState(false)
  const progress = useProgress()

  const pending = setupPending(snap)
  const linked = isLinked(snap)
  const mac = isMac(snap)

  let title: string
  let lede: string
  let label: string
  let disabled = false
  let action: 'connect' | 'disconnect' = 'connect'
  let second: [string, () => void] | null = null

  if (busy) {
    title = 'Getting you connected'
    lede = 'This part only happens once. Every step is listed under Setup if you want to watch.'
    label = 'Working…'
    disabled = true
  } else if (!snap.device.present) {
    title = 'Plug in your Quad Cortex'
    lede = 'Connect it to this computer with a USB cable and Patchbay will pick it up. Nothing else to do.'
    label = 'Connect'
    disabled = true
  } else if (pending) {
    title = 'One-time setup'
    lede = mac
      ? 'Patchbay installs qc-mcp, builds its own copy of Cortex Control, and registers the server with Claude. It never asks for your password.'
      : 'Patchbay installs qc-mcp and registers the server with Claude. No copy to build on Windows, and it never asks for your password.'
    label = 'Set up and connect'
    second = ['See each step', () => goto('setup')]
  } else if (snap.daemon.state !== 'running') {
    title = 'Ready when you are'
    lede = mac
      ? 'One press starts the daemon and opens Cortex Control, so Claude can read and change presets on your Quad Cortex.'
      : 'One press starts the daemon, and Claude can read and change presets on your Quad Cortex — with or without Cortex Control open.'
    label = 'Connect'
  } else if (!linked) {
    // macOS only: bridge mode rides the app's session, so the app must be up
    title = 'Almost there'
    lede = "Bridge mode shares Cortex Control's session with Claude, so the app needs to be open too."
    label = 'Open Cortex Control'
  } else {
    title = "You're connected"
    lede = 'Ask Claude for a tone and it will build it on the Quad Cortex.'
    label = 'Disconnect'
    action = 'disconnect'
    second = ['Show Cortex Control', () => { void window.patchbay.cortexFocus() }]
  }

  const press = async (): Promise<void> => {
    if (action === 'disconnect') {
      let s = await window.patchbay.daemonStop()
      if (s.cortex.running && snap.prefs.quitApp) s = await window.patchbay.cortexQuit()
      publish(s)
      say('Disconnected. Your presets are untouched.')
      return
    }
    setBusy(true)
    try { await connect() } finally { setBusy(false) }
  }

  return (
    <div className="view home">
      <div className="home-inner">
        <SignalPath snap={snap} />

        <h1>{title}</h1>
        <p className="lede">{lede}</p>

        {busy && (
          <div className="prog">
            <span className="track">
              <span
                className="fill"
                style={{ width: `${progress && progress.total ? Math.round((progress.done / progress.total) * 100) : 8}%` }}
              />
            </span>
            <div className="lbl">{progress?.label ?? 'Checking what is missing'}</div>
          </div>
        )}

        <div className="home-actions">
          <Button
            variant={action === 'disconnect' ? 'danger' : 'primary'}
            className="big"
            disabled={disabled}
            onClick={() => void press()}
          >
            {label}
          </Button>
          {second && <Button onClick={second[1]}>{second[0]}</Button>}
        </div>

        <div className="home-warn">
          {!busy && !mac && linked && snap.cortex.running && (
            <Strip>
              <span className="grow">
                Cortex Control is open too. Both are writing to the same device — fine for ordinary
                edits, but use <b>Direct</b> for heavy work.
              </span>
            </Strip>
          )}
          {!busy && mac && snap.device.present && !pending && snap.cortex.needsRebuild && (
            <Strip>
              <span className="grow">
                Cortex Control updated to <b>{snap.cortex.version}</b>. Its instrumented copy is{' '}
                {snap.cortex.instrumented?.version} — rebuild to stay in sync.
              </span>
              <Button size="sm" onClick={() => goto('console')}>Rebuild</Button>
            </Strip>
          )}
        </div>

        {!busy && <p className="home-note">Every one of these has a detailed view under Console.</p>}
      </div>
    </div>
  )
}
