import { useState } from 'react'
import { Button, Modal, ModalActions } from '@singz/ui'
import type { Snapshot } from '@shared/types'
import { act, say } from '../store.js'
import { Check } from '../components/Icons.js'

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
          ? 'No changes to apply'
          : after > before
            ? `Installed for ${after} ${after === 1 ? 'client' : 'clients'}`
            : `Removed — now installed for ${after} ${after === 1 ? 'client' : 'clients'}`
      )
      await act(async () => s)
      onClose()
    } finally {
      setBusy(false)
    }
  }

  const snippet = JSON.stringify(
    { mcpServers: { 'quad-cortex': { command: snap.paths.bin, args: ['--attach', '--socket', snap.paths.socket] } } },
    null,
    2
  )

  return (
    <Modal onClose={onClose} busy={busy} aria-label="Where should qc-mcp be installed">
      <h2>Where should qc-mcp be installed?</h2>
      <p className="fine">
        Patchbay writes the server entry into each client&apos;s own config file and leaves the rest
        of that file alone. Turn one off and the entry is removed.
      </p>

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
              <span className="n">{c.name}{c.found ? '' : ' — not installed'}</span>
              <span className="p">{c.path}</span>
            </span>
          </button>
        ))}
      </div>

      <div className="snippet">{snippet}</div>

      <ModalActions>
        <Button variant="primary" disabled={busy} onClick={() => void apply()}>
          {busy ? 'Applying…' : 'Apply'}
        </Button>
        <Button disabled={busy} onClick={onClose}>Cancel</Button>
        <span className="grow" />
        <span className="fine">{wanted.length} {wanted.length === 1 ? 'client' : 'clients'} selected</span>
      </ModalActions>
    </Modal>
  )
}
