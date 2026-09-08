/**
 * The connection decision, in one place.
 *
 * Three things used to decide this independently and disagree: the renderer's
 * connect() sequence, the daemon's own `auto` branch, and whatever the buttons
 * happened to do. Pressing them in the wrong order left the app describing a
 * session it did not have — the mode selector said Direct over a live bridge,
 * Disconnect deleted the socket of a daemon it could not stop, and Show Cortex
 * Control launched the stock bundle on top of the instrumented one.
 *
 * So the decision is a pure function of what is observably true, it produces a
 * PLAN rather than performing anything, and the same function answers "what
 * would happen if I pressed this" for the interface. Main executes the plan;
 * the renderer renders it; the tests enumerate it.
 */

import type { Mode, SessionMode } from './types.js'

/** Everything a decision here is allowed to depend on. */
export interface Facts {
  platform: 'mac' | 'win'
  devicePresent: boolean
  cortexInstalled: boolean
  /** A Cortex Control — either build — is running. */
  cortexRunning: boolean
  /** ...and it is the instrumented copy, not the stock app. */
  cortexInstrumented: boolean
  /** An instrumented copy exists to launch (macOS; always false on Windows). */
  instrumentedBuilt: boolean
  /** FIFOs open AND the instrumented app alive — the daemon's own test. */
  bridgeReady: boolean
  daemonRunning: boolean
  /** What the running daemon reported it opened. */
  daemonSession: SessionMode | null
  /**
   * The session lock: who holds the device, from the record they wrote.
   *
   * This is the fact the others used to be guessed from. `ours` distinguishes a
   * session Patchbay started from one it merely found — a daemon somebody ran
   * by hand, or an MCP server that opened the device itself, which is the
   * contender nothing could see before.
   */
  heldBy: {
    owner: 'daemon' | 'mcp' | 'bench'
    mode: SessionMode
    ours: boolean
    /**
     * Its socket answered. A daemon whose process is alive but whose endpoint
     * is gone — the socket file deleted under it, which is precisely what the
     * old stop() did to adopted daemons — is holding the device and serving
     * nobody. It cannot be joined, and saying so is the whole point: that state
     * used to be invisible, and every later connect failed on a device that was
     * plainly in use.
     */
    serving: boolean
    /** A serving daemon can simply be joined; an MCP server cannot. */
    adoptable: boolean
  } | null
}

export type Step =
  | { do: 'stop-daemon' }
  /** End somebody else's session, by the pid in the lock. Only ever from an
   *  explicit take-over — never a side effect of pressing Connect. */
  | { do: 'take-over' }
  | { do: 'quit-cortex' }
  | { do: 'launch-bridge' }
  | { do: 'launch-stock' }
  | { do: 'await-bridge' }
  | { do: 'await-cortex' }
  | { do: 'start-daemon'; session: SessionMode }
  | { do: 'focus-cortex' }

export interface Plan {
  /** The session this reaches, or null for goals that open none. */
  session: SessionMode | null
  steps: Step[]
  /** Why it cannot run. When set, `steps` is empty and the sentence is the fix. */
  blocked: string | null
  /** True when the goal is already met and there is nothing to do. */
  satisfied: boolean
}

/** `take-over` is `connect` with permission to end somebody else's session. */
export type Goal = 'connect' | 'disconnect' | 'show-app' | 'take-over'

/**
 * Which session a mode opens, given what is running.
 *
 * `auto` uses what is there and starts nothing extra: a Cortex Control already
 * open means share it, an idle machine means take the device. That is exactly
 * what qc_mcp/daemon.py serve() decides, and having the launcher decide
 * differently — it used to open Cortex Control unasked, so `auto` never once
 * chose direct — is what made the mode unpredictable.
 */
export function resolveSession(mode: Mode, f: Facts): SessionMode {
  const shared = f.platform === 'win' ? 'shared' : 'bridge'
  if (mode === 'direct') return 'direct'
  if (mode === 'bridge') return shared
  return f.bridgeReady || f.cortexRunning ? shared : 'direct'
}

/** The steps that reach `session` from here, before any teardown. */
function build(session: SessionMode, f: Facts): Plan {
  const done = (steps: Step[]): Plan => ({ session, steps, blocked: null, satisfied: false })
  const no = (blocked: string): Plan => ({ session, steps: [], blocked, satisfied: false })

  if (session === 'direct') {
    // Cortex Control holds the device exclusively on both platforms.
    const steps: Step[] = []
    if (f.cortexRunning) steps.push({ do: 'quit-cortex' })
    steps.push({ do: 'start-daemon', session })
    return done(steps)
  }

  if (session === 'shared') {
    if (!f.cortexInstalled) return no('Cortex Control is not installed.')
    // Windows needs no interposer — the app just has to be up, and the daemon
    // opens its own non-exclusive handle beside it.
    const steps: Step[] = []
    if (!f.cortexRunning) steps.push({ do: 'launch-stock' }, { do: 'await-cortex' })
    steps.push({ do: 'start-daemon', session })
    return done(steps)
  }

  // bridge
  if (!f.instrumentedBuilt) {
    return no(f.cortexRunning
      // auto lands here too: the app holds the device and there is nothing to
      // share it through, which is the same dead end daemon.serve() reports.
      ? 'Cortex Control is holding the device and there is no instrumented copy to share it through — quit the app, or build the copy in Setup.'
      : 'Bridge mode needs the instrumented copy of Cortex Control — build it in Setup.')
  }
  // run-bridge.sh replaces a stock instance itself, so a bridge that is not yet
  // ready is one step whether or not the stock app is in the way.
  const steps: Step[] = []
  if (!f.bridgeReady) steps.push({ do: 'launch-bridge' }, { do: 'await-bridge' })
  steps.push({ do: 'start-daemon', session })
  return done(steps)
}

/**
 * What pressing this does, from here.
 *
 * `quitApp` is the preference that says Cortex Control belongs to Patchbay and
 * should go down with it; without it a disconnect leaves the app alone.
 */
export function planFor(goal: Goal, mode: Mode, f: Facts, quitApp = false): Plan {
  if (goal === 'disconnect') {
    const steps: Step[] = []
    // Disconnecting a session Patchbay did not start is an eviction, and is
    // named as one. It used to be planned as an ordinary stop, which is how a
    // daemon somebody else was running got killed by our Disconnect button
    // without anything ever saying so.
    if (f.daemonRunning) {
      steps.push(f.heldBy && !f.heldBy.ours ? { do: 'take-over' } : { do: 'stop-daemon' })
    }
    if (quitApp && f.cortexRunning) steps.push({ do: 'quit-cortex' })
    return { session: null, steps, blocked: null, satisfied: steps.length === 0 }
  }

  if (goal === 'show-app') {
    // NEVER open the stock bundle by path while a Cortex Control is running:
    // on macOS that starts a SECOND instance next to the instrumented one, and
    // the two then fight over the device. Focus the process that is up.
    if (f.cortexRunning) {
      return { session: null, steps: [{ do: 'focus-cortex' }], blocked: null, satisfied: false }
    }
    // And never START one into a device the daemon is holding exclusively.
    // Cortex Control does not survive that: it comes up, finds the device it
    // expects unavailable, and segfaults on its own null a few seconds in
    // (EXC_BAD_ACCESS on the JUCE message thread, nine seconds after launch).
    if (f.daemonSession === 'direct') {
      return {
        session: null, steps: [], satisfied: false,
        blocked: 'The daemon is holding the Quad Cortex on its own, and Cortex Control cannot start into a device it has no access to. Disconnect first, or switch to Bridge.'
      }
    }
    if (f.platform === 'mac' && mode !== 'direct' && f.instrumentedBuilt) {
      return { session: null, steps: [{ do: 'launch-bridge' }, { do: 'await-bridge' }], blocked: null, satisfied: false }
    }
    if (!f.cortexInstalled) {
      return { session: null, steps: [], blocked: 'Cortex Control is not installed.', satisfied: false }
    }
    return { session: null, steps: [{ do: 'launch-stock' }], blocked: null, satisfied: false }
  }

  // connect (and take-over, which is connect with permission to evict)
  if (!f.devicePresent) {
    return { session: null, steps: [], blocked: 'Plug the Quad Cortex in over USB.', satisfied: false }
  }
  const want = resolveSession(mode, f)
  if (f.daemonRunning && f.daemonSession === want) {
    return { session: want, steps: [], blocked: null, satisfied: true }
  }

  // Somebody else's session. A serving daemon in the mode we want is simply
  // joined; anything else has to end first, and ending it is a decision, not a
  // side effect of pressing Connect.
  const held = f.heldBy
  const evict = held && !held.ours && !(held.adoptable && held.mode === want)
  if (evict && goal !== 'take-over') {
    return {
      session: want, steps: [], satisfied: false,
      blocked: held.owner === 'mcp'
        ? `An MCP server is holding the Quad Cortex in ${held.mode} mode. ` +
          'Take over to end it and connect, or quit that client.'
        : `A ${held.mode} session started outside Patchbay is holding the Quad Cortex. ` +
          'Take over to end it and connect.'
    }
  }

  const rest = build(want, f)
  if (rest.blocked) return rest
  // A session that is open but wrong is torn down first — the mode selector
  // used to change only the preference, so it claimed a session nobody had.
  const teardown: Step[] = []
  if (evict) teardown.push({ do: 'take-over' })
  else if (f.daemonRunning) teardown.push({ do: 'stop-daemon' })
  return { ...rest, steps: [...teardown, ...rest.steps] }
}

/**
 * What moving the mode selector means.
 *
 * Two things it does NOT mean. It does not open Cortex Control or start a
 * bridge: with nothing connected the mode is a preference and a picture, and
 * pressing a picture used to launch the app. And it does not re-shape a session
 * that is already open — that is a disconnect and a connect, which is asked for
 * on purpose rather than triggered by touching a selector.
 */
export function modeSwitch(f: Facts, daemonBusy = false): { allowed: boolean; why: string | null } {
  if (f.daemonRunning || daemonBusy) {
    return { allowed: false, why: 'Disconnect first — the mode decides how the session is opened.' }
  }
  return { allowed: true, why: null }
}

/** One line saying what the plan does, for the interface. */
export function planWords(goal: Goal, mode: Mode, f: Facts, p: Plan): string {
  if (p.blocked) return p.blocked
  if (goal === 'disconnect') {
    return p.satisfied
      ? 'Nothing is connected.'
      : p.steps.some((s) => s.do === 'quit-cortex')
        ? 'Stops the daemon and quits Cortex Control.'
        : 'Stops the daemon. Cortex Control is left alone.'
  }
  if (goal === 'show-app') {
    return f.cortexRunning ? 'Brings Cortex Control forward.' : 'Opens Cortex Control.'
  }
  if (p.satisfied) return 'Already connected this way.'

  const swap = p.steps.some((s) => s.do === 'stop-daemon')
  const quit = p.steps.some((s) => s.do === 'quit-cortex')
  const opens = p.steps.some((s) => s.do === 'launch-bridge' || s.do === 'launch-stock')
  const mac = f.platform === 'mac'

  const body = p.session === 'direct'
    ? quit
      ? 'quits Cortex Control and takes the device.'
      : 'takes the device on its own.'
    : p.session === 'shared'
      ? opens
        ? 'opens Cortex Control and takes a second handle beside it.'
        : 'takes a second handle beside Cortex Control.'
      : opens
        ? mac
          ? 'opens the instrumented Cortex Control and shares its session — about twenty seconds.'
          : 'opens Cortex Control and shares its session.'
        : "shares the Cortex Control session that is already open."

  return `${swap ? 'Closes the current session, then ' : 'Patchbay '}${body}`
}
