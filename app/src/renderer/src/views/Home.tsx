import { useState } from 'react'
import { Button, SegmentedControl } from '@singz/ui'
import type { Mode, Snapshot } from '@shared/types'
import {
  isLinked, isMac, modeGist, modePlan, sessionMode, sessionWords, setupPending
} from '../derive.js'
import { act, publish, say, useProgress } from '../store.js'
import { SignalPath } from '../components/SignalPath.js'
import { Strip } from '../components/Bits.js'

const MODES: { value: Mode; label: string }[] = [
  { value: 'auto', label: 'Auto' },
  { value: 'bridge', label: 'Bridge' },
  { value: 'direct', label: 'Direct' }
]

/**
 * Which mode is selected, and what that means for the press about to happen.
 *
 * The mode lived only in Preferences, so the front page offered one button and
 * no way to know whether it was about to seize the device or share the app's
 * session. `auto` made that worse by naming a decision instead of a session.
 * Both lines here are the same resolution the connect sequence performs.
 */
export function ModeChoice({ snap, live }: { snap: Snapshot; live: boolean }): React.JSX.Element {
  const mode = snap.prefs.mode
  const p = modePlan(snap)

  return (
    <div className={`home-mode${p.blocked ? ' warn' : ''}`}>
      <div className="hm-pick">
        <span className="eyebrow">Connection mode</span>
        <SegmentedControl
          options={MODES}
          value={mode}
          aria-label="Connection mode"
          onChange={(m) => {
            void act(() => window.patchbay.setMode(m))
            if (m === 'direct' && snap.cortex.running) {
              say('Direct mode needs the device to itself — quit Cortex Control.', true)
            }
          }}
        />
      </div>
      <p className="hm-gist">{modeGist(snap, mode)}</p>
      <p className="hm-plan">
        {live
          ? <><b>Open now:</b> {sessionWords(snap, sessionMode(snap))}
              {snap.daemon.external ? ' · adopted from a daemon started outside Patchbay' : ''}</>
          : <><b>{p.blocked ? 'Cannot connect:' : 'This press:'}</b> {p.blocked ?? p.plan}</>}
      </p>
    </div>
  )
}

/**
 * The quick start: whatever is missing, in order. Each call returns the fresh
 * snapshot, so the sequence never decides a step from stale state.
 */
async function connect(): Promise<void> {
  let s = await window.patchbay.snapshot()
  if (setupPending(s)) s = await window.patchbay.runSetup()
  // Cortex Control FIRST on macOS. The daemon picks bridge vs direct ONCE, at
  // startup, from whether the instrumented app is already up — so starting it
  // first silently produced direct mode every time, seizing the device and
  // leaving the interposer unused. cortexLaunch waits for the bridge to open.
  if (isMac(s) && s.prefs.mode !== 'direct' && !s.cortex.running) s = await window.patchbay.cortexLaunch()
  if (s.daemon.state !== 'running') s = await window.patchbay.daemonStart()
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
    // The how depends on the mode, and the mode block below says it — repeating
    // a fixed sentence here is what made "Connect" ambiguous in the first place.
    lede = 'One press starts the daemon, so Claude can read and change presets on your Quad Cortex.'
    label = 'Connect'
  } else if (!linked) {
    // macOS only: bridge mode rides the app's session, so the app must be up
    title = 'Almost there'
    lede = "The daemon is up, but this is a bridge session — it rides Cortex Control's own connection, so the app has to be open too."
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

        {!busy && snap.device.present && !pending && (
          <ModeChoice snap={snap} live={linked} />
        )}

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
