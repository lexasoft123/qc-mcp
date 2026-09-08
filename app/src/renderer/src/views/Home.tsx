import { useState } from 'react'
import { Button } from '@singz/ui'
import type { Snapshot } from '@shared/types'
import type { Mode } from '@shared/types'
import { modeSwitch } from '@shared/session'
import {
  cleanError, heldButUnreachable, heldByOther, isLinked, isMac, modeFacts, modePlan,
  sessionMode, sessionWords, setupPending, sharedWriters
} from '../derive.js'
import { act, publish, say, useProgress } from '../store.js'
import { T, t } from '../i18n.js'
import { SignalPath } from '../components/SignalPath.js'
import { Strip } from '../components/Bits.js'

const MODES: { value: Mode; key: 'auto' | 'bridge' | 'direct' }[] = [
  { value: 'auto', key: 'auto' },
  { value: 'bridge', key: 'bridge' },
  { value: 'direct', key: 'direct' }
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
  const mac = isMac(snap)
  const p = modePlan(snap)
  // A live session is not a setting. Changing the mode under one means tearing
  // the session down and building another, which is a thing to ask for on
  // purpose — Disconnect — not a side effect of touching a selector.
  const gate = modeSwitch(modeFacts(snap), snap.daemon.state !== 'stopped')
  const locked = !gate.allowed
  // The chip belongs to the ROUTE, not the selection: it labels a sentence
  // about the session, and ties back to the colour running through the path.
  const route = live ? sessionMode(snap) : p.will

  return (
    <div className={`home-mode r-${route}${p.blocked ? ' warn' : ''}`}>
      <div className="hm-tiles" role="radiogroup" aria-label={t('aria.mode')}>
        {MODES.map((m) => {
          const on = m.value === mode
          return (
            <button
              key={m.value} type="button" role="radio" aria-checked={on}
              className={`hm-tile m-${m.value}${on ? ' on' : ''}`}
              disabled={locked && !on}
              aria-disabled={locked || undefined}
              onClick={() => {
                if (on) return
                if (locked) { say(t('mode.locked'), true); return }
                // Nothing is running, so this changes a preference and the
                // picture above it. No app is opened, no daemon is started.
                void act(() => window.patchbay.setMode(m.value))
              }}
            >
              <b><i className="dot" />{t(`mode.${m.key}` as Parameters<typeof t>[0])}</b>
              <span>{t(`mode.${m.key}.${mac ? 'mac' : 'win'}` as Parameters<typeof t>[0])}</span>
            </button>
          )
        })}
      </div>

      <p className="hm-plan">
        {live
          ? <><i>{t('mode.openNow')}</i> {sessionWords(snap, sessionMode(snap))}
              {snap.daemon.external ? ` · ${t('mode.adopted')}` : ''}
              <span className="hm-lock"> · {t('mode.disconnectToChange')}</span></>
          : locked
            ? <><i>{t('mode.workingLabel')}</i> {t('mode.opening')}</>
            : <><i>{p.blocked ? t('mode.cannot') : t('mode.thisPress')}</i> {p.blocked ?? p.plan}</>}
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
  // Main plans it now, from shared/session.ts, which is the same function the
  // tiles below describe. This used to be a sequence written here, decided from
  // a snapshot the renderer read itself — it disagreed with the daemon's own
  // `auto` branch and could not see a mode switch needing a teardown first.
  s = await window.patchbay.connect()
  publish(s)
  if (s.daemon.state === 'running') say(t('home.connectedToast'))
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
    title = t('home.busy.title')
    lede = t('home.busy.lede')
    label = t('home.working')
    disabled = true
  } else if (!snap.device.present) {
    title = t('home.plug.title')
    lede = t('home.plug.lede')
    label = t('home.connect')
    disabled = true
  } else if (pending) {
    title = t('home.setup.title')
    lede = mac ? t('home.setup.ledeMac') : t('home.setup.ledeWin')
    label = t('home.setup.label')
    second = [t('home.setup.second'), () => goto('setup')]
  } else if (snap.daemon.state !== 'running') {
    title = t('home.ready.title')
    // The how depends on the mode, and the block below says it — repeating a
    // fixed sentence here is what made "Connect" ambiguous in the first place.
    lede = t('home.ready.lede')
    label = t('home.connect')
  } else if (!linked) {
    // macOS only: bridge mode rides the app's session, so the app must be up
    title = t('home.almost.title')
    lede = t('home.almost.lede')
    label = t('home.openApp')
  } else {
    title = t('home.connected.title')
    lede = t('home.connected.lede')
    label = t('home.disconnect')
    action = 'disconnect'
    // Launch/quit like the Console page's button, which is what makes this work
    // on Windows at all — `cortexFocus` was `open -a` behind an IS_MAC guard, so
    // the button did nothing there. But NOT quit on macOS: this branch is only
    // reached with the app running, and in bridge mode the daemon rides that
    // app's session, so quitting would silently drop the connection we just told
    // the user they had. Raise it instead.
    second = !snap.cortex.running
      ? [t('home.openApp'), () => { void act(() => window.patchbay.cortexLaunch()) }]
      : mac
        ? [t('home.showApp'), () => { void act(() => window.patchbay.cortexFocus()) }]
        : [t('home.quitApp'), () => { void act(() => window.patchbay.cortexQuit()) }]
  }

  const press = async (): Promise<void> => {
    setBusy(true)
    try {
      if (action === 'disconnect') {
        // quitApp is part of the plan now, so this is one call, not two.
        publish(await window.patchbay.disconnect())
        say(t('home.disconnectedToast'))
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
            <div className="lbl">{progress?.label ?? t('home.checking')}</div>
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

        {/* What the connected state is FOR. The reward for finishing setup used
            to be one sentence, for a product with two halves — the bench was
            reachable only by clicking a tab nobody had a reason to press. */}
        {!busy && linked && (
          <div className="home-next">
            <div className="nx">
              <span className="eyebrow">{t('home.next.claude')}</span>
              <p>{t('home.next.claudeBody')}</p>
              <q>{t('home.next.claudeExample')}</q>
            </div>
            <button type="button" className="nx go" onClick={() => goto('leveling')}>
              <span className="eyebrow">{t('home.next.level')}</span>
              <p>{t('home.next.levelBody')}</p>
              <span className="nx-cta">{t('home.next.levelCta')}</span>
            </button>
          </div>
        )}

        <div className="home-warn">
          {/* Somebody else holds the device. Name them, and offer the one thing
              that resolves it — as a decision, not a hidden step in Connect. */}
          {!busy && heldButUnreachable(snap) && (
            <Strip bad>
              <span className="grow">
                <T k="lock.unreachable" vars={{
                  mode: snap.lock!.mode, pid: String(snap.lock!.pid)
                }} />
              </span>
              <Button size="sm" variant="danger"
                      onClick={() => void act(() => window.patchbay.takeOver())}>
                {t('takeOver')}
              </Button>
            </Strip>
          )}
          {!busy && !heldButUnreachable(snap) && heldByOther(snap) && (
            <Strip>
              <span className="grow">{heldByOther(snap)} {t('lock.canEndIt')}</span>
              <Button size="sm" variant="danger"
                      onClick={() => void act(() => window.patchbay.takeOver())}>
                {t('takeOver')}
              </Button>
            </Strip>
          )}
          {!busy && sharedWriters(snap) && (
            <Strip>
              <span className="grow"><T k="home.sharedWarn" /></span>
            </Strip>
          )}
          {!busy && mac && snap.device.present && !pending && snap.cortex.needsRebuild && (
            <Strip>
              <span className="grow">
                <T k="home.rebuildWarn" vars={{ version: snap.cortex.version ?? '', old: snap.cortex.instrumented?.version ?? '' }} />
              </span>
              <Button size="sm" onClick={() => goto('console')}>{t('home.rebuild')}</Button>
            </Strip>
          )}
        </div>

        {!busy && <p className="home-note">{t('home.note')}</p>}
      </div>
    </div>
  )
}
