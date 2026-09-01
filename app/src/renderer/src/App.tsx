import { useState } from 'react'
import { Button, SegmentedControl, StatusDot, WindowButtons } from '@singz/ui'
import { isMac, railText, regCount, sessionWord } from './derive.js'
import { useSnapshot, useToast, useUpdate } from './store.js'
import { t, tn } from './i18n.js'
import { Gear } from './components/Icons.js'
import { UpdateChip } from './components/Bits.js'
import { Home } from './views/Home.js'
import { Console } from './views/Console.js'
import { Setup } from './views/Setup.js'
import { Logs } from './views/Logs.js'
import { Leveling } from './views/Leveling.js'
import { Prefs } from './modals/Prefs.js'

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
              : t('tb.stopped')}
          </span>
        </div>
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

      {toast && (
        <div className={toast.bad ? 'toast bad' : 'toast'} key={toast.id}>
          <StatusDot tone="ok" className={toast.bad ? 'bad' : ''} />
          <span>{toast.text}</span>
        </div>
      )}
    </div>
  )
}
