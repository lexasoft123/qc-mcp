import { useCallback, useEffect, useRef, useState } from 'react'
import { Badge, Button } from '@singz/ui'
import type {
  AudioState, AutoResult, AutoStep, Measurement, RiffList, SampleState
} from '@shared/types'
import { InputMeter, Pedal, Wave } from './Pedal.js'

/**
 * The measured half of the bench.
 *
 * The bench proper is levelled by ear: play, look at the meter, turn the fader.
 * This removes the ear from the loop — one riff of yours, played into the preset
 * over the USB reamp path, and the loudness that comes back is measured.
 *
 * It sits above the faders rather than in its own view because the numbers only
 * mean anything next to the knobs they explain, and because the same riff has to
 * be used for every preset or the comparison is worthless.
 */

const TARGETS = [-14, -16, -18, -20, -23]
const POLL_MS = 150

const db = (v: number | null | undefined): string =>
  v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)}`

export function Measured({
  live, presetName, target, onTarget, step, onTrimmed, playDone
}: {
  /** The daemon is up. Without it there is nothing to talk to. */
  live: boolean
  /**
   * What is loaded on the device, for the label only — measuring does not need it
   * to be on the bench. The lanes are the service's business: a preset's signal
   * enters on one and leaves by another, and naming a row here would get it wrong.
   */
  presetName: string | null
  target: number
  onTarget: (v: number) => void
  /** The newest loop iteration, pushed from the service so a run can be watched. */
  step: AutoStep | null
  /** A run wrote a trim, so the bench should re-read the preset. */
  onTrimmed: () => void
  /** Playback finished or failed, pushed from the service. */
  playDone?: number
}): React.JSX.Element | null {
  const [audio, setAudio] = useState<AudioState | null>(null)
  const [sample, setSample] = useState<SampleState | null>(null)
  const [reading, setReading] = useState<Measurement | null>(null)
  const [result, setResult] = useState<AutoResult | null>(null)
  const [busy, setBusy] = useState<'measure' | 'auto' | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [playing, setPlaying] = useState(false)
  const poll = useRef<ReturnType<typeof setInterval> | null>(null)
  const quietSince = useRef<number | null>(null)

  useEffect(() => {
    if (!live) return
    window.patchbay.leveling
      .audio()
      .then((a) => {
        setAudio(a)
        // A riff saved in an earlier session is only a path until it is read back;
        // without this "Ready" sits over a blank waveform and zeroed facts.
        if (a.sample) {
          window.patchbay.leveling.sampleStatus().then(setSample).catch(() => undefined)
        }
      })
      .catch(() => undefined)
  }, [live])

  const stopPoll = (): void => {
    if (poll.current) clearInterval(poll.current)
    poll.current = null
  }
  useEffect(() => stopPoll, [])
  // The service says when playback ends; the button must not stay on "Stop".
  useEffect(() => { if (playDone) setPlaying(false) }, [playDone])

  const keep = useCallback(async (): Promise<void> => {
    stopPoll()
    try {
      const s = await window.patchbay.leveling.sampleStop()
      setSample(s)
      if (s.error) setErr(s.error)
      setAudio(await window.patchbay.leveling.audio())
    } catch (e) { setErr((e as Error).message) }
  }, [])

  /* A take ends by itself — on silence or at the cap — so the button cannot be what
     learns about it. Poll only while armed or recording, never at rest. */
  const watch = useCallback((): void => {
    stopPoll()
    poll.current = setInterval(() => {
      window.patchbay.leveling
        .sampleStatus()
        .then((s) => {
          setSample(s)
          if (s.state === 'recording') {
            const quiet = (s.input_dbfs ?? -99) < s.threshold_dbfs
            if (quiet && quietSince.current === null) quietSince.current = Date.now()
            if (!quiet) quietSince.current = null
          }
          if (s.state === 'done') { stopPoll(); void keep() }
          if (s.state === 'idle') stopPoll()
        })
        .catch(() => undefined)
    }, POLL_MS)
  }, [keep])

  const arm = useCallback(async (): Promise<void> => {
    setErr(null); setReading(null); setResult(null); quietSince.current = null
    try {
      setSample(await window.patchbay.leveling.sampleArm())
      watch()
    } catch (e) { setErr((e as Error).message) }
  }, [watch])

  const discard = useCallback(async (): Promise<void> => {
    stopPoll(); quietSince.current = null
    try { setSample(await window.patchbay.leveling.sampleDiscard()) }
    catch (e) { setErr((e as Error).message) }
  }, [])

  const play = async (): Promise<void> => {
    setErr(null)
    try {
      setPlaying(true)
      await window.patchbay.leveling.samplePlay()
    } catch (e) { setErr((e as Error).message); setPlaying(false) }
  }

  const stopPlay = async (): Promise<void> => {
    try { await window.patchbay.leveling.sampleStopPlay() }
    catch { /* it may have finished on its own */ }
    setPlaying(false)
  }

  const measure = async (): Promise<void> => {
    setBusy('measure'); setErr(null); setResult(null)
    try {
      const m = await window.patchbay.leveling.measure()
      setReading(m)
      if (m.error) setErr(m.error)
    } catch (e) { setErr((e as Error).message) } finally { setBusy(null) }
  }

  const run = async (dryRun: boolean): Promise<void> => {
    setBusy('auto'); setErr(null); setReading(null)
    try {
      const r = await window.patchbay.leveling.autolevel({ target, dryRun })
      setResult(r)
      if (r.error) setErr(r.error)
      if (r.written) onTrimmed()
    } catch (e) { setErr((e as Error).message) } finally { setBusy(null) }
  }

  const state = (sample?.state ?? 'idle') as 'idle' | 'armed' | 'recording' | 'done'
  const [riffs, setRiffs] = useState<RiffList | null>(null)
  const [loadingRiff, setLoadingRiff] = useState<string | null>(null)

  /* The riffs the bench brings itself. Fetched once the strip is live, because
     until they were reachable the only way to start was to plug a guitar in —
     and the first riff anyone records is usually not good enough to measure
     with. */
  useEffect(() => {
    if (!live) return
    void window.patchbay.leveling.riffs().then(setRiffs).catch(() => undefined)
  }, [live])

  const useRiff = async (name: string): Promise<void> => {
    setLoadingRiff(name)
    try {
      const s2 = await window.patchbay.leveling.useRiff(name)
      setSample(s2 as SampleState)
      setRiffs(await window.patchbay.leveling.riffs())
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setLoadingRiff(null)
    }
  }
  const recording = state === 'recording'
  const armed = state === 'armed'
  const busyRecording = recording || armed

  /* The pedal is a footswitch, so the space bar is the foot. One key, whatever the
     recorder is doing — and never while a text field has the caret. */
  useEffect(() => {
    if (!live) return
    const onKey = (e: KeyboardEvent): void => {
      if (e.code !== 'Space' || e.repeat) return
      const el = e.target as HTMLElement | null
      if (el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) return
      e.preventDefault()
      if (recording) void keep()
      else if (armed) void discard()
      else void arm()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [live, recording, armed, arm, keep, discard])

  if (!live) return null

  if (audio && !audio.available) {
    return (
      <div className="strip lvl-measured quiet">
        <span className="grow">
          Measured leveling needs the audio extra. {audio.hint ?? ''}
        </span>
      </div>
    )
  }

  const ready = Boolean(audio?.sample) && !busyRecording
  const running = busy === 'auto'
  const shown = step ?? result?.iterations.at(-1) ?? null
  const peaks = sample?.peaks ?? []
  const silenceLeft = recording && quietSince.current !== null
    ? (sample?.silence_seconds ?? 1.5) - (Date.now() - quietSince.current) / 1000
    : null

  return (
    <div className={`lvl-measured ${busyRecording ? 'live' : ''}`}>
      <div className="lvl-measured-head">
        <span className="eyebrow">Reference riff</span>
        {ready && <Badge className="live">Ready</Badge>}
        {recording && <Badge className="attn">Recording</Badge>}
        {armed && <Badge className="attn">Listening</Badge>}
        <span className="fine">
          {busyRecording
            ? 'Recording starts on your first note and stops when you stop playing.'
            : ready
              ? 'Played into every preset, so they are all measured with the same signal.'
              : 'Record one riff. It is played into every preset, so they are all measured with the same signal.'}
        </span>
        <span className="grow" />
        {ready && (
          <div className="lvl-measured-acts-head">
            <label className="lvl-measured-target">
              <abbr title="Broadcast loudness standard (EBU R128). -18 LUFS leaves headroom for a band and matches most amp models' own output.">Target</abbr>
              <select value={target} onChange={(e) => onTarget(Number(e.target.value))}
                      disabled={running}>
                {TARGETS.map((t) => <option key={t} value={t}>{t} LUFS</option>)}
              </select>
            </label>
            <Button size="sm" disabled={busy !== null} onClick={() => void measure()}>
              {busy === 'measure' ? 'Measuring…' : 'Measure'}
            </Button>
            <Button size="sm" disabled={busy !== null} onClick={() => void run(true)}>
              Suggest
            </Button>
            <Button size="sm" variant="primary" disabled={busy !== null}
                    onClick={() => void run(false)}>
              {running ? 'Levelling…' : 'Level to target'}
            </Button>
          </div>
        )}
      </div>

      {!busyRecording && riffs && riffs.riffs.length > 0 && (
        <div className="riffs">
          <span className="eyebrow">
            {ready ? 'Or use one of ours' : 'No guitar to hand?'}
          </span>
          <div className="riff-row">
            {riffs.riffs.map((r) => (
              <button
                type="button" key={r.name}
                className={`riff${riffs.loaded === r.name ? ' on' : ''}`}
                disabled={loadingRiff !== null}
                title={r.why}
                onClick={() => void useRiff(r.name)}
              >
                <b>{r.name}</b>
                <span>{loadingRiff === r.name ? 'loading…' : `${r.seconds.toFixed(1)}s`}</span>
              </button>
            ))}
          </div>
          <p className="hint">
            {riffs.loaded
              ? riffs.riffs.find((r) => r.name === riffs.loaded)?.why
              : 'Synthesised, always the same, and loud enough to measure with. '
                + 'Recording your own is better — it is your playing — but these need no room and no guitar.'}
          </p>
        </div>
      )}

      <div className="lvl-measured-body">
        <Pedal
          state={state}
          seconds={sample?.seconds_recorded ?? 0}
          maxSeconds={sample?.max_seconds ?? 30}
          silenceLeft={silenceLeft}
          onArm={() => void arm()}
          onStop={() => void (recording ? keep() : discard())}
        />

        <div className="lvl-measured-main">
          {busyRecording ? (
            <>
              <div className="lvl-measured-nums">
                <span className="big">
                  {(sample?.seconds_recorded ?? 0).toFixed(1)}<em>s</em>
                </span>
                <span className="eyebrow">
                  of {(sample?.max_seconds ?? 30).toFixed(0)} s max
                </span>
                <span className="grow" />
                {silenceLeft !== null && silenceLeft > 0 && (
                  <span className="warn">stops in {silenceLeft.toFixed(1)} s</span>
                )}
                <span className="lvl-measured-in">in {db(sample?.input_dbfs)} dBFS</span>
              </div>
              {recording
                ? <Wave peaks={peaks} live height={84} />
                : <InputMeter dbfs={sample?.input_dbfs ?? null}
                              thresholdDbfs={sample?.threshold_dbfs ?? -40} />}
              {armed && (
                <div className="hint">
                  The dashed line is the start threshold — play above it to begin.
                </div>
              )}
            </>
          ) : ready ? (
            <>
              <Wave peaks={peaks} height={72} />
              {/* Say whether the take is any good. It used to show peak, LUFS
                  and duration and leave the judgement to somebody who has no
                  way to make it — and every number downstream depends on this
                  one signal being loud and busy enough. The thresholds are the
                  ones tests/test_leveling_e2e.py holds the shipped riffs to. */}
              <Verdict peak={sample?.peak_dbfs ?? null} lufs={sample?.lufs ?? null} />
              <dl className="facts lvl-measured-facts">
                <div className="fact"><dt>Duration</dt>
                  <dd>{(sample?.duration_s ?? 0).toFixed(2)} s</dd></div>
                <div className="fact"><dt>Peak</dt>
                  <dd>{db(sample?.peak_dbfs)} dBFS</dd></div>
                <div className="fact"><dt>Loudness</dt>
                  <dd>{db(sample?.lufs)} LUFS</dd></div>
                <div className="fact"><dt>Preset</dt>
                  <dd className="muted">{presetName ?? 'none loaded'}</dd></div>
              </dl>
              <div className="lvl-measured-acts">
                <Button size="sm" disabled={playing}
                        onClick={() => void (playing ? stopPlay() : play())}
                        title="Hear the riff through this preset (P)">
                  {playing ? 'Stop' : 'Play through preset'} <kbd>P</kbd>
                </Button>
                <Button size="sm" onClick={() => void arm()}>Re-record</Button>
                <span className="grow" />
                {(reading || shown || result) && (
                  <span className="lvl-measured-out">
                    {reading && !reading.error && (
                      <>
                        <b>{db(reading.lufs_integrated)}</b> LUFS
                        <span className="sep">·</span>
                        true peak {db(reading.true_peak_dbtp)} dBTP
                        <span className="sep">·</span>
                        needs {db(target - (reading.lufs_integrated ?? 0))} dB
                      </>
                    )}
                    {!reading && shown && (
                      <>
                        pass {shown.n}<span className="sep">·</span>
                        <b>{db(shown.measured)}</b> LUFS<span className="sep">·</span>
                        off by {db(shown.delta_db)} dB
                        {shown.wrote_db !== null && (
                          <><span className="sep">·</span>trim {db(shown.wrote_db)} dB</>
                        )}
                        {shown.limited_by === 'true_peak' && (
                          <><span className="sep">·</span>
                            <span className="warn">
                              held at {db(shown.backed_off_to_db)} dB — the true peak was
                              against the ceiling
                            </span></>
                        )}
                      </>
                    )}
                    {result && !running && (
                      <>
                        <span className="sep">·</span>
                        {result.suggested_db !== undefined
                          ? <span>suggestion only, nothing written</span>
                          : result.converged
                            ? <span className="ok">
                                landed within {Math.abs(result.final_delta_db ?? 0).toFixed(1)} dB
                              </span>
                            : <span className="warn">
                                did not settle — {db(result.final_delta_db)} dB out
                              </span>}
                      </>
                    )}
                  </span>
                )}
              </div>
            </>
          ) : (
            <div className="lvl-measured-empty">
              <InputMeter dbfs={null} thresholdDbfs={-40} />
              <div className="hint">
                Recorded from the dry DI on USB 1/2, so the riff is your instrument with
                no preset on it. Measuring is silent in the room — the output is tapped
                to USB while it runs.
              </div>
            </div>
          )}
        </div>
      </div>

      {err && (
        <div className="lvl-measured-err">
          <span className="grow">{err}</span>
          <Button size="sm" onClick={() => setErr(null)}>Dismiss</Button>
        </div>
      )}
    </div>
  )
}

/**
 * Is this take worth measuring with?
 *
 * A riff that is quiet or mostly gaps produces numbers that look exactly like
 * preset differences. The first one ever recorded against this feature peaked
 * at -22.8 dBFS with three-quarters of its samples near silence, and nothing
 * anywhere said so — every reading taken with it was noise wearing a decimal
 * point. This is the one cheap moment to catch that.
 */
function Verdict({ peak, lufs }: { peak: number | null; lufs: number | null }): React.JSX.Element | null {
  if (peak === null) return null
  const quiet = peak < -12
  const thin = lufs !== null && lufs < -30
  if (!quiet && !thin) {
    return (
      <div className="verdict good">
        <span className="dot" />
        Good to measure with — {peak.toFixed(1)} dBFS peak, plenty to hear through a preset.
      </div>
    )
  }
  return (
    <div className="verdict bad">
      <span className="dot" />
      <span>
        <b>Too quiet to measure with.</b>{' '}
        {quiet
          ? `It peaks at ${peak.toFixed(1)} dBFS; -12 or hotter gives a preset something to work on.`
          : `It averages ${lufs?.toFixed(1)} LUFS — mostly gaps, so the gaps get measured.`}{' '}
        Play harder and re-record, or use one of the sample riffs.
      </span>
    </div>
  )
}
