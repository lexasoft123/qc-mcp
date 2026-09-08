import { useState } from 'react'
import { Button } from '@singz/ui'
import type { Mode, Snapshot } from '@shared/types'
import { modeSwitch } from '@shared/session'
import {
  cleanError, heldButUnreachable, heldByOther, isLinked, isMac, modeFacts,
  modePlan, sessionMode, sessionWords, setupPending
} from '../derive.js'
import { act, publish, say, useProgress } from '../store.js'
import { SignalPath } from '../components/SignalPath.js'
import { Strip } from '../components/Bits.js'

/**
 * Auto is a decision, not a session, so the tile says what it decides FROM.
 * The other two say what they do, in the fewest words that stay true.
 */
const MODES: { value: Mode; name: string; mac: string; win: string }[] = [
  { value: 'auto', name: 'Auto',
    mac: 'Whichever fits what is already running',
    win: 'Whichever fits what is already running' },
  { value: 'bridge', name: 'Bridge',
    mac: "Through Cortex Control's own connection",
    win: 'A second handle beside Cortex Control' },
  { value: 'direct', name: 'Direct',
    mac: 'Straight to the device, app closed',
    win: 'Straight to the device, app closed' }
]

/**
 * Which mode is selected, and what that means for the press about to happen.
 *
 * The mode lived only in Preferences, then briefly as a word in the header and
 * the same word in the footer — small, twice, and explaining nothing. Three
 * tiles carry it now, and the line under them resolves it the same way the two
 * places that actually make the call do: Home's connect() sequence, and
 * daemon.serve() (qc_mcp/daemon.py).
 */
export function ModeChoice({ snap, live }: { snap: Snapshot; live: boolean }): React.JSX.Element {
  const mode = snap.prefs.mode
  const mac = isMac(snap)
  const p = modePlan(snap)
  // A live session is not a setting. Changing the mode under one means tearing
  // the session down and building another, which is a thing to ask for on
  // purpose — Disconnect — not a side effect of touching a selector.
  const gate = modeSwitch(modeFacts(snap), snap.daemon.state !== 'stopped')
  const locked = !gate.allowed
  // The chip belongs to the ROUTE, not the selection: it labels a sentence about
  // the session, and it is the tie back to the colour running through the path.
  const route = live ? sessionMode(snap) : p.will

  return (
    <div className={`home-mode r-${route}${p.blocked ? ' warn' : ''}`}>
      <div className="hm-tiles" role="radiogroup" aria-label="Connection mode">
        {MODES.map((m) => {
          const on = m.value === mode
          return (
            <button
              key={m.value}
              type="button"
              role="radio"
              aria-checked={on}
              className={`hm-tile m-${m.value}${on ? ' on' : ''}`}
              disabled={locked && !on}
              aria-disabled={locked || undefined}
              onClick={() => {
                if (on) return
                if (locked) { say(gate.why!, true); return }
                // Nothing is running, so this changes a preference and the
                // picture above it. No app is opened, no daemon is started.
                void act(() => window.patchbay.setMode(m.value))
              }}
            >
              <b><i className="dot" />{m.name}</b>
              <span>{mac ? m.mac : m.win}</span>
            </button>
          )
        })}
      </div>

      <p className="hm-plan">
        {live
          ? <><i>Open now</i> {sessionWords(snap, sessionMode(snap))}
              {snap.daemon.external ? ' · adopted from a daemon started outside Patchbay' : ''}
              <span className="hm-lock"> · disconnect to change it</span></>
          : locked
            ? <><i>Working</i> Opening the session.</>
            : <><i>{p.blocked ? 'Cannot connect' : 'This press'}</i> {p.blocked ?? p.plan}</>}
      </p>
    </div>
  )
}

/**
 * One press, one plan.
 *
 * This used to be a sequence written here — run setup, maybe launch Cortex
 * Control, maybe start the daemon — decided from a snapshot the renderer read
 * itself. It disagreed with the daemon's own `auto` branch (it opened Cortex
 * Control unasked, so auto never once chose direct), and it could not see a
 * mode switch that needed the live session torn down first. Main plans it now,
 * from shared/session.ts, which is the same function the tiles below describe.
 */
async function connect(): Promise<void> {
  let s = await window.patchbay.snapshot()
  if (setupPending(s)) s = await window.patchbay.runSetup()
  s = await window.patchbay.connect()
  publish(s)
  if (s.daemon.state === 'running') say('Connected — Claude can reach your Quad Cortex')
  else if (s.daemon.error) say(cleanError(s.daemon.error) ?? s.daemon.error, true)
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
    setBusy(true)
    try {
      if (action === 'disconnect') {
        // quitApp is part of the plan now, so this is one call, not two.
        publish(await window.patchbay.disconnect())
        say('Disconnected. Your presets are untouched.')
        return
      }
      await connect()
    } finally { setBusy(false) }
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
          {/* Somebody else holds the device. Name them, and offer the one thing
              that resolves it — as a decision, not as a hidden step in Connect. */}
          {!busy && heldButUnreachable(snap) && (
            <Strip bad>
              <span className="grow">
                A <b>{snap.lock!.mode}</b> daemon (pid {snap.lock!.pid}) is holding the Quad
                Cortex and answering nobody — its socket is gone. Nothing can reach the device
                until it stops.
              </span>
              <Button size="sm" variant="danger"
                      onClick={() => void act(() => window.patchbay.takeOver())}>
                Take over
              </Button>
            </Strip>
          )}
          {!busy && !heldButUnreachable(snap) && heldByOther(snap) && (
            <Strip>
              <span className="grow">
                {heldByOther(snap)} Patchbay can end it and connect in its place.
              </span>
              <Button size="sm" variant="danger"
                      onClick={() => void act(() => window.patchbay.takeOver())}>
                Take over
              </Button>
            </Strip>
          )}
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
