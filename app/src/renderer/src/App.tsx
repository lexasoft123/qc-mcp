import { useState } from 'react'
import { Button, SegmentedControl, StatusDot, WindowButtons } from '@singz/ui'
import { isMac, railText, regCount } from './derive.js'
import { useSnapshot, useToast } from './store.js'
import { Gear } from './components/Icons.js'
import { Home } from './views/Home.js'
import { Console } from './views/Console.js'
import { Setup } from './views/Setup.js'
import { Logs } from './views/Logs.js'
import { Prefs } from './modals/Prefs.js'

type View = 'home' | 'console' | 'setup' | 'logs'

const VIEWS: { value: View; label: string }[] = [
  { value: 'home', label: 'Home' },
  { value: 'console', label: 'Console' },
  { value: 'setup', label: 'Setup' },
  { value: 'logs', label: 'Logs' }
]

export function App(): React.JSX.Element {
  const snap = useSnapshot()
  const toast = useToast()
  const [view, setView] = useState<View>('home')
  const [prefs, setPrefs] = useState(false)

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
              ? `${snap.prefs.mode === 'direct' ? 'direct' : 'bridge'} session · ${n} ${n === 1 ? 'client' : 'clients'}`
              : 'daemon stopped'}
          </span>
        </div>
        <Button size="sm" icon title="Preferences" aria-label="Preferences" onClick={() => setPrefs(true)}>
          <Gear />
        </Button>
        {onWindows && <WindowButtons api={window.patchbay.window} />}
      </div>

      {view === 'home' && <Home snap={snap} goto={(v) => setView(v as View)} />}
      {view === 'console' && <Console snap={snap} />}
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

      {toast && (
        <div className={toast.bad ? 'toast bad' : 'toast'} key={toast.id}>
          <StatusDot tone="ok" className={toast.bad ? 'bad' : ''} />
          <span>{toast.text}</span>
        </div>
      )}
    </div>
  )
}
