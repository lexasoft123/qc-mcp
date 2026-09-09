/*
 * The no-jump check: the whole view is walked through its states on a timer
 * while a ResizeObserver watches every band. Any band whose size changes is
 * a jump, and is written to #log (and window.__nojump) — which harness/shot.mjs
 * prints, so this runs without a display:
 *
 *   npx electron harness/shot.mjs http://localhost:4173/nojump.html out.png 16000
 *
 * The bench rows may grow (they are the scroller); nothing else may.
 */
import { getSample, prefs, setRerender, setSample, snapOf, wait } from './bench-stub'
import '@fontsource-variable/bricolage-grotesque'
import '@fontsource-variable/martian-mono'
import '@singz/ui/kit.css'
import '../src/renderer/src/styles.css'
import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import type { Snapshot } from '../src/shared/types'
import type { Leveling as LevelingView } from '../src/renderer/src/views/Leveling'

const BANDS = ['.irail', '.lvl-top', '.xport', '.bt-head', '.bt-foot', '.dock', '.bt-body']
const log: string[] = []
const say = (line: string): void => {
  log.push(line)
  document.getElementById('log')!.textContent = log.join('\n')
  ;(window as unknown as { __nojump: string[] }).__nojump = log
}

function Harness({ Leveling }: { Leveling: typeof LevelingView }): React.JSX.Element {
  const [, tick] = useState(0)
  setRerender(() => tick((n) => n + 1))
  return <Leveling snap={snapOf() as Snapshot} />
}

document.body.style.background = '#0a0908'
document.body.style.margin = '0'

const key = (k: string, mod = false): void =>
  window.dispatchEvent(new KeyboardEvent('keydown', { key: k, metaKey: mod, ctrlKey: mod, bubbles: true }))

void import('../src/renderer/src/views/Leveling').then(async ({ Leveling }) => {
  createRoot(document.getElementById('root')!).render(
    <div style={{ width: 1078, height: 788, margin: '20px auto', overflow: 'hidden', display: 'grid' }}>
      <Harness Leveling={Leveling} />
    </div>
  )
  await wait(1200)

  // baseline sizes, then watch
  const size = new Map<string, string>()
  let phase = 'settled'
  const ro = new ResizeObserver((entries) => {
    for (const e of entries) {
      const sel = (e.target as HTMLElement).dataset.band ?? '?'
      const now = `${Math.round(e.contentRect.width)}x${Math.round(e.contentRect.height)}`
      const was = size.get(sel)
      size.set(sel, now)
      if (was !== undefined && was !== now && sel !== '.bt-body') say(`JUMP ${sel} ${was} -> ${now} during ${phase}`)
    }
  })
  for (const sel of BANDS) {
    const el = document.querySelector<HTMLElement>(sel)
    if (!el) { say(`MISSING ${sel}`); continue }
    el.dataset.band = sel
    ro.observe(el)
  }
  const step = async (name: string, act: () => void, ms: number): Promise<void> => {
    phase = name; act(); await wait(ms); say(`ok   ${name}`)
  }
  const base = getSample()
  await step('arm', () => key(' '), 800)
  await step('recording', () => setSample({ ...base, state: 'recording', seconds_recorded: 2.1, input_dbfs: -8, path: undefined,
    peaks: Array.from({ length: 60 }, (_, i) => 0.3 + 0.5 * Math.abs(Math.sin(i))) }), 800)
  await step('recorded', () => setSample({ ...base, state: 'done', path: '/riff.wav', duration_s: 6.8, peak_dbfs: -6, lufs: -21.4,
    peaks: Array.from({ length: 160 }, (_, i) => 0.3 + 0.5 * Math.abs(Math.sin(i))) }), 1200)
  await step('play', () => key('p'), 3200)
  await step('measure', () => key('m'), 500 * (prefs.bench.length + 1) + 600)
  await step('apply', () => key('Enter', true), 3000)
  await step('drawer closed', () => key('Enter'), 600)
  await step('drawer open', () => key('Enter'), 600)
  await step('focus 1', () => key('1'), 1200)
  say(log.some((l) => l.startsWith('JUMP')) ? 'RESULT: jumps found' : 'RESULT: no jumps')
})
