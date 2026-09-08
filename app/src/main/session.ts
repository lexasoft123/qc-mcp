import type { Goal, Facts, Plan } from '../shared/session.js'
import type { Outcome, SessionOps } from '../shared/run-plan.js'
import type { Mode, Snapshot } from '../shared/types.js'
import { planFor } from '../shared/session.js'
import { runPlan } from '../shared/run-plan.js'
import { singleFlight } from '../shared/once.js'
import * as cortex from './cortex.js'
import * as state from './state.js'
import { IS_MAC, PLATFORM } from './paths.js'
import { sleep } from './util.js'

/**
 * Wiring the decision table to the machine.
 *
 * Everything here is effects. The choosing is in shared/session.ts, where both
 * the renderer and the tests can reach it, so an interface that says "this will
 * share Cortex Control's session" and a main process that then seizes the
 * device cannot happen: they are reading the same function.
 */

/** Facts from a snapshot — the renderer's view and this one agree by construction. */
export function factsFrom(s: Snapshot, bridgeReady: boolean): Facts {
  return {
    platform: s.platform,
    devicePresent: s.device.present,
    cortexInstalled: s.cortex.installed,
    cortexRunning: s.cortex.running,
    cortexInstrumented: Boolean(s.cortex.runningInstrumented),
    instrumentedBuilt: Boolean(s.cortex.instrumented?.built),
    bridgeReady,
    daemonRunning: s.daemon.state === 'running',
    daemonSession: s.daemon.session
  }
}

export async function facts(): Promise<Facts> {
  const snap = await state.refresh()
  const ready = IS_MAC ? await cortex.bridgeReady(state.getPaths().repo) : snap.cortex.running
  return factsFrom(snap, ready)
}

/** What would happen, without doing it. Used for the plan the interface shows. */
export async function preview(goal: Goal, mode?: Mode): Promise<Plan> {
  const f = await facts()
  return planFor(goal, mode ?? state.getPrefs().mode, f, state.getPrefs().quitApp)
}

function opsWith(note: (label: string) => void): SessionOps {
  const paths = (): ReturnType<typeof state.getPaths> => state.getPaths()
  return {
    note,
    stopDaemon: async () => { await state.getDaemon().stop(); await state.push() },
    startDaemon: async (session) => {
      const d = state.getDaemon()
      // The daemon resolves `auto` itself; hand it the session we decided on so
      // the two cannot disagree about what was opened.
      d.setMode(session === 'shared' ? 'bridge' : session)
      await d.start(() => void state.push())
      const info = d.info()
      if (info.state !== 'running') return info.error ?? 'The daemon did not start.'
      return null
    },
    quitCortex: async () => { await cortex.quit(paths().repo); await state.push() },
    launchBridge: async () => cortex.launch(paths()),
    launchStock: async () => cortex.launchStock(paths()),
    // `await-bridge` only ever follows a `launch-bridge` (the plan pairs them),
    // so the boot-storm settle always applies exactly where the app is new.
    awaitBridge: async () => cortex.waitForBridge(paths().repo, 60000, cortex.BOOT_SETTLE_MS),
    awaitCortex: async () => {
      for (let i = 0; i < 60; i++) {
        if ((await cortex.cortexPid(paths().repo)).pid !== null) return true
        await sleep(500)
      }
      return false
    },
    focusCortex: async () => {
      const { pid } = await cortex.cortexPid(paths().repo)
      await cortex.focus(pid)
    }
  }
}

/**
 * Decide, then do it. The one entry point every button goes through — and one
 * at a time, because the steps ahead of `start-daemon` take twenty seconds and
 * the poll behind them runs every two.
 */
export const pursue = singleFlight(async (
  goal: Goal,
  mode: Mode | undefined,
  note: (label: string) => void
): Promise<Outcome> => {
  state.setBusy(true)
  const started = Date.now()
  const at = (m: string): void => console.log(`[session] ${goal}: ${m} (+${Date.now() - started}ms)`)
  try {
    const prefs = state.getPrefs()
    const f = await facts()
    const plan = planFor(goal, mode ?? prefs.mode, f, prefs.quitApp)
    at(plan.blocked ? `blocked: ${plan.blocked}`
       : plan.satisfied ? 'already satisfied'
       : `plan ${plan.steps.map((s2) => s2.do).join(' -> ')}`)
    if (plan.satisfied) return { ok: true, error: null, ran: [] }
    await state.push()
    const out = await runPlan(plan, opsWith((label) => { at(label); note(label) }))
    at(out.ok ? 'done' : `failed: ${out.error}`)
    return out
  } finally {
    state.setBusy(false)
    await state.push(true)
  }
})

/** Is a plan running? The poll must not start a second one behind it. */
export const busy = (): boolean => pursue.busy()

export const platform = PLATFORM
