import { useEffect, useState } from 'react'
import { Button, SegmentedControl } from '@singz/ui'
import type { LogDirection, LogLine, Snapshot } from '@shared/types'
import { isMac } from '../derive.js'
import { say } from '../store.js'
import { T, t } from '../i18n.js'

type Filter = 'all' | LogDirection

const filters = (): { value: Filter; label: string }[] => [
  { value: 'all', label: t('logs.all') },
  { value: 'tx', label: t('logs.sent') },
  { value: 'rx', label: t('logs.received') },
  { value: 'err', label: t('logs.errors') }
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
  const mac = isMac(snap)

  return (
    <div className="view">
      <div className="log-bar">
        <SegmentedControl
          options={filters()}
          value={filter}
          onChange={setFilter}
          disabled={!snap.prefs.verbose}
          aria-label={t('aria.logFilter')}
        />
        <span className="grow" />
        <span className="fine mono">
          {snap.prefs.verbose ? `${snap.paths.show.logPath} · ${mb(size)}` : t('logs.off')}
        </span>
        <Button
          size="sm"
          disabled={!snap.prefs.verbose}
          onClick={() => { void window.patchbay.reveal(snap.paths.logPath); say(mac ? t('logs.revealedMac') : t('logs.revealedWin')) }}
        >
          {mac ? t('logs.revealMac') : t('logs.revealWin')}
        </Button>
      </div>

      <div className="logs">
        {!snap.prefs.verbose ? (
          <div className="log-empty"><T k="logs.offBody" /></div>
        ) : rows.length === 0 ? (
          <div className="log-empty">{t('logs.empty')}</div>
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
