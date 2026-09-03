import { useEffect, useRef } from 'react'

/**
 * Reports per second over the last minute. The samples are measured in the
 * main process from the interposer's own millisecond stamps, so this is the
 * real traffic rate, not an animation.
 */
export function Sparkline({ series, live }: { series: number[]; live: boolean }): React.JSX.Element {
  const ref = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const cvs = ref.current
    const ctx = cvs?.getContext('2d')
    if (!cvs || !ctx) return
    const w = cvs.width
    const h = cvs.height
    const n = series.length
    ctx.clearRect(0, 0, w, h)
    if (n < 2) return

    const max = Math.max(60, ...series)
    const pt = (i: number): [number, number] => [
      (i / (n - 1)) * (w - 6) + 3,
      h - 5 - (series[i] / max) * (h - 12)
    ]

    ctx.strokeStyle = 'rgba(255,240,214,0.06)'
    ctx.lineWidth = 1
    for (const f of [0.34, 0.67]) {
      ctx.beginPath()
      ctx.moveTo(3, h * f)
      ctx.lineTo(w - 3, h * f)
      ctx.stroke()
    }

    ctx.beginPath()
    ctx.moveTo(3, h - 4)
    for (let i = 0; i < n; i++) ctx.lineTo(...pt(i))
    ctx.lineTo(w - 3, h - 4)
    ctx.closePath()
    const g = ctx.createLinearGradient(0, 0, 0, h)
    g.addColorStop(0, live ? 'rgba(255,160,40,0.26)' : 'rgba(155,145,126,0.10)')
    g.addColorStop(1, 'rgba(255,160,40,0)')
    ctx.fillStyle = g
    ctx.fill()

    ctx.beginPath()
    for (let i = 0; i < n; i++) {
      const p = pt(i)
      if (i) ctx.lineTo(...p)
      else ctx.moveTo(...p)
    }
    ctx.strokeStyle = live ? '#ffa028' : 'rgba(155,145,126,0.5)'
    ctx.lineWidth = 2
    ctx.lineJoin = 'round'
    ctx.stroke()

    const last = pt(n - 1)
    ctx.beginPath()
    ctx.arc(last[0], last[1], 3, 0, Math.PI * 2)
    ctx.fillStyle = live ? '#ffd489' : 'rgba(155,145,126,0.6)'
    ctx.fill()
  }, [series, live])

  return <canvas ref={ref} width={600} height={68} aria-hidden />
}
