import { Button, Modal, ModalActions, SegmentedControl } from '@singz/ui'
import type { Mode, Prefs as P, Snapshot } from '@shared/types'
import { isMac } from '../derive.js'
import { act, say } from '../store.js'
import { Check } from '../components/Icons.js'

type Flag = 'login' | 'autoconnect' | 'quitApp' | 'verbose' | 'autoRebuild'

const MODES: { value: Mode; label: string }[] = [
  { value: 'auto', label: 'Auto' },
  { value: 'bridge', label: 'Bridge' },
  { value: 'direct', label: 'Direct' }
]

export function Prefs({ snap, onClose }: { snap: Snapshot; onClose: () => void }): React.JSX.Element {
  const mac = isMac(snap)
  const p = snap.prefs
  const set = (patch: Partial<P>): void => { void act(() => window.patchbay.setPrefs(patch)) }

  const Toggle = ({ id, name, desc }: { id: Flag; name: string; desc: string }): React.JSX.Element => (
    <button type="button" className="tog" aria-pressed={p[id]} onClick={() => set({ [id]: !p[id] } as Partial<P>)}>
      <span className="box"><Check /></span>
      <span className="meta">
        <span className="n">{name}</span>
        <span className="d">{desc}</span>
      </span>
    </button>
  )

  return (
    <Modal onClose={onClose} cardClassName="prefs-card" aria-label="Preferences">
      <h2>Preferences</h2>

      <div className="pref-group">
        <span className="eyebrow">Startup</span>
        <div className="tog-list">
          <Toggle
            id="login"
            name={`Start Patchbay ${mac ? 'when I log in' : 'when I sign in'}`}
            desc="It waits out of the way and connects nothing until you ask."
          />
          <Toggle
            id="autoconnect"
            name="Connect as soon as the Quad Cortex is plugged in"
            desc={mac ? 'Starts the daemon and opens Cortex Control for you.' : 'Starts the daemon, so Claude can reach the device right away.'}
          />
        </div>
      </div>

      <div className="pref-group">
        <span className="eyebrow">Connection</span>
        <div className="pref-row">
          <div className="meta">
            <div className="n">Connection mode</div>
            <div className="d">
              {mac
                ? "Auto shares Cortex Control's session when the app is open, and takes the device directly when it is not."
                : 'Auto opens a shared handle beside Cortex Control, and takes the device exclusively when the app is closed.'}
            </div>
          </div>
          <SegmentedControl options={MODES} value={p.mode} onChange={(mode) => set({ mode })} aria-label="Connection mode" />
        </div>
        <div className="tog-list">
          <Toggle id="quitApp" name="Quit Cortex Control when Patchbay quits" desc="Leave this off if you also use the app on its own." />
        </div>
      </div>

      <div className="pref-group">
        <span className="eyebrow">Diagnostics</span>
        <div className="tog-list">
          <Toggle
            id="verbose"
            name="Write the frame log"
            desc="Records every report to and from the device. The Logs screen reads it, and bug reports need it."
          />
          {/* nothing to rebuild on Windows — there is no instrumented copy */}
          {mac && (
            <Toggle
              id="autoRebuild"
              name="Rebuild after a Cortex Control update"
              desc="Runs the rebuild on its own, and quits the app first."
            />
          )}
        </div>
        <div className="pref-row">
          <div className="meta">
            <div className="n">Frame log</div>
            <div className="d"><code>{p.verbose ? snap.paths.logPath : 'not being written'}</code></div>
          </div>
          <Button size="sm" disabled={!p.verbose} onClick={() => { void window.patchbay.clearLog(); say('Frame log cleared') }}>
            Clear
          </Button>
        </div>
      </div>

      <div className="pref-group">
        <span className="eyebrow">Locations</span>
        <div className="pref-row">
          <div className="meta"><div className="n">qc-mcp</div><div className="d"><code>{snap.paths.repo}</code></div></div>
          <Button size="sm" onClick={() => void act(() => window.patchbay.choosePath('repo'))}>Change…</Button>
        </div>
        <div className="pref-row">
          <div className="meta"><div className="n">Cortex Control</div><div className="d"><code>{snap.paths.cortex}</code></div></div>
          <Button size="sm" onClick={() => void act(() => window.patchbay.choosePath('cortex'))}>Change…</Button>
        </div>
      </div>

      <ModalActions>
        <Button variant="primary" onClick={onClose}>Done</Button>
        <span className="grow" />
        <Button
          variant="danger"
          size="sm"
          onClick={() => { void act(() => window.patchbay.setClients([])); say('Removed qc-mcp from every client. Nothing else was deleted.') }}
        >
          Remove from all clients
        </Button>
      </ModalActions>
    </Modal>
  )
}
