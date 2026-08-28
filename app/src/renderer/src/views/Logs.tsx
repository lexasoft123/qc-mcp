import { useEffect, useState } from 'react'
import { Button, SegmentedControl } from '@singz/ui'
import type { LogDirection, LogLine, Snapshot } from '@shared/types'
import { isMac } from '../derive.js'
import { say } from '../store.js'

type Filter = 'all' | LogDirection

const FILTERS: { value: Filter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'tx', label: 'Sent' },
  { value: 'rx', label: 'Received' },
  { value: 'err', label: 'Errors' }
]

const mb = (bytes: number): string => `${(bytes / 1024 / 1024).toFixed(1)} MB`

export function Logs({ snap }: { snap: Snapshot }): React.JSX.Element {
  const [filter, setFilter] = useState<Filter>('all')
  const [lines, setLines] = useState<LogLine[]>([])
  const [size, setSize] = useState(0)

  useEffect(() => {
    if (!snap.prefs.verbose) return
    let alive = true
    const pull = async (): Promise<void> => {
      const [l, s] = await Promise.all([window.patchbay.readLog(400), window.patchbay.logSize()])
      if (alive) { setLines(l); setSize(s) }
    }
    void pull()
    const t = setInterval(() => void pull(), 1500)
    return () => { alive = false; clearInterval(t) }
  }, [snap.prefs.verbose])

  const rows = lines.filter((l) => filter === 'all' || l.dir === filter)
  const glyph = (d: LogDirection): string => (d === 'tx' ? '→' : d === 'rx' ? '←' : d === 'err' ? 'err' : 'sys')

  return (
    <div className="view">
      <div className="log-bar">
        <SegmentedControl
          options={FILTERS}
          value={filter}
          onChange={setFilter}
          disabled={!snap.prefs.verbose}
          aria-label="Log filter"
        />
        <span className="grow" />
        <span className="fine mono">
          {snap.prefs.verbose ? `${snap.paths.show.logPath} · ${mb(size)}` : 'frame log is off'}
        </span>
        <Button
          size="sm"
          disabled={!snap.prefs.verbose}
          onClick={() => { void window.patchbay.reveal(snap.paths.logPath); say(isMac(snap) ? 'Revealed in Finder' : 'Opened in Explorer') }}
        >
          {isMac(snap) ? 'Reveal in Finder' : 'Show in Explorer'}
        </Button>
      </div>

      <div className="logs">
        {!snap.prefs.verbose ? (
          <div className="log-empty">
            The frame log is off, so there is nothing to show. Turn on <b>Write the frame log</b> in
            Preferences and reconnect.
          </div>
        ) : rows.length === 0 ? (
          <div className="log-empty">
            Nothing logged yet. Frames appear here as soon as something talks to the device.
          </div>
        ) : (
          rows.map((l, i) => (
            <div className={`log-line ${l.dir}`} key={i}>
              <span className="t">{l.t}</span>
              <span className="d">{glyph(l.dir)}</span>
              <span>{l.text}</span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
