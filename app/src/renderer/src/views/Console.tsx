import { useEffect, useRef, useState } from 'react'
import { Badge, Button, Modal, ModalActions, SegmentedControl, StatusDot } from '@singz/ui'
import type { Mode, Snapshot } from '@shared/types'
import { clash, isMac, sessionFact, sharedWriters, staleClients, uptime } from '../derive.js'
import { act, say, useProgress } from '../store.js'
import { T, t, tn } from '../i18n.js'
import { Facts, Strip, Tag } from '../components/Bits.js'
import { Sparkline } from '../components/Sparkline.js'
import { Clients } from '../modals/Clients.js'

export const modes = (): { value: Mode; label: string }[] => [
  { value: 'auto', label: t('mode.auto') },
  { value: 'bridge', label: t('mode.bridge') },
  { value: 'direct', label: t('mode.direct') }
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
  const installed = snap.clients.filter((c) => c.installed).length
  const stale = staleClients(snap)

  const rebuild = async (): Promise<void> => {
    setRebuilding(true)
    try {
      await act(() => window.patchbay.cortexRebuild())
      say(t('rebuild.toast'))
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
            <h1>{t('console.device')}</h1>
            <div className="sub">
              <span className="fine mono">
                {snap.device.present
                  ? t('console.usb', { serial: snap.device.serial ?? t('console.serialNA') })
                  : t('console.noDevice')}
              </span>
              <Badge className={online ? 'live' : 'off'}>
                {!snap.device.present ? t('console.notFound') : online ? t('console.connected') : t('console.noSession')}
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
            <StatusDot tone={installed ? 'ok' : 'idle'} />
            <h2>{t('console.reg')}</h2>
            <Badge className={installed ? '' : 'off'}>
              {installed ? tn('clients', installed) : t('console.regNone')}
            </Badge>
            <span className="grow" />
            <div className="acts">
              <Button size="sm" onClick={() => setSheet('clients')}>{t('console.manage')}</Button>
            </div>
          </div>
          <div className="mod-body">
            {stale.length > 0 && (
              <Strip>
                <span className="grow">
                  {tn('console.stale', stale.length, { names: stale.map((c) => c.name).join(', ') })}
                </span>
                <Button size="sm" onClick={() => setSheet('clients')}>{t('console.repoint')}</Button>
              </Strip>
            )}
            <div className="clients">
              {snap.clients.filter((c) => c.found || c.installed).map((c) => (
                <div className="client-row" key={c.id}>
                  <StatusDot tone={c.installed ? (c.stale ? 'warn' : 'ok') : 'idle'} />
                  <span className="name">{c.name}</span>
                  <span className="path mono">{c.path}</span>
                  <span className={c.installed && !c.stale ? 'state on' : 'state'}>
                    {!c.installed ? t('console.notInstalled') : c.stale ? t('console.opensItself') : t('console.installed')}
                  </span>
                </div>
              ))}
            </div>
            <p className="fine"><T k="console.samePath" vars={{ bin: snap.paths.show.bin }} /></p>
          </div>
        </article>

        {/* ── the daemon ── */}
        <article className={live ? 'mod live' : 'mod'}>
          <div className="mod-head">
            <StatusDot tone={live ? 'ok' : snap.daemon.state === 'starting' ? 'warn' : 'idle'} />
            <h2>{t('console.daemon')}</h2>
            <Badge className={live ? 'live' : snap.daemon.state === 'starting' ? 'attn' : 'off'}>
              {t(`state.${snap.daemon.state}`)}
            </Badge>
            <span className="grow" />
            <div className="acts">
              <SegmentedControl
                options={modes()}
                value={snap.prefs.mode}
                aria-label={t('aria.mode')}
                onChange={(m) => {
                  void act(() => window.patchbay.setMode(m))
                  if (m === 'direct' && snap.cortex.running) say(t('console.directNeedsApp'), true)
                }}
              />
              <Button
                variant={live ? 'danger' : 'primary'}
                size={live ? 'sm' : 'md'}
                disabled={snap.daemon.state === 'starting'}
                onClick={() => void act(() => (live ? window.patchbay.daemonStop() : window.patchbay.daemonStart()))}
              >
                {live ? t('console.stop') : snap.daemon.state === 'starting' ? t('console.starting') : t('console.start')}
              </Button>
            </div>
          </div>
          <div className="mod-body">
            {clash(snap) && (
              <Strip bad>
                <span className="grow">{t('console.clash')}</span>
                <Button variant="danger" size="sm" onClick={() => void act(() => window.patchbay.cortexQuit())}>
                  {t('console.quitApp')}
                </Button>
              </Strip>
            )}
            {sharedWriters(snap) && (
              <Strip>
                <span className="grow"><T k="console.sharedWriters" /></span>
              </Strip>
            )}
            {!snap.daemon.supported && snap.daemon.error && (
              <Strip bad>
                <span className="grow"><T k="console.noDaemon" vars={{ error: snap.daemon.error }} /></span>
              </Strip>
            )}
            <div className="meter" style={{ opacity: live || snap.cortex.running ? 1 : 0.32 }}>
              <div className="box"><Sparkline series={series} live={live || snap.cortex.running} /></div>
              <div className="read">
                <div className="n">{live || snap.cortex.running ? snap.daemon.reportsPerSecond : '—'}</div>
                <div className="u">{t('console.rps')}</div>
              </div>
            </div>
            <Facts
              rows={[
                [t('fact.status'), live ? t('fact.pidUp', { pid: snap.daemon.pid ?? '', uptime: uptime(snap.daemon.startedAt) }) : t('fact.notRunning'), live ? '' : 'off'],
                [mac ? t('fact.socket') : t('fact.pipe'), snap.paths.show.socket, live ? 'muted' : 'off'],
                [t('fact.session'), live ? sessionFact(snap) : '—', live ? '' : 'off'],
                [t('fact.clients'), live ? (snap.daemon.clients.join(', ') || t('fact.none')) : '—', live ? 'muted' : 'off']
              ]}
            />
          </div>
        </article>

        {/* ── Cortex Control ── */}
        <article className={snap.cortex.running ? 'mod live' : 'mod'}>
          <div className="mod-head">
            <StatusDot tone={snap.cortex.running ? 'ok' : 'idle'} />
            <h2>
              {t('console.app')}{' '}
              {mac && <Badge className="attn">{t('console.instrumented')}</Badge>}
            </h2>
            <span className="grow" />
            <div className="acts">
              {mac && (
                <Button size="sm" disabled={!snap.cortex.installed} onClick={() => setSheet('rebuild')}>
                  {t('console.rebuild')}
                </Button>
              )}
              <Button
                size="sm"
                disabled={!snap.cortex.installed}
                onClick={() => void act(() => (snap.cortex.running ? window.patchbay.cortexQuit() : window.patchbay.cortexLaunch()))}
              >
                {snap.cortex.running ? t('console.quitApp') : t('console.launch')}
              </Button>
            </div>
          </div>
          <div className="mod-body">
            {mac && !snap.cortex.instrumented?.built && (
              <Strip bad>
                <span className="grow">{t('console.noCopy')}</span>
              </Strip>
            )}
            {mac && snap.cortex.needsRebuild && (
              <Strip>
                <span className="grow">
                  <T k="console.appUpdated" vars={{ version: snap.cortex.version ?? '', old: snap.cortex.instrumented?.version ?? '' }} />
                </span>
              </Strip>
            )}
            <div className="tags">
              {mac ? (
                <>
                  <Tag label={t('tag.hardened')} value={snap.cortex.instrumented?.hardenedRuntimeOff ? t('tag.off') : t('tag.on')} on={Boolean(snap.cortex.instrumented?.hardenedRuntimeOff)} />
                  <Tag label={t('tag.libval')} value={snap.cortex.instrumented?.libraryValidationOff ? t('tag.disabled') : t('tag.enforced')} on={Boolean(snap.cortex.instrumented?.libraryValidationOff)} />
                  <Tag label={t('tag.app')} value={snap.cortex.running ? t('tag.running') : t('tag.closed')} on={snap.cortex.running} />
                </>
              ) : (
                <>
                  <Tag label={t('tag.hid')} value={t('tag.nonExclusive')} on />
                  <Tag label={t('tag.inputReports')} value={t('tag.copiedBoth')} on />
                  <Tag label={t('tag.writes')} value={t('tag.independent')} on />
                  <Tag label={t('tag.app')} value={snap.cortex.running ? t('tag.running') : t('tag.closed')} on={snap.cortex.running} />
                </>
              )}
            </div>
            <Facts
              rows={mac
                ? [
                    [t('fact.sourceApp'), `${snap.cortex.path}${snap.cortex.version ? ` · ${snap.cortex.version}` : ''}`, 'muted'],
                    [t('fact.copy'), snap.cortex.instrumented?.built ? `interceptor/CortexControl-instrumented.app · ${snap.cortex.instrumented.version}` : t('fact.notBuilt'), snap.cortex.instrumented?.built ? 'muted' : 'off'],
                    [t('fact.process'), snap.cortex.running ? t('fact.pid', { pid: snap.cortex.pid ?? '' }) : t('fact.notRunning'), snap.cortex.running ? '' : 'off']
                  ]
                : [
                    [t('fact.installed'), `${snap.cortex.path}${snap.cortex.version ? ` · ${snap.cortex.version}` : ''}`, 'muted'],
                    [t('fact.neededFor'), t('fact.nothing'), 'muted'],
                    [t('fact.process'), snap.cortex.running ? t('fact.pid', { pid: snap.cortex.pid ?? '' }) : t('fact.notRunning'), snap.cortex.running ? '' : 'off']
                  ]}
            />
          </div>
        </article>
      </div>

      {sheet === 'clients' && <Clients snap={snap} onClose={() => setSheet(null)} />}

      {sheet === 'rebuild' && (
        <Modal onClose={() => setSheet(null)} busy={rebuilding} aria-label={t('rebuild.title')}>
          <h2>{t('rebuild.title')}</h2>
          <p className="fine">{t('rebuild.body', { version: snap.cortex.version ?? '' })}</p>
          {rebuilding && <div className="build-list"><div className="build-row"><span className="mk">›</span>{progress?.label ?? t('home.working')}</div></div>}
          <ModalActions>
            <Button variant="primary" disabled={rebuilding} onClick={() => void rebuild()}>
              {rebuilding ? t('rebuild.busy') : t('rebuild.go')}
            </Button>
            <Button disabled={rebuilding} onClick={() => setSheet(null)}>{t('rebuild.notNow')}</Button>
          </ModalActions>
        </Modal>
      )}
    </div>
  )
}
