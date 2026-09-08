import { Button, Modal, ModalActions } from '@singz/ui'
import { grouped } from '../keys.js'
import { t } from '../i18n.js'

/**
 * Every shortcut, from the same list the handlers read.
 *
 * Written from `keys.ts` rather than by hand: the old legend listed five
 * bindings and omitted `space`, the one a player uses most, because it was
 * maintained separately from the code that bound it.
 */
export function Shortcuts({ onClose }: { onClose: () => void }): React.JSX.Element {
  return (
    <Modal onClose={onClose} aria-label={t('keys.title')} cardClassName="sc-card">
      <h2>{t('keys.title')}</h2>
      <p className="fine">{t('keys.lede')}</p>

      <div className="sc-groups">
        {grouped().map(([group, rows]) => (
          <div className="sc-group" key={group}>
            <span className="eyebrow">{t(group)}</span>
            <dl>
              {rows.map((r) => (
                <div className="sc-row" key={`${group}-${r.cap}-${r.does}`}>
                  <dt>{r.cap.split(' ').map((c) => <kbd key={c}>{c}</kbd>)}</dt>
                  <dd>{t(r.does)}</dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
      </div>

      <ModalActions>
        <Button variant="primary" onClick={onClose}>{t('gotIt')}</Button>
      </ModalActions>
    </Modal>
  )
}
