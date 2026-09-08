import { useEffect, useState } from 'react'
import { Button, SegmentedControl, StatusDot, WindowButtons } from '@singz/ui'
import { isMac, railText, regCount, sessionMode } from './derive.js'
import { useSnapshot, useToast } from './store.js'
import { Gear, Keyboard } from './components/Icons.js'
import { Home } from './views/Home.js'
import { Console } from './views/Console.js'
import { Setup } from './views/Setup.js'
import { Logs } from './views/Logs.js'
import { Leveling } from './views/Leveling.js'
import { Prefs } from './modals/Prefs.js'
import { Shortcuts } from './modals/Shortcuts.js'
import { SHORTCUTS, matches, typing } from './keys.js'

type View = 'home' | 'console' | 'leveling' | 'setup' | 'logs'

const VIEWS: { value: View; label: string }[] = [
  { value: 'home', label: 'Home' },
  { value: 'console', label: 'Console' },
  { value: 'leveling', label: 'Leveling' },
  { value: 'setup', label: 'Setup' },
  { value: 'logs', label: 'Logs' }
]

export function App(): React.JSX.Element {
  const snap = useSnapshot()
  const toast = useToast()
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
    const settings = SHORTCUTS.find((s) => s.does === 'Preferences')!

    const onKey = (e: KeyboardEvent): void => {
      if (typing(e)) return
      if (matches(e, jump)) {
        e.preventDefault()
        setView(VIEWS[Number(e.key) - 1].value)
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
        <SegmentedControl options={VIEWS} value={view} onChange={setView} aria-label="View" />
        <span className="grow" />
        <div className="tb-status">
          <StatusDot tone={live ? 'ok' : 'idle'} />
          <span>
            {live
              ? `${sessionMode(snap)} · ${n} ${n === 1 ? 'client' : 'clients'}` +
                (snap.cortex.running ? ' · CC open' : '')
              : snap.daemon.state === 'starting'
                ? 'connecting…'
                : 'not connected'}
          </span>
        </div>
        <Button size="sm" icon title="Keyboard shortcuts (?)" aria-label="Keyboard shortcuts"
                onClick={() => setKeysOpen(true)}>
          <Keyboard />
        </Button>
        <Button size="sm" icon title="Preferences" aria-label="Preferences" onClick={() => setPrefs(true)}>
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
        <span className="ver">
          Patchbay 0.1.0 · {isMac(snap) ? 'macOS' : 'Windows'}
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
