import { useState } from 'react'
import { Button, Modal, ModalActions } from '@singz/ui'
import type { Snapshot } from '@shared/types'
import { act, say } from '../store.js'
import { T, t, tn, type Key } from '../i18n.js'
import { Check } from '../components/Icons.js'

/** Each client's "and then what?" — how it picks the entry up, and how to see that it did. */
const hint = (id: string): Key | null =>
  (['code', 'desktop', 'cursor', 'vscode', 'zed', 'codex'].includes(id) ? (`clients.hint.${id}` as Key) : null)

export function Clients({ snap, onClose }: { snap: Snapshot; onClose: () => void }): React.JSX.Element {
  const [wanted, setWanted] = useState<string[]>(() => snap.clients.filter((c) => c.installed).map((c) => c.id))
  const [busy, setBusy] = useState(false)

  const toggle = (id: string): void =>
    setWanted((w) => (w.includes(id) ? w.filter((x) => x !== id) : [...w, id]))

  const apply = async (): Promise<void> => {
    setBusy(true)
    try {
      const before = snap.clients.filter((c) => c.installed).length
      const s = await window.patchbay.setClients(wanted)
      const after = s.clients.filter((c) => c.installed).length
      say(
        after === before
          ? t('clients.noChange')
          : after > before
            ? tn('clients.installedFor', after)
            : tn('clients.removed', after)
      )
      await act(async () => s)
      onClose()
    } finally {
      setBusy(false)
    }
  }

  const json = JSON.stringify(
    { mcpServers: { 'quad-cortex': { command: snap.paths.bin, args: ['--attach', '--socket', snap.paths.socket] } } },
    null,
    2
  )
  // the same entry as Codex's config.toml wants it — shown whenever Codex is
  // on this machine, since the JSON above is no help to it
  const q = (s: string): string => JSON.stringify(s)
  const toml = `[mcp_servers.quad-cortex]\ncommand = ${q(snap.paths.bin)}\nargs = ["--attach", "--socket", ${q(snap.paths.socket)}]`
  const codexHere = snap.clients.some((c) => c.format === 'toml' && (c.found || wanted.includes(c.id)))
  const chosen = snap.clients.filter((c) => wanted.includes(c.id) && hint(c.id))

  return (
    <Modal onClose={onClose} busy={busy} aria-label={t('clients.title')}>
      <h2>{t('clients.title')}</h2>
      <p className="fine">{t('clients.intro')}</p>

      <div className="tog-list">
        {snap.clients.map((c) => (
          <button
            type="button"
            className="tog"
            key={c.id}
            disabled={!c.found || busy}
            aria-pressed={wanted.includes(c.id)}
            onClick={() => toggle(c.id)}
          >
            <span className="box"><Check /></span>
            <span className="meta">
              <span className="n">{c.name}{c.found ? '' : t('clients.notInstalled')}</span>
              <span className="p">{c.path}</span>
            </span>
          </button>
        ))}
      </div>

      {chosen.length > 0 && (
        <div className="hints">
          <span className="eyebrow">{t('clients.after')}</span>
          {chosen.map((c) => (
            <div className="hint" key={c.id}>
              <b>{c.name}</b>
              <span><T k={hint(c.id)!} /></span>
            </div>
          ))}
        </div>
      )}

      <div className="snippet-label"><T k="clients.snippetJson" /></div>
      <div className="snippet">{json}</div>
      {codexHere && (
        <>
          <div className="snippet-label"><T k="clients.snippetToml" /></div>
          <div className="snippet">{toml}</div>
        </>
      )}

      <ModalActions>
        <Button variant="primary" disabled={busy} onClick={() => void apply()}>
          {busy ? t('clients.applying') : t('clients.apply')}
        </Button>
        <Button disabled={busy} onClick={onClose}>{t('clients.cancel')}</Button>
        <span className="grow" />
        <span className="fine">{tn('clients.selected', wanted.length)}</span>
      </ModalActions>
    </Modal>
  )
}
