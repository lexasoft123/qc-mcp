import { t } from '../i18n.js'
import type { Sampler } from '../sampler.js'

/**
 * The reference riff, as a looper.
 *
 * Two big buttons and a tape strip, because that is the gesture: REC arms,
 * your first note starts the take, the silence after ends it, PLAY hears it
 * through whatever preset is loaded. The state lives in the buttons' faces —
 * "Rec", "Listening", "Stop", "Re-rec" — rather than in a paragraph beside
 * them, and `space` is REC in every state.
 *
 * ONE grid for all six states (empty, armed, recording, recorded, playing, a
 * run in progress). Everything that can appear is mounted all the time; a
 * state only changes which alternative is visible and what the buttons say.
 * The tape holds the waveform and the input meter at once, the facts cell is
 * sized by its widest alternative, and the footline is a single fixed track
 * with the verdict, an error, or the key hints — so nothing below it moves
 * when a take starts or ends.
 */

const fmt = (v: number | null | undefined): string =>
  v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${v.toFixed(1)}`

/** Input level as a fraction of the meter, which runs -60..0 dBFS. */
const pos = (dbfs: number | null): number =>
  dbfs === null ? 0 : Math.max(0, Math.min(1, (dbfs + 60) / 60))

export function RiffTransport({
  sampler, playing, onPlay, presetName, running, primary
}: {
  sampler: Sampler
  /** The riff is sounding through the loaded preset. */
  playing: boolean
  onPlay: () => void
  presetName: string | null
  /** A measurement or audition owns the device: nothing here may start. */
  running: boolean
  /** REC is the screen's one accented control right now (no riff yet). */
  primary: boolean
}): React.JSX.Element {
  const { audio, sample, riffs, state, ready, silenceLeft, loadingRiff, error } = sampler
  const noAudio = audio !== null && !audio.available
  const recording = state === 'recording'
  const armed = state === 'armed'
  const hasTake = Boolean(sample?.path)
  const peaks = sample?.peaks ?? []

  const face: 'empty' | 'armed' | 'recording' | 'recorded' | 'playing' | 'running' =
    running ? 'running' : playing ? 'playing' : recording ? 'recording' : armed ? 'armed'
      : hasTake ? 'recorded' : 'empty'

  const recLabel = recording ? t('lvl.tp.stop') : armed ? t('lvl.tp.listening')
    : hasTake ? t('lvl.tp.rerec') : t('lvl.tp.rec')
  const recDisabled = noAudio || playing || running
  const playDisabled = noAudio || !ready || running

  const verdict = verdictOf(sample?.peak_dbfs ?? null, sample?.lufs ?? null)
  const foot = error
    ? { text: error, tone: 'bad' }
    : noAudio
      ? { text: `${t('lvl.tp.needsAudio')} ${audio?.hint ?? ''}`, tone: 'bad' }
      : verdict && face !== 'armed' && face !== 'recording'
        ? { text: verdict.text, tone: verdict.good ? 'ok' : 'warn' }
        : { text: t('lvl.tp.keysHint'), tone: '' }

  // `is-` prefixed: the page owns `.empty` (the no-presets notice)
  return (
    <div className={`xport is-${face}`}>
      <span className="eyebrow xport-lab">{t('msd.riff')}</span>

      <button type="button"
              className={`xport-btn rec${armed || recording || primary ? ' hot' : ''}${armed ? ' pulse' : ''}`}
              disabled={recDisabled} onClick={sampler.foot} aria-label={recLabel}>
        <span className="g">{recording ? '■' : '●'}</span>
        <span className="c">{recLabel}</span>
      </button>
      <button type="button" className={`xport-btn play${playing ? ' hot' : ''}`}
              disabled={playDisabled} onClick={onPlay}
              aria-label={playing ? t('lvl.tp.stop') : t('lvl.tp.play')}>
        <span className="g">{playing ? '■' : '▶'}</span>
        <span className="c">{playing ? t('lvl.tp.stop') : t('lvl.tp.play')}</span>
      </button>

      {/* the tape: every layer mounted, the state picks which shows */}
      <div className={`xport-tape${face === 'running' ? ' dim' : ''}`}>
        <span className={`tape-empty${face === 'empty' ? ' on' : ''}`}>{t('lvl.tp.noRiff')}</span>
        <Wave peaks={peaks} live={recording} on={hasTake || recording} />
        <InputMeter dbfs={sample?.input_dbfs ?? null} thresholdDbfs={sample?.threshold_dbfs ?? -40}
                    on={armed} />
        <span className="playhead" hidden={!playing}
              style={{ animationDuration: `${sample?.duration_s ?? 5}s` }} />
      </div>

      {/* the facts: line one is the take, line two is what is happening now.
          Each line is a stack of alternatives sized by the widest. */}
      <div className="xport-facts">
        <div className="xport-alts">
          <span className={`row${hasTake && !recording ? ' on' : ''}`}>
            <b>{(sample?.duration_s ?? 0).toFixed(1)} s</b>
            <b>{fmt(sample?.peak_dbfs)} dBFS</b>
            <b>{fmt(sample?.lufs)} LUFS</b>
            <span className={`dot${verdict ? (verdict.good ? ' good' : ' warn') : ''}`}
                  title={verdict?.text ?? ''} />
          </span>
          <span className={`row${recording ? ' on' : ''}`}>
            <b>{(sample?.seconds_recorded ?? 0).toFixed(1)} s</b>
            <span>{t('msd.ofMax', { max: (sample?.max_seconds ?? 30).toFixed(0) })}</span>
          </span>
          <span className={`row${armed ? ' on' : ''}`}><b>{t('lvl.tp.playToStart')}</b></span>
        </div>
        <div className="xport-alts">
          <label className={`row${face === 'recorded' || face === 'empty' ? ' on' : ''}`}>
            <span>{t('lvl.tp.source')}</span>
            <select
              className="xport-sel" value={riffs?.loaded ?? ''}
              disabled={loadingRiff !== null || recDisabled}
              aria-label={t('lvl.tp.source')}
              onChange={(e) => {
                // the keyboard must get `space` back: a focused select owns it
                e.target.blur()
                if (e.target.value) void sampler.pick(e.target.value)
              }}
            >
              <option value="" disabled={!riffs?.recorded}>
                {riffs?.recorded ? t('lvl.tp.yourTake') : t('lvl.tp.noTake')}
              </option>
              {(riffs?.riffs ?? []).map((r) => (
                <option key={r.name} value={r.name} title={r.why}>
                  {loadingRiff === r.name ? t('lvl.tp.loading', { name: r.name }) : `${r.name} ${r.seconds.toFixed(1)}s`}
                </option>
              ))}
            </select>
          </label>
          <span className={`row${armed || recording ? ' on' : ''}`}>
            <span>{t('msd.inDbfs', { db: fmt(sample?.input_dbfs) })}</span>
            <span className="warn" style={{ visibility: recording && silenceLeft !== null && silenceLeft > 0 ? 'visible' : 'hidden' }}>
              {t('msd.stopsIn', { s: Math.max(0, silenceLeft ?? 0).toFixed(1) })}
            </span>
          </span>
          <span className={`row${playing ? ' on' : ''}`}>
            <span>{t('lvl.tp.through', { name: presetName ?? '' })}</span>
          </span>
          <span className={`row${face === 'running' ? ' on' : ''}`}>
            <span>{t('lvl.tp.running')}</span>
          </span>
        </div>
      </div>

      {/* one footline for the band */}
      <div className={`xport-foot ${foot.tone}`} title={foot.text}>
        <span className="xport-foot-text">{foot.text}</span>
        <button type="button" className="bt-foot-x" onClick={sampler.dismiss}
                style={{ visibility: error ? 'visible' : 'hidden' }} aria-label={t('lvl.dismiss')}>×</button>
      </div>
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
 * point. This is the one cheap moment to catch that. The thresholds are the
 * ones tests/test_leveling_e2e.py holds the shipped riffs to.
 */
function verdictOf(peak: number | null, lufs: number | null): { good: boolean; text: string } | null {
  if (peak === null) return null
  const quiet = peak < -12
  const thin = lufs !== null && lufs < -30
  if (!quiet && !thin) return { good: true, text: t('msd.verdict.good', { peak: peak.toFixed(1) }) }
  const why = quiet
    ? t('msd.verdict.quiet', { peak: peak.toFixed(1) })
    : t('msd.verdict.thin', { lufs: lufs?.toFixed(1) ?? '' })
  return { good: false, text: `${t('msd.verdict.badPlain')} ${why} ${t('msd.verdict.fix')}` }
}

/** Live input level with the start threshold marked, so "why has it not started" is visible. */
function InputMeter({ dbfs, thresholdDbfs, on }: {
  dbfs: number | null
  thresholdDbfs: number
  on: boolean
}): React.JSX.Element {
  return (
    <div className={`inmeter${on ? ' on' : ''}`} aria-hidden={!on}>
      <div className="inmeter-bar">
        <span className="fill" style={{ width: `${pos(dbfs) * 100}%` }} />
        <span className="thr" style={{ left: `${pos(thresholdDbfs) * 100}%` }} />
      </div>
    </div>
  )
}

/**
 * The take, drawn from the peak envelope the service sends.
 *
 * Not the audio: that is tens of megabytes and this crosses a JSON socket on
 * every poll while recording, so the service downsamples to a few hundred peaks
 * and this draws those.
 */
function Wave({ peaks, live, on }: {
  peaks: number[]
  /** Recording: the take grows to the right edge and the head sits there. */
  live: boolean
  on: boolean
}): React.JSX.Element {
  const n = Math.max(peaks.length, 1)
  const w = 100 / n
  const norm = Math.max(0.05, ...peaks)          // scale to the take, not to full scale
  return (
    <div className={`wave${on ? ' on' : ''}`} aria-hidden={!on}>
      <svg viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden>
        {peaks.map((p, i) => {
          const h = Math.max(0.6, (p / norm) * 38)
          return (
            <rect key={i} x={i * w} y={(40 - h) / 2}
                  width={Math.max(w * 0.62, 0.12)} height={h} rx={0.08} />
          )
        })}
      </svg>
      <span className="head" style={{ visibility: live ? 'visible' : 'hidden' }} />
      <span className="wave-empty" style={{ visibility: live && peaks.length === 0 ? 'visible' : 'hidden' }}>
        {t('lvl.tp.waiting')}
      </span>
    </div>
  )
}
