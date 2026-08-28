import { useEffect, useRef, useState } from 'react'
import { Badge, Button, Modal, ModalActions, SegmentedControl, StatusDot } from '@singz/ui'
import type { Mode, Snapshot } from '@shared/types'
import { clash, isMac, sessionFact, sharedWriters, uptime } from '../derive.js'
import { act, say, useProgress } from '../store.js'
import { Facts, Strip, Tag } from '../components/Bits.js'
import { Sparkline } from '../components/Sparkline.js'
import { Clients } from '../modals/Clients.js'

const MODES: { value: Mode; label: string }[] = [
  { value: 'auto', label: 'Auto' },
  { value: 'bridge', label: 'Bridge' },
  { value: 'direct', label: 'Direct' }
]

/** Keep a minute of the measured rate for the meter. */
function useSeries(rate: number, live: boolean): number[] {
  const [series, setSeries] = useState<number[]>(() => new Array(60).fill(0))
  const last = useRef(rate)
  last.current = rate
  useEffect(() => {
    const t = setInterval(() => setSeries((s) => [...s.slice(1), live ? last.current : 0]), 1000)
    return () => clearInterval(t)
  }, [live])
  return series
}

export function Console({ snap }: { snap: Snapshot }): React.JSX.Element {
  const [sheet, setSheet] = useState<'clients' | 'rebuild' | null>(null)
  const [rebuilding, setRebuilding] = useState(false)
  const progress = useProgress()

  const mac = isMac(snap)
  const live = snap.daemon.state === 'running'
  const online = snap.device.present && (live || snap.cortex.running)
  const series = useSeries(snap.daemon.reportsPerSecond, live || snap.cortex.running)

  const rebuild = async (): Promise<void> => {
    setRebuilding(true)
    try {
      await act(() => window.patchbay.cortexRebuild())
      say('Rebuilt the instrumented copy')
    } finally {
      setRebuilding(false)
      setSheet(null)
    }
  }

  return (
    <div className="view">
      <section className={online ? 'device' : 'device dark'}>
        <div className="device-id">
          <div className={online ? 'qc-mark' : 'qc-mark off'}>QC</div>
          <div>
            <h1>Quad Cortex</h1>
            <div className="sub">
              <span className="fine mono">
                {snap.device.present
                  ? `USB · ${snap.device.serial ?? 'serial unavailable'}`
                  : 'no Quad Cortex on USB — check the cable'}
              </span>
              <Badge className={online ? 'live' : 'off'}>
                {!snap.device.present ? 'not found' : online ? 'connected' : 'no session'}
              </Badge>
            </div>
          </div>
        </div>
        <div className="device-now" style={{ opacity: online ? 1 : 0.4 }}>
          <div className="fine">{sessionFact(snap)}</div>
        </div>
      </section>

      <div className="rack">
        {/* ── MCP registration ── */}
        <article className="mod">
          <div className="mod-head">
            <StatusDot tone={snap.clients.some((c) => c.installed) ? 'ok' : 'idle'} />
            <h2>MCP registration</h2>
            <Badge className={snap.clients.some((c) => c.installed) ? '' : 'off'}>
              {snap.clients.filter((c) => c.installed).length || 'no'} clients
            </Badge>
            <span className="grow" />
            <div className="acts">
              <Button size="sm" onClick={() => setSheet('clients')}>Manage clients…</Button>
            </div>
          </div>
          <div className="mod-body">
            <div className="clients">
              {snap.clients.filter((c) => c.found || c.installed).map((c) => (
                <div className="client-row" key={c.id}>
                  <StatusDot tone={c.installed ? 'ok' : 'idle'} />
                  <span className="name">{c.name}</span>
                  <span className="path mono">{c.path}</span>
                  <span className={c.installed ? 'state on' : 'state'}>
                    {c.installed ? 'installed' : 'not installed'}
                  </span>
                </div>
              ))}
            </div>
            <p className="fine">
              Every client points at the same binary — <code>{snap.paths.bin}</code>. Move the repo
              and Patchbay rewrites all of them.
            </p>
          </div>
        </article>

        {/* ── the daemon ── */}
        <article className={live ? 'mod live' : 'mod'}>
          <div className="mod-head">
            <StatusDot tone={live ? 'ok' : snap.daemon.state === 'starting' ? 'warn' : 'idle'} />
            <h2>qc-mcp daemon</h2>
            <Badge className={live ? 'live' : snap.daemon.state === 'starting' ? 'attn' : 'off'}>
              {snap.daemon.state}
            </Badge>
            <span className="grow" />
            <div className="acts">
              <SegmentedControl
                options={MODES}
                value={snap.prefs.mode}
                aria-label="Connection mode"
                onChange={(m) => {
                  void act(() => window.patchbay.setMode(m))
                  if (m === 'direct' && snap.cortex.running) {
                    say('Direct mode needs the device to itself — quit Cortex Control.', true)
                  }
                }}
              />
              <Button
                variant={live ? 'danger' : 'primary'}
                size={live ? 'sm' : 'md'}
                disabled={snap.daemon.state === 'starting'}
                onClick={() => void act(() => (live ? window.patchbay.daemonStop() : window.patchbay.daemonStart()))}
              >
                {live ? 'Stop' : snap.daemon.state === 'starting' ? 'Starting…' : 'Start daemon'}
              </Button>
            </div>
          </div>
          <div className="mod-body">
            {clash(snap) && (
              <Strip bad>
                <span className="grow">
                  Direct mode needs the device to itself. Cortex Control is holding it — quit the
                  app, or switch back to Bridge.
                </span>
                <Button variant="danger" size="sm" onClick={() => void act(() => window.patchbay.cortexQuit())}>
                  Quit app
                </Button>
              </Strip>
            )}
            {sharedWriters(snap) && (
              <Strip>
                <span className="grow">
                  Cortex Control and the daemon are independent writers on one handle. Reads and
                  single-value edits are atomic, so this is safe for ordinary use — switch to{' '}
                  <b>Direct</b> for write-heavy work.
                </span>
              </Strip>
            )}
            {!snap.daemon.supported && snap.daemon.error && (
              <Strip bad>
                <span className="grow">
                  This qc-mcp build has no daemon entry point yet. Patchbay expects{' '}
                  <code>qc-mcp --daemon --socket …</code>. {snap.daemon.error}
                </span>
              </Strip>
            )}
            <div className="meter" style={{ opacity: live || snap.cortex.running ? 1 : 0.32 }}>
              <div className="box"><Sparkline series={series} live={live || snap.cortex.running} /></div>
              <div className="read">
                <div className="n">{live || snap.cortex.running ? snap.daemon.reportsPerSecond : '—'}</div>
                <div className="u">reports/s</div>
              </div>
            </div>
            <Facts
              rows={[
                ['status', live ? `pid ${snap.daemon.pid} · up ${uptime(snap.daemon.startedAt)}` : 'not running', live ? '' : 'off'],
                [mac ? 'socket' : 'named pipe', snap.paths.socket, live ? 'muted' : 'off'],
                ['session', live ? sessionFact(snap) : '—', live ? '' : 'off'],
                ['attached clients', live ? (snap.daemon.clients.join(', ') || 'none') : '—', live ? 'muted' : 'off']
              ]}
            />
          </div>
        </article>

        {/* ── Cortex Control ── */}
        <article className={snap.cortex.running ? 'mod live' : 'mod'}>
          <div className="mod-head">
            <StatusDot tone={snap.cortex.running ? 'ok' : 'idle'} />
            <h2>
              Cortex Control{' '}
              {mac && <Badge className="attn">instrumented</Badge>}
            </h2>
            <span className="grow" />
            <div className="acts">
              {mac && (
                <Button size="sm" disabled={!snap.cortex.installed} onClick={() => setSheet('rebuild')}>
                  Rebuild…
                </Button>
              )}
              <Button
                size="sm"
                disabled={!snap.cortex.installed}
                onClick={() => void act(() => (snap.cortex.running ? window.patchbay.cortexQuit() : window.patchbay.cortexLaunch()))}
              >
                {snap.cortex.running ? 'Quit app' : 'Launch'}
              </Button>
            </div>
          </div>
          <div className="mod-body">
            {mac && !snap.cortex.instrumented?.built && (
              <Strip bad>
                <span className="grow">No instrumented copy yet. Run setup to build one.</span>
              </Strip>
            )}
            {mac && snap.cortex.needsRebuild && (
              <Strip>
                <span className="grow">
                  Cortex Control updated to <b>{snap.cortex.version}</b>. The instrumented copy is{' '}
                  {snap.cortex.instrumented?.version} — rebuild to stay in sync.
                </span>
              </Strip>
            )}
            <div className="tags">
              {mac ? (
                <>
                  <Tag label="hardened runtime" value={snap.cortex.instrumented?.hardenedRuntimeOff ? 'off' : 'on'} on={Boolean(snap.cortex.instrumented?.hardenedRuntimeOff)} />
                  <Tag label="library validation" value={snap.cortex.instrumented?.libraryValidationOff ? 'disabled' : 'enforced'} on={Boolean(snap.cortex.instrumented?.libraryValidationOff)} />
                  <Tag label="app" value={snap.cortex.running ? 'running' : 'closed'} on={snap.cortex.running} />
                </>
              ) : (
                <>
                  <Tag label="HID handle" value="non-exclusive" on />
                  <Tag label="input reports" value="copied to both" on />
                  <Tag label="writes" value="independent" on />
                  <Tag label="app" value={snap.cortex.running ? 'running' : 'closed'} on={snap.cortex.running} />
                </>
              )}
            </div>
            <Facts
              rows={mac
                ? [
                    ['source app', `${snap.cortex.path}${snap.cortex.version ? ` · ${snap.cortex.version}` : ''}`, 'muted'],
                    ['instrumented copy', snap.cortex.instrumented?.built ? `interceptor/CortexControl-instrumented.app · ${snap.cortex.instrumented.version}` : 'not built', snap.cortex.instrumented?.built ? 'muted' : 'off'],
                    ['process', snap.cortex.running ? `pid ${snap.cortex.pid}` : 'not running', snap.cortex.running ? '' : 'off']
                  ]
                : [
                    ['installed', `${snap.cortex.path}${snap.cortex.version ? ` · ${snap.cortex.version}` : ''}`, 'muted'],
                    ['needed for', 'nothing — the daemon reaches the device on its own', 'muted'],
                    ['process', snap.cortex.running ? `pid ${snap.cortex.pid}` : 'not running', snap.cortex.running ? '' : 'off']
                  ]}
            />
          </div>
        </article>
      </div>

      {sheet === 'clients' && <Clients snap={snap} onClose={() => setSheet(null)} />}

      {sheet === 'rebuild' && (
        <Modal onClose={() => setSheet(null)} busy={rebuilding} aria-label="Rebuild the instrumented copy">
          <h2>Rebuild the instrumented copy</h2>
          <p className="fine">
            This copies Cortex Control {snap.cortex.version} again, re-signs it ad-hoc without the
            hardened runtime, and verifies that injection is permitted. The running app quits first.
          </p>
          {rebuilding && <div className="build-list"><div className="build-row"><span className="mk">›</span>{progress?.label ?? 'Working…'}</div></div>}
          <ModalActions>
            <Button variant="primary" disabled={rebuilding} onClick={() => void rebuild()}>
              {rebuilding ? 'Rebuilding…' : 'Rebuild and relaunch'}
            </Button>
            <Button disabled={rebuilding} onClick={() => setSheet(null)}>Not now</Button>
          </ModalActions>
        </Modal>
      )}
    </div>
  )
}
