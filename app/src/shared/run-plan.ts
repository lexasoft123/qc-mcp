/**
 * Running a plan.
 *
 * Separate from the deciding so both halves can be tested without the other:
 * planFor() against every combination of facts, this against a fake world that
 * can fail any step. It stops at the first failure and says which step it was,
 * because "connect failed" over a five-step sequence is what made the old
 * behaviour impossible to reason about.
 */

import type { SessionMode } from './types.js'
import type { Plan, Step } from './session.js'

export interface SessionOps {
  stopDaemon(): Promise<void>
  /** Resolves to an error sentence, or null when the daemon is serving. */
  startDaemon(session: SessionMode): Promise<string | null>
  quitCortex(): Promise<void>
  launchBridge(): Promise<string | null>
  launchStock(): Promise<string | null>
  /** False on timeout — a slow launch, not a crash. */
  awaitBridge(): Promise<boolean>
  awaitCortex(): Promise<boolean>
  focusCortex(): Promise<void>
  /** Progress, one line per step. */
  note?(label: string): void
}

export interface Outcome {
  ok: boolean
  error: string | null
  /** The steps that actually ran, in order — what the tests assert on. */
  ran: Step[]
}

const LABEL: Record<Step['do'], string> = {
  'stop-daemon': 'Closing the current session',
  'quit-cortex': 'Quitting Cortex Control',
  'launch-bridge': 'Opening the instrumented Cortex Control',
  'launch-stock': 'Opening Cortex Control',
  'await-bridge': 'Waiting for the bridge',
  'await-cortex': 'Waiting for Cortex Control',
  'start-daemon': 'Starting the daemon',
  'focus-cortex': 'Bringing Cortex Control forward'
}

export async function runPlan(plan: Plan, ops: SessionOps): Promise<Outcome> {
  if (plan.blocked) return { ok: false, error: plan.blocked, ran: [] }
  const ran: Step[] = []
  const fail = (error: string): Outcome => ({ ok: false, error, ran })

  for (const step of plan.steps) {
    ops.note?.(LABEL[step.do])
    ran.push(step)
    switch (step.do) {
      case 'stop-daemon':
        await ops.stopDaemon()
        break
      case 'quit-cortex':
        await ops.quitCortex()
        break
      case 'launch-bridge': {
        const err = await ops.launchBridge()
        if (err) return fail(err)
        break
      }
      case 'launch-stock': {
        const err = await ops.launchStock()
        if (err) return fail(err)
        break
      }
      case 'await-bridge':
        if (!(await ops.awaitBridge())) {
          return fail('Cortex Control did not open in time. Try again, or switch to Direct.')
        }
        break
      case 'await-cortex':
        if (!(await ops.awaitCortex())) {
          return fail('Cortex Control did not open in time. Try again, or switch to Direct.')
        }
        break
      case 'start-daemon': {
        const err = await ops.startDaemon(step.session)
        if (err) return fail(err)
        break
      }
      case 'focus-cortex':
        await ops.focusCortex()
        break
    }
  }
  return { ok: true, error: null, ran }
}
