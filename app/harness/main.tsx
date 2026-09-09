import '@fontsource-variable/bricolage-grotesque'
import '@fontsource-variable/martian-mono'
import '@singz/ui/kit.css'
import '../src/renderer/src/styles.css'
import { useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import { Measured } from '../src/renderer/src/components/Measured'

/*
 * A look at the measured strip in each state it has, without a daemon, a device
 * or a recorded riff — the IPC bridge is stubbed.
 *
 * One state per page load (?p=N). The component reads the global bridge inside
 * an effect, and effects run after every render, so putting all the panels on
 * one page would leave them all seeing whichever stub was installed last.
 */

const device = { index: 3, name: 'Quad Cortex', inputs: 8, outputs: 8 }

const stub = (over: Record<string, unknown> = {}): unknown => ({
  leveling: {
    audio: async () => ({ available: true, quadCortex: device, sample: null }),
    sampleArm: async () => idle,
    sampleStatus: async () => idle,
    sampleStop: async () => done,
    sampleDiscard: async () => idle,
    measure: async () => reading,
    autolevel: async () => ({ target: -18, metric: 'lufs', iterations: [], written: false }),
    ...over
  }
})

const env = (n: number, seed = 1): number[] =>
  Array.from({ length: n }, (_, i) => {
    const t = i / n
    const pluck = Math.exp(-((t * 6) % 1) * 3)
    return Math.max(0.04, pluck * (0.55 + 0.4 * Math.sin(seed + t * 11)))
  })

const idle = {
  state: 'idle', seconds_recorded: 0, input_dbfs: null,
  threshold_dbfs: -40, max_seconds: 30, silence_seconds: 1.5, peaks: []
}
const armed = {
  state: 'armed', seconds_recorded: 0, input_dbfs: -46.3,
  threshold_dbfs: -40, max_seconds: 30, silence_seconds: 1.5, peaks: []
}
const recording = {
  state: 'recording', seconds_recorded: 7.4, input_dbfs: -7.2,
  threshold_dbfs: -40, max_seconds: 30, silence_seconds: 1.5, peaks: env(150)
}
const done = {
  state: 'done', seconds_recorded: 6.82, input_dbfs: -8,
  threshold_dbfs: -40, max_seconds: 30, silence_seconds: 1.5,
  duration_s: 6.82, peak_dbfs: -3.1, lufs: -16.4, peaks: env(240, 3)
}
const reading = {
  duration_s: 6.82, silent: false, sample_peak_dbfs: -3.1,
  lufs_integrated: -24.8, true_peak_dbtp: -3.1
}

const withSample = { available: true, quadCortex: device, sample: '~/.qc-mcp/reference_di.wav' }

const ready = stub({ audio: async () => withSample, sampleStatus: async () => done })
const listening = stub({ sampleStatus: async () => armed, sampleArm: async () => armed })
const taking = stub({ sampleStatus: async () => recording, sampleArm: async () => recording })
const missing = stub({
  audio: async () => ({
    available: false, error: 'sounddevice is required',
    hint: "install the audio extra:  pip install -e '.[audio]'",
    quadCortex: null, sample: null
  })
})

type Panel = [string, unknown, Record<string, unknown>]

const PANELS: Panel[] = [
  ['1 · no riff recorded yet', stub(), {}],
  ['1b · listening for the first note', listening, {}],
  ['1c · recording', taking, {}],
  ['2 · ready — measure, suggest, or level to target', ready, {}],
  ['3 · a run in progress, pass 2', ready,
    { step: { n: 2, measured: -18.6, delta_db: 0.6, true_peak_dbtp: -2.4, wrote_db: 6.8 } }],
  ['4 · held by the true-peak guard', ready,
    { step: { n: 3, measured: -19.1, delta_db: 1.1, true_peak_dbtp: -0.2, wrote_db: 7.4,
              limited_by: 'true_peak', backed_off_to_db: 6.6 } }],
  ['5 · audio extra not installed', missing, {}]
]

// The recorder only leaves idle when the pedal is pressed, so a panel that wants
// to show armed or recording has to press it.
function AutoArm(): null {
  useEffect(() => {
    const t = setTimeout(() => {
      const b = document.querySelector<HTMLButtonElement>('.pedalbtn')
      if (b && b.classList.contains('idle')) b.click()
    }, 60)
    return () => clearTimeout(t)
  }, [])
  return null
}

const which = Number(new URLSearchParams(location.search).get('p') ?? 0)
const [title, api, rest] = PANELS[which] ?? PANELS[0]
;(window as unknown as { patchbay: unknown }).patchbay = api
document.body.style.background = '#12100d'

createRoot(document.getElementById('root')!).render(
  <div style={{ padding: 20, width: 1080 }}>
    <div
      style={{
        font: "700 9.5px 'Bricolage Grotesque', system-ui", letterSpacing: '.14em',
        textTransform: 'uppercase', color: '#6b6355', marginBottom: 9
      }}
    >
      {title}
    </div>
    {[1, 2].includes(which) && <AutoArm />}
    <Measured
      live
      presetName="Fender Scenes"
      target={-18}
      onTarget={() => {}}
      onTrimmed={() => {}}
      step={null}
      playing={false}
      onPlay={() => {}}
      foot={{ current: null }}
      {...rest}
    />
  </div>
)
