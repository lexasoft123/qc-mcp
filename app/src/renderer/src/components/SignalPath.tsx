import type { Snapshot } from '@shared/types'
import { isLinked, modePlan, regCount, sessionMode } from '../derive.js'
import { AppWindow, Bubble, Device, PatchCable } from './Icons.js'

/**
 * The route, drawn.
 *
 * The connection mode used to be a word in the header and the same word in the
 * footer, which told you nothing about what it meant. It is really a question
 * about the shape of this path — does the signal run THROUGH Cortex Control, or
 * around it — so the path draws that: bridge grows a fourth node, direct does
 * not, and Windows' shared handle says it runs beside the app rather than
 * through it. Before a session exists the shape comes from what the selected
 * mode WILL open, so pressing Connect holds no surprise.
 */

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

const Link = ({ lit, label }: { lit: boolean; label?: string }): React.JSX.Element => (
  <span className={lit ? 'link lit' : 'link'}>
    <i />
    {label && <em>{label}</em>}
  </span>
)

export function SignalPath({ snap }: { snap: Snapshot }): React.JSX.Element {
  const live = snap.daemon.state === 'running'
  const starting = snap.daemon.state === 'starting'
  const linked = isLinked(snap)
  const n = regCount(snap)

  // What is open, or — before anything is — what this mode would open.
  const route = live ? sessionMode(snap) : modePlan(snap).will
  const through = route === 'bridge'
  const beside = route === 'shared'
  const appUp = snap.cortex.running

  return (
    <div className={`flow route-${route}`}>
      <Node
        tone={n && live ? 'on' : ''}
        cap="Claude"
        sub={n ? `${n} ${n === 1 ? 'client' : 'clients'}` : 'none yet'}
      >
        <Bubble />
      </Node>
      <Link lit={live && n > 0} />
      <Node
        tone={live || starting ? 'on' : ''}
        cap="Patchbay"
        sub={live ? 'running' : starting ? 'starting…' : 'stopped'}
      >
        <PatchCable />
      </Node>

      {through && (
        <>
          <Link lit={linked} label="through" />
          <Node
            tone={`cc${appUp ? ' on' : ''}`}
            cap="Cortex Control"
            sub={appUp ? 'session shared' : 'not open yet'}
            onClick={() => { void window.patchbay.cortexFocus() }}
          >
            <AppWindow />
          </Node>
        </>
      )}

      <Link
        lit={linked}
        label={through ? undefined : beside ? 'beside the app' : 'direct'}
      />
      <Node
        tone={!snap.device.present ? 'bad' : linked ? 'filled' : ''}
        cap="Quad Cortex"
        sub={!snap.device.present ? 'not plugged in' : linked ? 'connected' : 'waiting'}
      >
        <Device />
      </Node>
    </div>
  )
}
