import { useEffect, useState } from 'react'
import { Button, SegmentedControl, StatusDot, WindowButtons } from '@singz/ui'
import { isMac, railText, regCount, sessionWord } from './derive.js'
import { useSnapshot, useToast, useUpdate } from './store.js'
import { t, tn } from './i18n.js'
import { Gear, Keyboard } from './components/Icons.js'
import { UpdateChip } from './components/Bits.js'
import { Language } from './components/Language.js'
import { Home } from './views/Home.js'
import { Console } from './views/Console.js'
import { Setup } from './views/Setup.js'
import { Logs } from './views/Logs.js'
import { Leveling } from './views/Leveling.js'
import { Prefs } from './modals/Prefs.js'
import { Shortcuts } from './modals/Shortcuts.js'
import { SHORTCUTS, matches, typing } from './keys.js'

type View = 'home' | 'console' | 'leveling' | 'setup' | 'logs'

/** Built per render: the labels follow the language. */
const views = (): { value: View; label: string }[] => [
  { value: 'home', label: t('view.home') },
  { value: 'console', label: t('view.console') },
  { value: 'leveling', label: t('view.leveling') },
  { value: 'setup', label: t('view.setup') },
  { value: 'logs', label: t('view.logs') }
]

export function App(): React.JSX.Element {
  const snap = useSnapshot()
  const toast = useToast()
  const update = useUpdate()
  const [view, setView] = useState<View>('home')
  const [prefs, setPrefs] = useState(false)
  const [keysOpen, setKeysOpen] = useState(false)

  /*
   * Getting around, by key. The whole navigation was a mouse-only segmented
   * control, which for a tool operated with a guitar in both hands is most of
   * the tool being out of reach.
   */
  useEffect(() => {
    const byCap = (cap: string): (typeof SHORTCUTS)[number] =>
      SHORTCUTS.find((s) => s.cap === cap)!
    const jump = SHORTCUTS.find((s) => s.scope === 'app' && s.does.startsWith('Home,'))!
    const sheet = byCap('?')
    const settings = SHORTCUTS.find((s) => s.does === 'prefs.open')!

    const onKey = (e: KeyboardEvent): void => {
      if (typing(e)) return
      if (matches(e, jump)) {
        e.preventDefault()
        setView(views()[Number(e.key) - 1].value)
      } else if (matches(e, sheet)) {
        e.preventDefault()
        setKeysOpen((v) => !v)
      } else if (matches(e, settings)) {
        e.preventDefault()
        setPrefs((v) => !v)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  /* A view change should move the caret to the new view, not leave it in the
     titlebar so the next Tab starts the tour again. */
  useEffect(() => {
    const main = document.querySelector<HTMLElement>('.view')
    if (!main) return
    // -1 so it takes focus programmatically without joining the tab order
    main.tabIndex = -1
    main.focus({ preventScroll: true })
  }, [view])

  // the kit's chrome only belongs on Windows; macOS keeps its traffic lights
  const onWindows = document.body.classList.contains('win')

  if (!snap) {
    return (
      <div className="app">
        <div className="titlebar"><span className="logo">Patch<span>bay</span></span></div>
        <div className="view" />
      </div>
    )
  }

  const live = snap.daemon.state === 'running'
  const n = regCount(snap)

  return (
    <div className="app">
      <div className="titlebar">
        <span className="logo">Patch<span>bay</span></span>
        <SegmentedControl options={views()} value={view} onChange={setView} aria-label={t('aria.view')} />
        <span className="grow" />
        <div className="tb-status">
          <StatusDot tone={live ? 'ok' : 'idle'} />
          <span>
            {live
              ? t('tb.session', { mode: sessionWord(snap), clients: tn('clients', n) })
              : snap.daemon.state === 'starting'
                ? t('tb.connecting')
                : t('tb.stopped')}
          </span>
        </div>
        {/* The flag alone, beside the gear: the one control someone who cannot
            read the window needs to find, on every screen, without opening a
            dialog whose title they cannot read either. */}
        <Language snap={snap} compact />
        <Button size="sm" icon title={t('keys.open')} aria-label={t('keys.open')}
                onClick={() => setKeysOpen(true)}>
          <Keyboard />
        </Button>
        <Button size="sm" icon title={t('prefs.open')} aria-label={t('prefs.open')} onClick={() => setPrefs(true)}>
          <Gear />
        </Button>
        {onWindows && <WindowButtons api={window.patchbay.window} />}
      </div>

      {view === 'home' && <Home snap={snap} goto={(v) => setView(v as View)} />}
      {view === 'console' && <Console snap={snap} />}
      {view === 'leveling' && <Leveling snap={snap} />}
      {view === 'setup' && <Setup snap={snap} />}
      {view === 'logs' && <Logs snap={snap} />}

      <div className="rail">
        <div className="sess">
          <StatusDot
            tone={live && snap.device.present ? 'ok' : 'idle'}
            className={!snap.device.present ? 'bad' : ''}
          />
          <span>{railText(snap)}</span>
        </div>
        <span className="grow" />
        <UpdateChip update={update} />
        <span className="ver">
          {t('rail.version', { version: snap.version, os: isMac(snap) ? 'macOS' : 'Windows' })}
        </span>
      </div>

      {prefs && <Prefs snap={snap} onClose={() => setPrefs(false)} />}
      {keysOpen && <Shortcuts onClose={() => setKeysOpen(false)} />}

      {toast && (
        <div className={toast.bad ? 'toast bad' : 'toast'} key={toast.id}>
          <StatusDot tone="ok" className={toast.bad ? 'bad' : ''} />
          <span>{toast.text}</span>
        </div>
      )}
    </div>
  )
}
