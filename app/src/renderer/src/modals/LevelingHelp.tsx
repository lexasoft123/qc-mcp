import { Button } from '@singz/ui'

/**
 * How measured leveling works, in the order you do it.
 *
 * Five steps and only the fourth writes anything — which is the point worth
 * making early, because the rest of the app is a fader you turn and this one
 * moves faders for you.
 */

const STEPS: [string, React.ReactNode][] = [
  ['Record a reference riff',
   <>Arm the pedal and play — recording starts on your first note and stops when you
     stop. Space works it. The riff is taken from <code>USB 1/2</code>, the dry DI, so
     it is your instrument with no preset on it. Play it back to check it before you
     trust it.</>],
  ['Measure',
   <>Each preset is recalled, the riff is played into it through <code>USB in 5/6</code>,
     and what comes back is measured. Nothing is written, and nothing is heard: while a
     measurement runs the output is tapped to USB and is not reaching the XLRs.</>],
  ['Read the report',
   <>Every preset on one line with the correction it needs. An even spread means they
     already sit together; a wide one is what you came here to fix.</>],
  ['Apply, if you want to hear it',
   <>Moves the lane output fader on the device so you can listen to them balanced. It
     does <em>not</em> touch the preset file, and <em>Undo trims</em> puts every fader
     back where it was.</>],
  ['Save what you keep',
   <>Only a save makes a trim permanent, and only for the preset you save.</>]
]

const NEEDS: [string, string][] = [
  ['Connection', 'Daemon running'],
  ['Audio device', 'Quad Cortex · 48 kHz fixed'],
  ['USB dry/wet', '1/2 must carry the dry DI']
]

export function LevelingHelp({ onClose }: { onClose: () => void }): React.JSX.Element {
  return (
    <div className="modal-scrim" onClick={onClose} role="presentation">
      <div className="modal-card help-card" onClick={(e) => e.stopPropagation()}
           role="dialog" aria-label="How measured leveling works">
        <h2>Leveling by measurement</h2>
        <p className="fine">
          One riff of yours, played into every preset, so they can be compared on a
          number instead of on memory. Five steps, and only the fourth changes anything.
        </p>

        <div className="steps">
          {STEPS.map(([title, body], i) => (
            <div className="step" key={title}>
              <span className="num">{i + 1}</span>
              <div className="txt"><h3>{title}</h3><p>{body}</p></div>
            </div>
          ))}
        </div>

        <dl className="facts" style={{ marginTop: 16 }}>
          {NEEDS.map(([k, v]) => (
            <div className="fact" key={k}><dt>{k}</dt><dd>{v}</dd></div>
          ))}
        </dl>

        <p className="fine" style={{ marginTop: 12 }}>
          A capture that comes back as digital silence is reported as an error rather
          than as a quiet preset — on macOS a denied microphone permission returns
          silence instead of failing, and the two must not look alike.
        </p>

        <div className="modal-actions">
          <Button variant="primary" onClick={onClose}>Got it</Button>
        </div>
      </div>
    </div>
  )
}
