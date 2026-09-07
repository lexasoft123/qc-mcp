import assert from 'node:assert/strict'
import type { Facts, Goal, Plan } from '../src/shared/session.ts'
import type { SessionOps } from '../src/shared/run-plan.ts'
import type { Mode, SessionMode } from '../src/shared/types.ts'

/**
 * A machine small enough to enumerate.
 *
 * The plans are decided from facts, so the only way to test them properly is to
 * have every combination of facts to hand — including the ones a person would
 * have to work to produce, which is exactly where the old behaviour broke. This
 * is that machine: the same state a real one exposes, and operations that
 * change it the way the real ones do, including refusing what a real one would
 * refuse.
 */

export type World = Facts & {
  /** Which op should fail, for the failure-injection pass. */
  breaks?: keyof SessionOps
}

export const MODES: Mode[] = ['auto', 'bridge', 'direct']
export const GOALS: Goal[] = ['connect', 'disconnect', 'show-app']

/**
 * Worlds that can actually exist. Enumerating the raw cross product produces
 * nonsense — a ready bridge with no app running, a Windows machine with an
 * instrumented build — and a plan is not obliged to be sensible about a state
 * the machine cannot be in.
 */
export function coherent(f: Facts): boolean {
  if (f.platform === 'win') {
    if (f.instrumentedBuilt || f.cortexInstrumented) return false
    // Windows' "bridge" is a second handle: readiness is just the app being up.
    if (f.bridgeReady !== f.cortexRunning) return false
    if (f.daemonSession === 'bridge') return false
  } else if (f.daemonSession === 'shared') {
    return false
  }
  // the interposer's FIFOs are opened BY the instrumented app
  if (f.bridgeReady && !(f.cortexRunning && (f.platform === 'win' || f.cortexInstrumented))) return false
  if (f.cortexInstrumented && !f.cortexRunning) return false
  if (f.cortexInstrumented && !f.instrumentedBuilt) return false
  if (f.cortexRunning && !f.cortexInstalled && !f.cortexInstrumented) return false
  if (f.daemonRunning !== (f.daemonSession !== null)) return false
  // a bridge session cannot outlive the app it rides
  if (f.daemonSession === 'bridge' && !f.bridgeReady) return false
  if (f.daemonSession === 'shared' && !f.cortexRunning) return false
  // direct means the daemon holds the device alone
  if (f.daemonSession === 'direct' && f.cortexRunning) return false
  if (f.daemonRunning && !f.devicePresent) return false
  return true
}

/** Every world that can exist. ~200 of them, which is few enough to be exhaustive. */
export function allWorlds(): Facts[] {
  const out: Facts[] = []
  const bools = [false, true]
  const sessions: (SessionMode | null)[] = [null, 'bridge', 'shared', 'direct']
  for (const platform of ['mac', 'win'] as const)
    for (const devicePresent of bools)
      for (const cortexInstalled of bools)
        for (const cortexRunning of bools)
          for (const cortexInstrumented of bools)
            for (const instrumentedBuilt of bools)
              for (const bridgeReady of bools)
                for (const daemonSession of sessions) {
                  const f: Facts = {
                    platform, devicePresent, cortexInstalled, cortexRunning,
                    cortexInstrumented, instrumentedBuilt, bridgeReady,
                    daemonRunning: daemonSession !== null, daemonSession
                  }
                  if (coherent(f)) out.push(f)
                }
  return out
}

export const say = (f: Facts): string =>
  `${f.platform} device=${f.devicePresent ? 'y' : 'n'} cc=${
    f.cortexRunning ? (f.cortexInstrumented ? 'instrumented' : 'stock') : 'closed'
  } built=${f.instrumentedBuilt ? 'y' : 'n'} bridge=${f.bridgeReady ? 'y' : 'n'} session=${f.daemonSession ?? '-'}`

/** Operations that move the world, and refuse what the real ones refuse. */
export function ops(w: World, log: string[] = []): SessionOps & { log: string[] } {
  const broke = (k: keyof SessionOps): boolean => w.breaks === k
  return {
    log,
    async stopDaemon() {
      log.push('stop-daemon')
      w.daemonRunning = false
      w.daemonSession = null
    },
    async startDaemon(session) {
      log.push(`start-daemon:${session}`)
      if (broke('startDaemon')) return 'the daemon refused to start'
      // The real refusals, in the real words.
      if (session === 'direct' && w.cortexRunning) {
        return 'Cortex Control is holding the device, so it cannot be seized.'
      }
      if (session === 'bridge' && !w.bridgeReady) {
        return 'bridge mode needs Cortex Control running'
      }
      if (session === 'shared' && !w.cortexRunning) {
        return 'bridge mode needs Cortex Control running'
      }
      if (!w.devicePresent) return 'no Quad Cortex found'
      w.daemonRunning = true
      w.daemonSession = session
      return null
    },
    async quitCortex() {
      log.push('quit-cortex')
      // Quitting the app out from under a bridge session is the fault this
      // whole rewrite exists to prevent, so the fake refuses to model it.
      assert.notEqual(w.daemonSession, 'bridge',
        'quit Cortex Control while a bridge session was live')
      assert.notEqual(w.daemonSession, 'shared',
        'quit Cortex Control while a shared session was live')
      w.cortexRunning = false
      w.cortexInstrumented = false
      w.bridgeReady = false
    },
    async launchBridge() {
      log.push('launch-bridge')
      if (broke('launchBridge')) return 'the bridge script failed'
      if (!w.instrumentedBuilt) return 'Cortex Control is not installed.'
      // run-bridge.sh replaces whatever instance was there
      w.cortexRunning = true
      w.cortexInstrumented = true
      return null
    },
    async launchStock() {
      log.push('launch-stock')
      if (broke('launchStock')) return 'the app failed to launch'
      if (!w.cortexInstalled) return 'Cortex Control is not installed.'
      w.cortexRunning = true
      w.cortexInstrumented = false
      if (w.platform === 'win') w.bridgeReady = true
      return null
    },
    async awaitBridge() {
      log.push('await-bridge')
      if (broke('awaitBridge')) return false
      w.bridgeReady = w.cortexInstrumented
      return w.bridgeReady
    },
    async awaitCortex() {
      log.push('await-cortex')
      if (broke('awaitCortex')) return false
      return w.cortexRunning
    },
    async focusCortex() {
      log.push('focus-cortex')
      // Focusing nothing means the plan asked to raise a window that is not
      // there; on the real machine that is what launched a second app.
      assert.ok(w.cortexRunning, 'focused Cortex Control while none was running')
    }
  }
}

export const stepNames = (p: Plan): string[] => p.steps.map((s) => s.do)
