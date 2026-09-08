import { Button, Modal, ModalActions } from '@singz/ui'
import { grouped } from '../keys.js'

/**
 * Every shortcut, from the same list the handlers read.
 *
 * Written from `keys.ts` rather than by hand: the old legend listed five
 * bindings and omitted `space`, the one a player uses most, because it was
 * maintained separately from the code that bound it.
 */
export function Shortcuts({ onClose }: { onClose: () => void }): React.JSX.Element {
  return (
    <Modal onClose={onClose} aria-label="Keyboard shortcuts" cardClassName="sc-card">
      <h2>Keyboard</h2>
      <p className="fine">
        Everything below works with the mouse too — these are for when both hands
        are holding a guitar.
      </p>

      <div className="sc-groups">
        {grouped().map(([group, rows]) => (
          <div className="sc-group" key={group}>
            <span className="eyebrow">{group}</span>
            <dl>
              {rows.map((r) => (
                <div className="sc-row" key={`${group}-${r.cap}-${r.does}`}>
                  <dt>{r.cap.split(' ').map((c) => <kbd key={c}>{c}</kbd>)}</dt>
                  <dd>{r.does}</dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
      </div>

      <ModalActions>
        <Button variant="primary" onClick={onClose}>Got it</Button>
      </ModalActions>
    </Modal>
  )
}
