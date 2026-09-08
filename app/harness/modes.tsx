import '@fontsource-variable/bricolage-grotesque'
import '@fontsource-variable/martian-mono'
import '@singz/ui/kit.css'
import '../src/renderer/src/styles.css'
import './stub'
import { createRoot } from 'react-dom/client'
import type { Mode, SessionMode, Snapshot } from '../src/shared/types'
import { ModeChoice } from '../src/renderer/src/views/Home'
import { SignalPath } from '../src/renderer/src/components/SignalPath'

/*
 * Every sentence the front page's mode block can produce, on one page.
 *
 * ModeChoice is pure — it reads the snapshot it is handed and nothing else — so
 * unlike the Measured harness these can be stacked without the panels fighting
 * over one stubbed bridge.
 */


function snap(over: {
  platform?: 'mac' | 'win'
  mode?: Mode
  running?: boolean
  instrumented?: boolean
  built?: boolean
  daemon?: 'stopped' | 'running'
  session?: SessionMode | null
  external?: boolean
}): Snapshot {
  const o = { platform: 'mac' as const, mode: 'auto' as Mode, built: true, ...over }
  return {
    platform: o.platform,
    device: { present: true },
    cortex: {
      running: Boolean(o.running), runningInstrumented: Boolean(o.instrumented),
      instrumented: { built: o.built, version: '4.1.0', hardenedRuntimeOff: true, libraryValidationOff: true },
      installed: true, path: '/Applications/Cortex Control.app', version: '4.1.0',
      pid: null, needsRebuild: false
    },
    daemon: {
      state: o.daemon ?? 'stopped', session: o.session ?? null, external: o.external,
      pid: null, startedAt: null, socket: '', mode: o.mode, supported: true,
      error: null, reportsPerSecond: 0, clients: []
    },
    prefs: { mode: o.mode, quitApp: false },
    checks: [], clients: [{ id: 'claude', installed: true, stale: false, found: true, name: 'Claude', path: '' }],
    paths: {}
  } as unknown as Snapshot
}

const ALL: [string, Snapshot, boolean][] = [
  ['mac · auto · nothing running', snap({ mode: 'auto' }), false],
  ['mac · auto · instrumented app already up', snap({ mode: 'auto', running: true, instrumented: true }), false],
  ['mac · auto · stock app holding the device', snap({ mode: 'auto', running: true }), false],
  ['mac · bridge · app closed', snap({ mode: 'bridge' }), false],
  ['mac · bridge · no instrumented copy built', snap({ mode: 'bridge', built: false }), false],
  ['mac · direct · app closed', snap({ mode: 'direct' }), false],
  ['mac · direct · app holding the device (blocked)', snap({ mode: 'direct', running: true }), false],
  ['mac · connected, bridge session', snap({ mode: 'auto', running: true, instrumented: true, daemon: 'running', session: 'bridge' }), true],
  ['mac · connected, adopted direct session', snap({ mode: 'direct', daemon: 'running', session: 'direct', external: true }), true],
  ['win · auto · app closed', snap({ platform: 'win', mode: 'auto' }), false],
  ['win · auto · app open', snap({ platform: 'win', mode: 'auto', running: true }), false],
  ['win · bridge · app closed (blocked)', snap({ platform: 'win', mode: 'bridge' }), false]
]

// ?only=1,7 narrows the page down when you are looking at one thing.
const pick = new URLSearchParams(location.search).get('only')
const CASES = pick ? pick.split(',').map((i) => ALL[Number(i)]).filter(Boolean) : ALL

document.body.style.background = '#12100d'
createRoot(document.getElementById('root')!).render(
  <div style={{ padding: 20, display: 'grid', gap: 26, width: 700 }}>
    {CASES.map(([label, s, live]) => (
      <div key={label}>
        <div style={{
          font: "700 9.5px 'Bricolage Grotesque', system-ui", letterSpacing: '.14em',
          textTransform: 'uppercase', color: '#6b6355', marginBottom: 7
        }}>{label}</div>
        <SignalPath snap={s} />
        <ModeChoice snap={s} live={live} />
      </div>
    ))}
  </div>
)
