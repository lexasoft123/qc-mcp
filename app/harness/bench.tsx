/*
 * The whole Leveling view, against a fake bench.
 *
 *   bench.html?s=fresh|measured|applied|error|empty|closed&h=620|788|1000&w=980
 *
 * The stub (bench-stub.ts, imported FIRST) answers every call the view makes
 * and streams a meter, so the rail, the rows and the dock move. `measured`
 * presses M for you and the stub emits a measurement per preset; `applied`
 * then presses ⌘↵; `error` fails one preset; `closed` presses ↵ to shut the
 * focused row's drawer (it opens by default).
 * Nothing here is typechecked by `npm run typecheck` — build it
 * (`npx vite build -c harness/vite.config.ts`) and look.
 */
// The stub installs `window.patchbay`; the view's store reads it the moment
// its module evaluates, so the view is imported DYNAMICALLY below, after this
// module body has run — a static import would let the bundler order the chunks.
import { height, prefs, scenario, setRerender, snapOf, wait, width } from './bench-stub'
import '@fontsource-variable/bricolage-grotesque'
import '@fontsource-variable/martian-mono'
import '@singz/ui/kit.css'
import '../src/renderer/src/styles.css'
import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import type { Snapshot } from '../src/shared/types'
import type { Leveling as LevelingView } from '../src/renderer/src/views/Leveling'

function Harness({ Leveling }: { Leveling: typeof LevelingView }): React.JSX.Element {
  const [, tick] = useState(0)
  setRerender(() => tick((n) => n + 1))
  const snap = snapOf() as Snapshot
  return <Leveling snap={snap} />
}

document.body.style.background = '#0a0908'
document.body.style.margin = '0'
void import('../src/renderer/src/views/Leveling').then(({ Leveling }) => {
  createRoot(document.getElementById('root')!).render(
    <div style={{ width, height, margin: '20px auto', border: '1px solid #333', borderRadius: 14,
                  overflow: 'hidden', background: 'var(--sz-bg)', display: 'grid' }}>
      <Harness Leveling={Leveling} />
    </div>
  )
})

// drive the scenario once the view has mounted
const key = (k: string, mod = false): void =>
  window.dispatchEvent(new KeyboardEvent('keydown', { key: k, metaKey: mod, ctrlKey: mod, bubbles: true }))
setTimeout(() => {
  void (async () => {
    if (['measured', 'applied', 'error'].includes(scenario)) {
      key('m')
      await wait(500 * (prefs.bench.length + 1) + 400)
      if (scenario === 'applied') { key('Enter', true); await wait(3000) }
    }
    if (scenario === 'closed') key('Enter')
  })()
}, 900)
