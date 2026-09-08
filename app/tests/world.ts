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
export const GOALS: Goal[] = ['connect', 'disconnect', 'show-app', 'take-over']

/**
 * Every shape the session lock can take, including nobody — and including the
 * one that used to be invisible: a daemon whose process is alive but whose
 * socket is gone. It holds the device and serves nobody, and Patchbay's own
 * stop() is what created that state, by deleting the socket of a daemon it had
 * adopted without being able to kill it.
 */
const daemonLock = (mode: SessionMode, ours: boolean, serving: boolean): Facts['heldBy'] =>
  ({ owner: 'daemon', mode, ours, serving, adoptable: serving })

export const HOLDERS: Facts['heldBy'][] = [
  null,
  daemonLock('bridge', true, true),
  daemonLock('direct', true, true),
  daemonLock('shared', true, true),
  daemonLock('bridge', false, true),
  daemonLock('direct', false, true),
  daemonLock('shared', false, true),
  // alive, holding the device, answering nobody
  daemonLock('direct', true, false),
  daemonLock('bridge', false, false),
  daemonLock('direct', false, false),
  { owner: 'mcp', mode: 'direct', ours: false, serving: false, adoptable: false },
  { owner: 'mcp', mode: 'bridge', ours: false, serving: false, adoptable: false },
  { owner: 'bench', mode: 'bridge', ours: false, serving: false, adoptable: false }
]

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

  // --- the lock has to agree with the rest of the world -------------------
  const h = f.heldBy
  // a session we are serving is a session somebody holds, and vice versa for
  // the daemon case: the daemon writes the lock when it opens the device
  if (f.daemonRunning && !h) return false
  if (h && h.owner === 'daemon' && h.ours && !f.daemonRunning) return false
  if (f.daemonRunning && h && h.owner === 'daemon' && h.ours && h.mode !== f.daemonSession) return false
  // 'serving' and 'daemonRunning' are the same question asked of the same
  // endpoint: a daemon we can see serving is one the lock says is serving.
  if (h && h.owner === 'daemon' && h.ours && h.serving !== f.daemonRunning) return false
  if (f.daemonRunning && h && h.owner === 'daemon' && !h.serving) return false
  // A foreign daemon CAN be serving us: joining one is adoption, and then the
  // session is running while the lock still names them. Anything else foreign
  // cannot coexist with a daemon of ours.
  if (f.daemonRunning && h && !h.ours &&
      !(h.owner === 'daemon' && h.adoptable && h.mode === f.daemonSession)) return false
  if (h && h.mode === 'direct' && f.cortexRunning) return false
  if (h && h.mode === 'bridge' && !f.bridgeReady) return false
  if (h && h.mode === 'shared' && !f.cortexRunning) return false
  if (h && f.platform === 'win' && h.mode === 'bridge') return false
  if (h && f.platform === 'mac' && h.mode === 'shared') return false
  if (h && !f.devicePresent) return false
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
                for (const daemonSession of sessions)
                  for (const heldBy of HOLDERS) {
                    const f: Facts = {
                      platform, devicePresent, cortexInstalled, cortexRunning,
                      cortexInstrumented, instrumentedBuilt, bridgeReady,
                      daemonRunning: daemonSession !== null, daemonSession,
                      heldBy: heldBy && { ...heldBy }
                    }
                    if (coherent(f)) out.push(f)
                  }
  return out
}

export const say = (f: Facts): string =>
  `${f.platform} device=${f.devicePresent ? 'y' : 'n'} cc=${
    f.cortexRunning ? (f.cortexInstrumented ? 'instrumented' : 'stock') : 'closed'
  } built=${f.instrumentedBuilt ? 'y' : 'n'} bridge=${f.bridgeReady ? 'y' : 'n'} session=${
    f.daemonSession ?? '-'} lock=${
    f.heldBy
      ? `${f.heldBy.owner}/${f.heldBy.mode}${f.heldBy.ours ? '/ours' : ''}${
          f.heldBy.serving ? '' : '/UNREACHABLE'}`
      : '-'}`

/** Operations that move the world, and refuse what the real ones refuse. */
export function ops(w: World, log: string[] = []): SessionOps & { log: string[] } {
  const broke = (k: keyof SessionOps): boolean => w.breaks === k
  return {
    log,
    async stopDaemon() {
      log.push('stop-daemon')
      assert.ok(!w.heldBy || w.heldBy.ours,
        'stopped a session belonging to somebody else without taking over')
      w.daemonRunning = false
      w.daemonSession = null
      w.heldBy = null
    },
    async takeOver() {
      log.push('take-over')
      if (broke('takeOver')) return 'the other session would not stop'
      w.daemonRunning = false
      w.daemonSession = null
      w.heldBy = null
      return null
    },
    async startDaemon(session) {
      log.push(`start-daemon:${session}`)
      if (broke('startDaemon')) return 'the daemon refused to start'
      // A daemon already serving what we want is JOINED, not started again —
      // the socket is the join, and the lock keeps naming whoever owns it.
      if (w.heldBy && !w.heldBy.ours) {
        if (w.heldBy.owner === 'daemon' && w.heldBy.adoptable && w.heldBy.mode === session) {
          w.daemonRunning = true
          w.daemonSession = session
          return null
        }
        return 'the Quad Cortex is already held by another session'
      }
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
      assert.equal(w.heldBy, null, 'opened a session while somebody still held the device')
      w.daemonRunning = true
      w.daemonSession = session
      w.heldBy = daemonLock(session, true, true)
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
