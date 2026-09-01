import { useState } from 'react'
import { Button } from '@singz/ui'
import type { Snapshot } from '@shared/types'
import { isLinked, isMac, setupPending, sharedWriters } from '../derive.js'
import { act, publish, say, useProgress } from '../store.js'
import { T, t } from '../i18n.js'
import { SignalPath } from '../components/SignalPath.js'
import { Strip } from '../components/Bits.js'

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
  if (s.daemon.state === 'running') say(t('home.connectedToast'))
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
    lede = mac ? t('home.ready.ledeMac') : t('home.ready.ledeWin')
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
    if (action === 'disconnect') {
      let s = await window.patchbay.daemonStop()
      if (s.cortex.running && snap.prefs.quitApp) s = await window.patchbay.cortexQuit()
      publish(s)
      say(t('home.disconnectedToast'))
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

        <div className="home-warn">
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
