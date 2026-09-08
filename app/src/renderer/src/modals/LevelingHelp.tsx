import { Button, Modal, ModalActions } from '@singz/ui'
import { T, t } from '../i18n.js'

/**
 * How measured leveling works, in the order you do it.
 *
 * Five steps and only the fourth writes anything — which is the point worth
 * making early, because the rest of the app is a fader you turn and this one
 * moves faders for you.
 *
 * The kit's Modal, not a hand-rolled scrim: three of the four dialogs here
 * closed on Escape and this one did not, which is worse than none of them
 * doing it.
 */

const STEPS = ['record', 'measure', 'read', 'apply', 'save'] as const
const NEEDS = ['connection', 'audio', 'usb'] as const

export function LevelingHelp({ onClose }: { onClose: () => void }): React.JSX.Element {
  return (
    <Modal onClose={onClose} cardClassName="help-card" aria-label={t('help.title')}>
      <h2>{t('help.title')}</h2>
      <p className="fine">{t('help.lede')}</p>

      <div className="steps">
        {STEPS.map((id, i) => (
          <div className="step" key={id}>
            <span className="num">{i + 1}</span>
            <div className="txt">
              <h3>{t(`help.${id}` as Parameters<typeof t>[0])}</h3>
              <p><T k={`help.${id}.body` as Parameters<typeof t>[0]} /></p>
            </div>
          </div>
        ))}
      </div>

      <dl className="facts" style={{ marginTop: 16 }}>
        {NEEDS.map((id) => (
          <div className="fact" key={id}>
            <dt>{t(`help.need.${id}` as Parameters<typeof t>[0])}</dt>
            <dd>{t(`help.need.${id}.value` as Parameters<typeof t>[0])}</dd>
          </div>
        ))}
      </dl>

      <p className="fine" style={{ marginTop: 12 }}>{t('help.silence')}</p>

      <ModalActions>
        <Button variant="primary" onClick={onClose}>{t('gotIt')}</Button>
      </ModalActions>
    </Modal>
  )
}
