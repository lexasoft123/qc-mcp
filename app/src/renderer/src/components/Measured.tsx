import { useCallback, useEffect, useRef, useState } from 'react'
import type { MutableRefObject } from 'react'
import { Badge, Button } from '@singz/ui'
import type { AudioState, RiffList, SampleState } from '@shared/types'
import { T, t } from '../i18n.js'
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

const POLL_MS = 150

const db = (v: number | null | undefined): string =>
  v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)}`

export function Measured({
  live, presetName, playing, onPlay, foot, onSample
}: {
  /** The daemon is up. Without it there is nothing to talk to. */
  live: boolean
  /**
   * What is loaded on the device, for the label only — measuring does not need it
   * to be on the bench. The lanes are the service's business: a preset's signal
   * enters on one and leaves by another, and naming a row here would get it wrong.
   */
  presetName: string | null
  /** The recorder's state as it changes, and whether there is a take to
   *  measure with — the input rail and the drawer's tools read both. */
  onSample?: (s: SampleState | null, ready: boolean) => void
  /** The riff is sounding. Owned by the view: the service's `play` event is
   *  the only thing that clears it, and the view is what hears that event. */
  playing: boolean
  onPlay: () => void
  /** Where this hands the view its footswitch action, so the one keyboard
   *  handler can route `space` here without owning the recorder. */
  foot: MutableRefObject<(() => void) | null>
}): React.JSX.Element | null {
  const [audio, setAudio] = useState<AudioState | null>(null)
  const [sample, setSample] = useState<SampleState | null>(null)
  const [err, setErr] = useState<string | null>(null)
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
    setErr(null); quietSince.current = null
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
  const ready = Boolean(audio?.sample) && !busyRecording
  useEffect(() => { onSample?.(sample, ready) },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sample, ready])

  /* The pedal is a footswitch, so the space bar is the foot. One key, whatever
     the recorder is doing. The keystroke itself is the view's (keys.ts declares
     it once, beside every other binding); this only says what it does now. */
  foot.current = !live ? null
    : recording ? () => void keep()
    : armed ? () => void discard()
    : () => void arm()

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

  const peaks = sample?.peaks ?? []
  const silenceLeft = recording && quietSince.current !== null
    ? (sample?.silence_seconds ?? 1.5) - (Date.now() - quietSince.current) / 1000
    : null

  return (
    <div className={`lvl-measured ${busyRecording ? 'live' : ''}`}>
      <div className="lvl-measured-head">
        <span className="eyebrow">{t('msd.riff')}</span>
        {ready && <Badge className="live">{t('msd.ready')}</Badge>}
        {recording && <Badge className="attn">{t('msd.recording')}</Badge>}
        {armed && <Badge className="attn">{t('msd.listening')}</Badge>}
        <span className="fine">
          {busyRecording ? t('msd.hint.recording') : ready ? t('msd.hint.ready') : t('msd.hint.none')}
        </span>
        <span className="grow" />
      </div>

      {!busyRecording && riffs && riffs.riffs.length > 0 && (
        <div className="riffs">
          <span className="eyebrow">{ready ? t('msd.orOurs') : t('msd.noGuitar')}</span>
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
                <span>{loadingRiff === r.name ? t('msd.loading') : `${r.seconds.toFixed(1)}s`}</span>
              </button>
            ))}
          </div>
          <p className="hint">
            {riffs.loaded
              ? riffs.riffs.find((r) => r.name === riffs.loaded)?.why
              : t('msd.riffsHint')}
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
                  {t('msd.ofMax', { max: (sample?.max_seconds ?? 30).toFixed(0) })}
                </span>
                <span className="grow" />
                {silenceLeft !== null && silenceLeft > 0 && (
                  <span className="warn">{t('msd.stopsIn', { s: silenceLeft.toFixed(1) })}</span>
                )}
                <span className="lvl-measured-in">{t('msd.inDbfs', { db: db(sample?.input_dbfs) })}</span>
              </div>
              {recording
                ? <Wave peaks={peaks} live height={84} />
                : <InputMeter dbfs={sample?.input_dbfs ?? null}
                              thresholdDbfs={sample?.threshold_dbfs ?? -40} />}
              {armed && (
                <div className="hint">{t('msd.threshold')}</div>
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
                <div className="fact"><dt>{t('msd.duration')}</dt>
                  <dd>{(sample?.duration_s ?? 0).toFixed(2)} s</dd></div>
                <div className="fact"><dt>{t('msd.peak')}</dt>
                  <dd>{db(sample?.peak_dbfs)} dBFS</dd></div>
                <div className="fact"><dt>{t('msd.loudness')}</dt>
                  <dd>{db(sample?.lufs)} LUFS</dd></div>
                <div className="fact"><dt>{t('msd.preset')}</dt>
                  <dd className="muted">{presetName ?? t('msd.noneLoaded')}</dd></div>
              </dl>
              <div className="lvl-measured-acts">
                <Button size="sm" onClick={onPlay} title={t('msd.playHint')}>
                  {playing ? t('msd.stop') : t('msd.play')} <kbd>P</kbd>
                </Button>
                <Button size="sm" onClick={() => void arm()}>{t('msd.reRecord')}</Button>
                <span className="grow" />
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
        {t('msd.verdict.good', { peak: peak.toFixed(1) })}
      </div>
    )
  }
  return (
    <div className="verdict bad">
      <span className="dot" />
      <span>
        <T k="msd.verdict.bad" />{' '}
        {quiet
          ? t('msd.verdict.quiet', { peak: peak.toFixed(1) })
          : t('msd.verdict.thin', { lufs: lufs?.toFixed(1) ?? '' })}{' '}
        {t('msd.verdict.fix')}
      </span>
    </div>
  )
}
