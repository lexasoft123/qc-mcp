import { Button, LanguageSwitcher, Modal, ModalActions, SegmentedControl } from '@singz/ui'
import type { Prefs as P, Snapshot, UpdateState } from '@shared/types'
import type { Language } from '@shared/i18n'
import { isMac } from '../derive.js'
import { act, checkForUpdates, say, useUpdate } from '../store.js'
import { LOCALES, t, type Key } from '../i18n.js'
import { modes } from '../views/Console.js'
import { Check } from '../components/Icons.js'
import { FLAGS } from '../components/Flags.js'

type Flag = 'login' | 'autoconnect' | 'quitApp' | 'verbose' | 'autoRebuild' | 'updates'

function Toggle({ on, name, desc, onToggle }: {
  readonly on: boolean
  readonly name: string
  readonly desc: string
  readonly onToggle: () => void
}): React.JSX.Element {
  return (
    <button type="button" className="tog" aria-pressed={on} onClick={onToggle}>
      <span className="box"><Check /></span>
      <span className="meta">
        <span className="n">{name}</span>
        <span className="d">{desc}</span>
      </span>
    </button>
  )
}

/** The one place a check that found nothing — or failed — is worth saying. */
function updateLine(u: UpdateState, mac: boolean): string {
  switch (u.state) {
    case 'checking': return t('upd.checking')
    case 'available': return mac ? t('upd.availableMac', { version: u.version }) : t('upd.available', { version: u.version })
    // progress can arrive without a preceding update-available (a resumed
    // download), and then there is no version to name
    case 'downloading': return u.version ? t('upd.downloadingV', { version: u.version, percent: u.percent }) : t('upd.downloading', { percent: u.percent })
    case 'ready': return t('upd.ready', { version: u.version })
    case 'error': return t('upd.error', { message: u.message })
    default: return t('upd.upToDate')
  }
}

export function Prefs({ snap, onClose }: { snap: Snapshot; onClose: () => void }): React.JSX.Element {
  const mac = isMac(snap)
  const os = mac ? 'macOS' : 'Windows'
  const update = useUpdate()
  const p = snap.prefs
  const set = (patch: Partial<P>): void => { void act(() => window.patchbay.setPrefs(patch)) }

  /** value + setter for one boolean pref, spread into <Toggle /> */
  const flag = (id: Flag): { on: boolean; onToggle: () => void } => ({
    on: p[id],
    onToggle: () => set({ [id]: !p[id] } as Partial<P>)
  })

  // Each language by its own name, with its name in THIS language underneath
  // when the two differ — "English / 英语" on a Chinese screen, and no
  // redundant "English / English" on an English one.
  const languages = LOCALES.map((l) => {
    const named = t(`lang.${l.value}` as Key)
    return { value: l.value, label: l.label, code: l.code, flag: FLAGS[l.value], hint: named !== l.label ? named : undefined }
  })
  const systemName = LOCALES.find((l) => l.value === snap.systemLocale)?.label ?? ''

  return (
    <Modal onClose={onClose} cardClassName="prefs-card" aria-label={t('prefs.title')}>
      <h2>{t('prefs.title')}</h2>

      {/* First, and above the fold: the one group someone who cannot read the
          rest of this dialog is looking for. */}
      <div className="pref-group">
        <span className="eyebrow">{t('prefs.language')}</span>
        <div className="pref-row">
          <div className="meta">
            <div className="n">{t('prefs.language')}</div>
            <div className="d">{t('prefs.languageDesc', { os })}</div>
          </div>
          <LanguageSwitcher
            options={languages}
            value={p.language}
            onChange={(language) => set({ language: language as Language })}
            system={{
              label: t('lang.system'),
              hint: t('lang.systemHint', { os, name: systemName }),
              resolves: snap.systemLocale,
              badge: t('lang.auto')
            }}
            aria-label={t('prefs.language')}
          />
        </div>
      </div>

      <div className="pref-group">
        <span className="eyebrow">{t('prefs.startup')}</span>
        <div className="tog-list">
          <Toggle
            {...flag('login')}
            name={mac ? t('prefs.loginMac') : t('prefs.loginWin')}
            desc={t('prefs.loginDesc')}
          />
          <Toggle
            {...flag('autoconnect')}
            name={t('prefs.autoconnect')}
            desc={mac ? t('prefs.autoconnectMac') : t('prefs.autoconnectWin')}
          />
        </div>
      </div>

      <div className="pref-group">
        <span className="eyebrow">{t('prefs.connection')}</span>
        <div className="pref-row">
          <div className="meta">
            <div className="n">{t('prefs.mode')}</div>
            <div className="d">{mac ? t('prefs.modeMac') : t('prefs.modeWin')}</div>
          </div>
          <SegmentedControl options={modes()} value={p.mode} onChange={(mode) => set({ mode })} aria-label={t('aria.mode')} />
        </div>
        <div className="tog-list">
          <Toggle {...flag('quitApp')} name={t('prefs.quitApp')} desc={t('prefs.quitAppDesc')} />
        </div>
      </div>

      <div className="pref-group">
        <span className="eyebrow">{t('prefs.diagnostics')}</span>
        <div className="tog-list">
          <Toggle {...flag('verbose')} name={t('prefs.verbose')} desc={t('prefs.verboseDesc')} />
          {/* nothing to rebuild on Windows — there is no instrumented copy */}
          {mac && (
            <Toggle {...flag('autoRebuild')} name={t('prefs.autoRebuild')} desc={t('prefs.autoRebuildDesc')} />
          )}
        </div>
        <div className="pref-row">
          <div className="meta">
            <div className="n">{t('prefs.frameLog')}</div>
            <div className="d"><code>{p.verbose ? snap.paths.show.logPath : t('prefs.notWritten')}</code></div>
          </div>
          <Button size="sm" disabled={!p.verbose} onClick={() => { void window.patchbay.clearLog(); say(t('prefs.cleared')) }}>
            {t('prefs.clear')}
          </Button>
        </div>
      </div>

      <div className="pref-group">
        <span className="eyebrow">{t('prefs.updates')}</span>
        <div className="tog-list">
          <Toggle
            {...flag('updates')}
            name={t('prefs.updatesAuto')}
            desc={mac ? t('prefs.updatesMac') : t('prefs.updatesWin')}
          />
        </div>
        <div className="pref-row">
          <div className="meta">
            <div className="n">Patchbay {snap.version}</div>
            <div className="d">{updateLine(update, mac)}</div>
          </div>
          {update.state === 'available' && (
            <Button size="sm" variant="primary" onClick={() => { void window.patchbay.update.download() }}>
              {mac ? t('prefs.getIt') : t('prefs.releaseNotes')}
            </Button>
          )}
          {update.state === 'ready' && (
            <Button size="sm" variant="primary" onClick={() => window.patchbay.update.install()}>
              {t('prefs.restartInstall')}
            </Button>
          )}
          {update.state !== 'available' && update.state !== 'ready' && (
            <Button size="sm" disabled={update.state === 'checking' || update.state === 'downloading'} onClick={() => { void checkForUpdates() }}>
              {t('prefs.checkNow')}
            </Button>
          )}
        </div>
      </div>

      <div className="pref-group">
        <span className="eyebrow">{t('prefs.locations')}</span>
        <div className="pref-row">
          <div className="meta"><div className="n">qc-mcp</div><div className="d"><code>{snap.paths.show.repo}</code></div></div>
          <Button size="sm" onClick={() => void act(() => window.patchbay.choosePath('repo'))}>{t('prefs.change')}</Button>
        </div>
        <div className="pref-row">
          <div className="meta"><div className="n">Cortex Control</div><div className="d"><code>{snap.paths.show.cortex}</code></div></div>
          <Button size="sm" onClick={() => void act(() => window.patchbay.choosePath('cortex'))}>{t('prefs.change')}</Button>
        </div>
      </div>

      <ModalActions>
        <Button variant="primary" onClick={onClose}>{t('prefs.done')}</Button>
        <span className="grow" />
        <Button
          variant="danger"
          size="sm"
          onClick={() => { void act(() => window.patchbay.setClients([])); say(t('prefs.removedAll')) }}
        >
          {t('prefs.removeAll')}
        </Button>
      </ModalActions>
    </Modal>
  )
}
