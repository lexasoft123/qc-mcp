import type { Snapshot } from '@shared/types'
import { isLinked, regCount } from '../derive.js'
import { t, tn } from '../i18n.js'
import { Bubble, Device, PatchCable } from './Icons.js'

function Node({ tone, cap, sub, children, onClick }: {
  tone: string
  cap: string
  sub: string
  children: React.JSX.Element
  onClick?: () => void
}): React.JSX.Element {
  return (
    <div className={`node ${tone}`}>
      {onClick
        ? <button type="button" className="glyph" onClick={onClick} aria-label={cap}>{children}</button>
        : <span className="glyph">{children}</span>}
      <span className="cap">{cap}</span>
      <span className="sub">{sub}</span>
    </div>
  )
}

export function SignalPath({ snap }: { snap: Snapshot }): React.JSX.Element {
  const live = snap.daemon.state === 'running'
  const starting = snap.daemon.state === 'starting'
  const linked = isLinked(snap)
  const n = regCount(snap)

  return (
    <div className="flow">
      <Node
        tone={n && live ? 'on' : ''}
        cap={t('path.claude')}
        sub={n ? tn('clients', n) : t('path.noneYet')}
      >
        <Bubble />
      </Node>
      <span className={live && n > 0 ? 'link lit' : 'link'}><i /></span>
      <Node
        tone={live || starting ? 'on' : ''}
        cap={t('path.patchbay')}
        sub={live ? t('path.running') : starting ? t('path.starting') : t('path.stopped')}
      >
        <PatchCable />
      </Node>
      <span className={linked ? 'link lit' : 'link'}><i /></span>
      <Node
        tone={!snap.device.present ? 'bad' : linked ? 'filled' : ''}
        cap={t('path.device')}
        sub={!snap.device.present ? t('path.notPlugged') : linked ? t('path.connected') : t('path.waiting')}
      >
        <Device />
      </Node>
    </div>
  )
}
