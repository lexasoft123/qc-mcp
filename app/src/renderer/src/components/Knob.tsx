import { useEffect, useRef } from 'react'

const SWEEP = 135          // degrees either side of top
const PER_PIXEL = 0.35     // dB per pixel of drag
const FINE = 0.12          // …with Shift held

interface KnobProps {
  db: number
  min: number
  max: number
  /** Called continuously while dragging; the caller decides how to throttle. */
  onChange: (db: number) => void
  /** Called once the gesture ends, for the write that should actually persist. */
  onCommit?: (db: number) => void
  disabled?: boolean
  size?: number
  label?: string
}

const clamp = (v: number, lo: number, hi: number): number => Math.max(lo, Math.min(hi, v))

function arc(cx: number, cy: number, r: number, a0: number, a1: number): string {
  const p = (deg: number): [number, number] => {
    const rad = ((deg - 90) * Math.PI) / 180
    return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)]
  }
  const [x0, y0] = p(a0)
  const [x1, y1] = p(a1)
  return `M ${x0} ${y0} A ${r} ${r} 0 ${Math.abs(a1 - a0) > 180 ? 1 : 0} 1 ${x1} ${y1}`
}

/**
 * The level knob: drag vertically, wheel, or arrow keys; Shift for fine.
 *
 * Both handlers for a drag are created **inside** the pointerdown, so the
 * listeners registered for the gesture stay valid for its whole life. Hoisting
 * them into `useCallback`s does not work: `onChange` is a fresh closure on every
 * render, so the callbacks changed identity as soon as the first move updated
 * state, and the cleanup that tracked them tore the drag down one pixel in.
 * A ref carries the latest props into those closures instead.
 */
export function Knob({
  db, min, max, onChange, onCommit, disabled, size = 88, label
}: KnobProps): React.JSX.Element {
  const live = useRef(db)
  const dragging = useRef(false)
  const ref = useRef<HTMLDivElement>(null)
  const latest = useRef({ min, max, onChange, onCommit })
  latest.current = { min, max, onChange, onCommit }

  // follow the outside world, but never while the user's hand is on the control
  useEffect(() => { if (!dragging.current) live.current = db }, [db])

  const span = max - min
  const frac = clamp((db - min) / span, 0, 1)
  const angle = -SWEEP + frac * SWEEP * 2
  const r = size / 2 - 9
  const cx = size / 2
  const cy = size / 2
  const unity = unityAngle(min, max)

  const down = (e: React.PointerEvent): void => {
    if (disabled) return
    e.preventDefault()
    ref.current?.focus()
    const startY = e.clientY
    const startDb = live.current
    dragging.current = true

    const move = (ev: PointerEvent): void => {
      const c = latest.current
      const step = ev.shiftKey ? FINE : PER_PIXEL
      const next = clamp(startDb + (startY - ev.clientY) * step, c.min, c.max)
      if (next === live.current) return
      live.current = next
      c.onChange(next)
    }
    const up = (): void => {
      dragging.current = false
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      window.removeEventListener('pointercancel', up)
      latest.current.onCommit?.(live.current)
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
    window.addEventListener('pointercancel', up)
  }

  const nudge = (delta: number): void => {
    const c = latest.current
    const next = clamp(live.current + delta, c.min, c.max)
    live.current = next
    c.onChange(next)
    c.onCommit?.(next)
  }

  const keys = (e: React.KeyboardEvent): void => {
    if (disabled) return
    const fine = e.shiftKey ? 0.1 : 0.5
    if (e.key === 'ArrowUp' || e.key === 'ArrowRight') { e.preventDefault(); nudge(fine) }
    else if (e.key === 'ArrowDown' || e.key === 'ArrowLeft') { e.preventDefault(); nudge(-fine) }
    else if (e.key === 'Home') { e.preventDefault(); nudge(-live.current) }
  }

  return (
    <div
      ref={ref}
      className={disabled ? 'knob off' : 'knob'}
      style={{ width: size, height: size }}
      tabIndex={disabled ? -1 : 0}
      role="slider"
      aria-label={label ?? 'Level'}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={Math.round(db * 10) / 10}
      aria-valuetext={`${db >= 0 ? '+' : ''}${db.toFixed(1)} decibels`}
      aria-disabled={disabled}
      onPointerDown={down}
      onKeyDown={keys}
      onDoubleClick={() => { if (!disabled) nudge(-live.current) }}
      onWheel={(e) => { if (!disabled) nudge(e.deltaY < 0 ? 0.5 : -0.5) }}
    >
      <svg width={size} height={size} aria-hidden>
        <path d={arc(cx, cy, r, -SWEEP, SWEEP)} className="knob-track" />
        {/* unity is the reference the eye needs when matching presets */}
        <path
          d={arc(cx, cy, r, Math.min(angle, unity), Math.max(angle, unity))}
          className="knob-fill"
        />
        <line
          x1={cx} y1={cy - r + 12} x2={cx} y2={cy - r + 2}
          className="knob-unity"
          transform={`rotate(${unity} ${cx} ${cy})`}
        />
        <circle cx={cx} cy={cy} r={r - 8} className="knob-cap" />
        <line
          x1={cx} y1={cy - 3} x2={cx} y2={cy - r + 7}
          className="knob-pointer"
          transform={`rotate(${angle} ${cx} ${cy})`}
        />
      </svg>
    </div>
  )
}

/** Where 0 dB sits on the dial — the mark everything is levelled against. */
function unityAngle(min: number, max: number): number {
  return -SWEEP + clamp((0 - min) / (max - min), 0, 1) * SWEEP * 2
}
