/**
 * The reference-riff recorder, as a looper pedal.
 *
 * A pedal because that is the gesture: arm it, play, stop playing. Nothing here
 * is on a clock — the first note starts the take and the silence after ends it,
 * so the control has three faces rather than a start and a stop button.
 */
import { t } from '../i18n.js'

const R = 72
const CIRC = 2 * Math.PI * R

/** Input level as a fraction of the meter, which runs -60..0 dBFS. */
const pos = (dbfs: number | null): number =>
  dbfs === null ? 0 : Math.max(0, Math.min(1, (dbfs + 60) / 60))

export function Pedal({
  state, seconds, maxSeconds, silenceLeft, onArm, onStop
}: {
  state: 'idle' | 'armed' | 'recording' | 'done'
  seconds: number
  maxSeconds: number
  /** Seconds of quiet left before the take ends itself, or null when playing. */
  silenceLeft: number | null
  onArm: () => void
  onStop: () => void
}): React.JSX.Element {
  const recording = state === 'recording'
  const armed = state === 'armed'

  // While playing, the ring tracks the take against its cap. While the silence
  // timer is counting, it becomes that countdown instead — the more urgent of
  // the two, and the one that explains why the take is about to end.
  const frac = silenceLeft !== null && recording
    ? Math.max(0, silenceLeft) / 1.5
    : Math.min(1, seconds / Math.max(maxSeconds, 1))

  return (
    <div className="pedal">
      <button
        type="button"
        className={`pedalbtn ${state}`}
        onClick={recording || armed ? onStop : onArm}
        aria-label={recording ? t('pedal.stop') : armed ? t('pedal.cancel') : t('pedal.arm')}
      >
        {(recording || armed) && (
          <svg className="ring" viewBox="0 0 160 160" aria-hidden>
            <circle cx="80" cy="80" r={R} strokeWidth="3" className="ring-track" />
            <circle
              cx="80" cy="80" r={R} strokeWidth="3" strokeLinecap="round"
              className="ring-live" transform="rotate(-90 80 80)"
              strokeDasharray={CIRC} strokeDashoffset={CIRC * (1 - frac)}
            />
          </svg>
        )}
        <span className="cap">
          {recording ? (
            <>
              <span className="glyph" aria-hidden />
              <span className="sub">stop</span>
            </>
          ) : armed ? (
            <>
              <span className="lab">Listening</span>
              <span className="sub">play to start</span>
            </>
          ) : (
            <>
              <span className="lab">Arm</span>
              <span className="sub">then play</span>
            </>
          )}
        </span>
      </button>
      <div className="pedal-cap">
        <span className="kbd">space</span>{' '}
        {recording ? 'to stop' : armed ? 'to cancel' : 'to arm'}
      </div>
    </div>
  )
}

/** Live input level with the start threshold marked, so "why has it not started" is visible. */
export function InputMeter({
  dbfs, thresholdDbfs
}: {
  dbfs: number | null
  thresholdDbfs: number
}): React.JSX.Element {
  return (
    <div className="inmeter">
      <div className="inmeter-bar">
        <span className="fill" style={{ width: `${pos(dbfs) * 100}%` }} />
        <span className="thr" style={{ left: `${pos(thresholdDbfs) * 100}%` }} />
      </div>
      <div className="inmeter-scale">
        <span>&minus;60</span><span>&minus;40</span><span>&minus;20</span><span>0</span>
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
export function Wave({
  peaks, live, height = 96
}: {
  peaks: number[]
  /** Recording: the take grows to the right edge and the head sits there. */
  live?: boolean
  height?: number
}): React.JSX.Element {
  const n = Math.max(peaks.length, 1)
  const w = 100 / n
  const norm = Math.max(0.05, ...peaks)          // scale to the take, not to full scale
  return (
    <div className="wave" style={{ height }}>
      <svg viewBox="0 0 100 40" preserveAspectRatio="none" aria-hidden>
        {peaks.map((p, i) => {
          const h = Math.max(0.6, (p / norm) * 38)
          return (
            <rect
              key={i} x={i * w} y={(40 - h) / 2}
              width={Math.max(w * 0.62, 0.12)} height={h} rx={0.08}
            />
          )
        })}
      </svg>
      {live && <span className="head" />}
      {peaks.length === 0 && <span className="wave-empty">waiting for the first note</span>}
    </div>
  )
}
