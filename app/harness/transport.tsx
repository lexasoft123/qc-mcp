/*
 * The looper transport in each of its six states, one per page load:
 *
 *   transport.html?s=0   empty       s=3   recorded
 *   transport.html?s=1   armed       s=4   playing
 *   transport.html?s=2   recording   s=5   a run in progress
 *
 * The component takes the sampler as a prop, so each state is a plain object —
 * no stub, no store. Every state renders into the same grid; put the six side
 * by side and nothing may move but the buttons' faces.
 */
import './stub'
import '@fontsource-variable/bricolage-grotesque'
import '@fontsource-variable/martian-mono'
import '@singz/ui/kit.css'
import '../src/renderer/src/styles.css'
import { createRoot } from 'react-dom/client'
import type { SampleState } from '../src/shared/types'
import type { Sampler } from '../src/renderer/src/sampler'
import { RiffTransport } from '../src/renderer/src/components/RiffTransport'

const which = Number(new URLSearchParams(location.search).get('s') ?? 3)
const peaks = Array.from({ length: 160 }, (_, i) =>
  Math.max(0.05, Math.sin((i / 160) * Math.PI) ** 0.6 * (Math.abs(Math.sin(i * 0.7)) ** 2 * 0.75 + 0.3)))
const base: SampleState = {
  state: 'idle', seconds_recorded: 0, input_dbfs: null, threshold_dbfs: -40, max_seconds: 30, silence_seconds: 1.5
}
const take: SampleState = { ...base, state: 'done', path: '/riff.wav', duration_s: 6.8, peak_dbfs: -6.0, lufs: -21.4, peaks }
const noop = async (): Promise<void> => undefined
const riffs = { riffs: [{ name: 'chords', seconds: 4.5, why: 'Open chords, sustained.' }, { name: 'chug', seconds: 4.6, why: 'Palm-muted eighths.' }], loaded: null, recorded: true }
const audio = { available: true, sample: '/riff.wav', quadCortex: null }

const sampler = (over: Partial<Sampler>): Sampler => ({
  audio, sample: take, riffs, state: 'done', ready: true, silenceLeft: null, loadingRiff: null, error: null,
  arm: noop, keep: noop, discard: noop, pick: noop, foot: () => undefined, dismiss: () => undefined, ...over
})
const STATES: { title: string; sampler: Sampler; playing: boolean; running: boolean }[] = [
  { title: '0 · empty — no riff yet', playing: false, running: false,
    sampler: sampler({ sample: base, state: 'idle', ready: false, audio: { ...audio, sample: null }, riffs: { ...riffs, recorded: false } }) },
  { title: '1 · armed — listening for the first note', playing: false, running: false,
    sampler: sampler({ sample: { ...base, state: 'armed', input_dbfs: -41.2 }, state: 'armed', ready: false }) },
  { title: '2 · recording — 3.2 s in, silence counting', playing: false, running: false,
    sampler: sampler({ sample: { ...base, state: 'recording', seconds_recorded: 3.2, input_dbfs: -8.4, peaks: peaks.slice(0, 68) }, state: 'recording', ready: false, silenceLeft: 1.2 }) },
  { title: '3 · recorded — a take worth measuring with', playing: false, running: false, sampler: sampler({}) },
  { title: '4 · playing through NOLLY', playing: true, running: false, sampler: sampler({}) },
  { title: '5 · a run in progress — the riff is the service\'s now', playing: false, running: true, sampler: sampler({}) }
]
const st = STATES[which] ?? STATES[3]

document.body.style.background = '#12100d'
createRoot(document.getElementById('root')!).render(
  <div style={{ padding: 20, width: 1000 }}>
    <div style={{ font: "700 9.5px 'Bricolage Grotesque', system-ui", letterSpacing: '.14em',
                  textTransform: 'uppercase', color: '#6b6355', marginBottom: 9 }}>{st.title}</div>
    <RiffTransport sampler={st.sampler} playing={st.playing} running={st.running}
                   presetName="NOLLY" onPlay={() => undefined} />
  </div>
)
