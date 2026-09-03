import { useCallback, useEffect, useRef, useState } from 'react'
import { Badge, Button } from '@singz/ui'
import type { AudioState, AutoResult, AutoStep, Measurement, SampleState } from '@shared/types'

/**
 * The measured half of the bench.
 *
 * The bench proper is levelled by ear: play, look at the meter, turn the fader.
 * This strip removes the ear from the loop — it plays one riff of yours into the
 * preset over the USB reamp path, measures the loudness that comes back, and can
 * trim until it lands on a target.
 *
 * It is deliberately a strip above the bench rather than a separate view: the
 * numbers are only meaningful next to the faders they explain, and the same riff
 * has to be used for every preset or the comparison means nothing.
 */

const TARGETS = [-14, -16, -18, -20, -23]

const db = (v: number | null | undefined): string =>
  v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)}`

/** A take is only usable as a reference once it has been kept. */
const hasSample = (a: AudioState | null): boolean => Boolean(a?.sample)

export function Measured({
  live, presetName, target, onTarget, step, onTrimmed
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
}): React.JSX.Element | null {
  const [audio, setAudio] = useState<AudioState | null>(null)
  const [sample, setSample] = useState<SampleState | null>(null)
  const [reading, setReading] = useState<Measurement | null>(null)
  const [result, setResult] = useState<AutoResult | null>(null)
  const [busy, setBusy] = useState<'measure' | 'auto' | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const poll = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (!live) return
    window.patchbay.leveling.audio().then(setAudio).catch(() => undefined)
  }, [live])

  /* A take ends on its own — on silence or at the cap — so the button cannot be
     what learns about it. Poll only while armed or recording, never at rest. */
  const watch = useCallback((): void => {
    if (poll.current) clearInterval(poll.current)
    poll.current = setInterval(() => {
      window.patchbay.leveling
        .sampleStatus()
        .then((s) => {
          setSample(s)
          if (s.state === 'done' || s.state === 'idle') {
            if (poll.current) clearInterval(poll.current)
            poll.current = null
            if (s.state === 'done') void keep()
          }
        })
        .catch(() => undefined)
    }, 200)
  }, [])

  useEffect(() => () => { if (poll.current) clearInterval(poll.current) }, [])

  const keep = async (): Promise<void> => {
    try {
      const s = await window.patchbay.leveling.sampleStop()
      setSample(s)
      setAudio(await window.patchbay.leveling.audio())
    } catch (e) { setErr((e as Error).message) }
  }

  const arm = async (): Promise<void> => {
    setErr(null); setReading(null); setResult(null)
    try {
      setSample(await window.patchbay.leveling.sampleArm())
      watch()
    } catch (e) { setErr((e as Error).message) }
  }

  const discard = async (): Promise<void> => {
    if (poll.current) { clearInterval(poll.current); poll.current = null }
    try { setSample(await window.patchbay.leveling.sampleDiscard()) }
    catch (e) { setErr((e as Error).message) }
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

  if (!live) return null

  // The extra is optional, and saying so plainly beats a dead button.
  if (audio && !audio.available) {
    return (
      <div className="strip lvl-measured quiet">
        <span className="grow">
          Measured leveling needs the audio extra. {audio.hint ?? ''}
        </span>
      </div>
    )
  }

  const recording = sample?.state === 'recording'
  const armed = sample?.state === 'armed'
  const ready = hasSample(audio)
  const running = busy === 'auto'
  const shown = step ?? (result?.iterations.at(-1) ?? null)

  return (
    <div className="lvl-measured">
      <div className="lvl-measured-row">
        <span className="eyebrow">Reference riff</span>

        {!ready && !armed && !recording && (
          <>
            <Button size="sm" onClick={() => void arm()}>Record a riff</Button>
            <span className="fine">
              Played into every preset, so they are all measured with the same signal.
            </span>
          </>
        )}

        {(armed || recording) && (
          <>
            <Badge className="attn">{recording ? 'Recording' : 'Listening'}</Badge>
            <span className="fine">
              {recording
                ? `${sample?.seconds_recorded?.toFixed(1) ?? '0.0'} s — stops when you stop playing`
                : 'Play to start'}
            </span>
            <span className="lvl-measured-in">
              in {db(sample?.input_dbfs ?? null)} dBFS
            </span>
            <span className="grow" />
            <Button size="sm" onClick={() => void keep()}>Stop</Button>
            <Button size="sm" onClick={() => void discard()}>Cancel</Button>
          </>
        )}

        {ready && !armed && !recording && (
          <>
            <Badge className="live">Ready</Badge>
            {sample?.duration_s !== undefined && (
              <span className="fine">
                {sample.duration_s.toFixed(2)} s · {db(sample.lufs)} LUFS
              </span>
            )}
            <span className="grow" />
            <label className="lvl-measured-target">
              Target
              <select
                value={target}
                onChange={(e) => onTarget(Number(e.target.value))}
                disabled={running}
              >
                {TARGETS.map((t) => (
                  <option key={t} value={t}>{t} LUFS</option>
                ))}
              </select>
            </label>
            <Button size="sm" disabled={busy !== null}
                    onClick={() => void measure()}>
              {busy === 'measure' ? 'Measuring…' : 'Measure'}
            </Button>
            <Button size="sm" disabled={busy !== null}
                    onClick={() => void run(true)}>
              Suggest
            </Button>
            <Button size="sm" variant="primary" disabled={busy !== null}
                    onClick={() => void run(false)}>
              {running ? 'Levelling…' : 'Level to target'}
            </Button>
            <Button size="sm" onClick={() => void arm()}>Re-record</Button>
          </>
        )}
      </div>

      {/* One line of numbers, whichever action produced them. */}
      {(reading || shown || result) && (
        <div className="lvl-measured-out">
          {reading && !reading.error && (
            <>
              <b>{db(reading.lufs_integrated)}</b> LUFS
              <span className="sep">·</span>
              true peak {db(reading.true_peak_dbtp)} dBTP
              <span className="sep">·</span>
              {(reading.lufs_integrated ?? 0) !== 0 && (
                <>needs {db(target - (reading.lufs_integrated ?? 0))} dB</>
              )}
            </>
          )}
          {!reading && shown && (
            <>
              pass {shown.n}
              <span className="sep">·</span>
              <b>{db(shown.measured)}</b> LUFS
              <span className="sep">·</span>
              off by {db(shown.delta_db)} dB
              {shown.wrote_db !== null && (
                <>
                  <span className="sep">·</span>
                  trim {db(shown.wrote_db)} dB
                </>
              )}
              {shown.limited_by === 'true_peak' && (
                <>
                  <span className="sep">·</span>
                  <span className="warn">
                    held at {db(shown.backed_off_to_db)} dB — the true peak was
                    against the ceiling
                  </span>
                </>
              )}
            </>
          )}
          {result && !running && (
            <>
              <span className="sep">·</span>
              {result.suggested_db !== undefined ? (
                <span>suggestion only, nothing written</span>
              ) : result.converged ? (
                <span className="ok">
                  landed within {Math.abs(result.final_delta_db ?? 0).toFixed(1)} dB
                </span>
              ) : (
                <span className="warn">did not settle — {db(result.final_delta_db)} dB out</span>
              )}
            </>
          )}
        </div>
      )}

      {err && (
        <div className="lvl-measured-err">
          {err}
          <Button size="sm" onClick={() => setErr(null)}>Dismiss</Button>
        </div>
      )}
    </div>
  )
}
