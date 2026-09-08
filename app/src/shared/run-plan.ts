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
  /** End whoever the session lock names. Resolves to an error sentence when it
   *  will not go — never pretend a stubborn owner is gone. */
  takeOver(): Promise<string | null>
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
  'take-over': 'Ending the session that holds the device',
  'quit-cortex': 'Quitting Cortex Control',
  'launch-bridge': 'Opening the instrumented Cortex Control',
  'launch-stock': 'Opening Cortex Control',
  'await-bridge': 'Waiting for the bridge',
  'await-cortex': 'Waiting for Cortex Control',
  'start-daemon': 'Starting the daemon',
  'focus-cortex': 'Bringing Cortex Control forward'
}

/**
 * Every step is bounded, and the whole plan is bounded again on top.
 *
 * A step that never settles used to leave the app saying "starting…" for ever,
 * with no way to tell which step it was stuck in — the one failure mode where
 * the interface has nothing true to show. A plan that overruns is reported as
 * an overrun, naming the step.
 */
export const STEP_TIMEOUT_MS: Record<Step['do'], number> = {
  'stop-daemon': 30_000,
  'take-over': 30_000,
  'quit-cortex': 30_000,
  'launch-bridge': 30_000,
  'launch-stock': 30_000,
  'await-bridge': 90_000,
  'await-cortex': 45_000,
  'start-daemon': 90_000,
  'focus-cortex': 15_000
}

const TIMED_OUT = Symbol('timed out')

/** A step that overran, or one that threw — never an escaped exception. */
const THREW = Symbol('threw')

async function within<T>(
  ms: number, work: Promise<T>
): Promise<T | typeof TIMED_OUT | { [THREW]: unknown }> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const bell = new Promise<typeof TIMED_OUT>((r) => { timer = setTimeout(() => r(TIMED_OUT), ms) })
  try {
    return await Promise.race([work, bell])
  } catch (e) {
    // An op that throws is a step that failed, and the plan says so. Letting it
    // escape turns a legible failure into a raw IPC rejection, with the session
    // left in whatever state the step reached and nothing on screen about it.
    return { [THREW]: e }
  } finally {
    if (timer) clearTimeout(timer)
  }
}

const threw = (v: unknown): v is { [THREW]: unknown } =>
  typeof v === 'object' && v !== null && THREW in v

const because = (step: Step, e: unknown): string => {
  const msg = e instanceof Error ? e.message : String(e)
  return `${LABEL[step.do]} failed: ${msg || 'no reason given'}`
}

export async function runPlan(plan: Plan, ops: SessionOps): Promise<Outcome> {
  if (plan.blocked) return { ok: false, error: plan.blocked, ran: [] }
  const ran: Step[] = []
  const fail = (error: string): Outcome => ({ ok: false, error, ran })
  const guard = <T>(step: Step, work: Promise<T>): Promise<T | typeof TIMED_OUT | { [THREW]: unknown }> =>
    within(STEP_TIMEOUT_MS[step.do], work)

  for (const step of plan.steps) {
    ops.note?.(LABEL[step.do])
    ran.push(step)
    switch (step.do) {
      case 'stop-daemon':
      case 'quit-cortex':
      case 'focus-cortex': {
        const call = step.do === 'stop-daemon' ? ops.stopDaemon()
          : step.do === 'quit-cortex' ? ops.quitCortex() : ops.focusCortex()
        const r = await guard(step, call)
        if (r === TIMED_OUT) return fail(overran(step))
        if (threw(r)) return fail(because(step, r[THREW]))
        break
      }
      case 'take-over': {
        const err = await guard(step, ops.takeOver())
        if (err === TIMED_OUT) return fail(overran(step))
        if (threw(err)) return fail(because(step, err[THREW]))
        if (err) return fail(err)
        break
      }
      case 'launch-bridge':
      case 'launch-stock': {
        const call = step.do === 'launch-bridge' ? ops.launchBridge() : ops.launchStock()
        const err = await guard(step, call)
        if (err === TIMED_OUT) return fail(overran(step))
        if (threw(err)) return fail(because(step, err[THREW]))
        if (err) return fail(err)
        break
      }
      case 'await-bridge':
      case 'await-cortex': {
        const call = step.do === 'await-bridge' ? ops.awaitBridge() : ops.awaitCortex()
        const ok = await guard(step, call)
        if (ok === TIMED_OUT) return fail(overran(step))
        if (threw(ok)) return fail(because(step, ok[THREW]))
        if (!ok) return fail('Cortex Control did not open in time. Try again, or switch to Direct.')
        break
      }
      case 'start-daemon': {
        const err = await guard(step, ops.startDaemon(step.session))
        if (err === TIMED_OUT) return fail(overran(step))
        if (threw(err)) return fail(because(step, err[THREW]))
        if (err) return fail(err)
        break
      }
    }
  }
  return { ok: true, error: null, ran }
}

const overran = (step: Step): string =>
  `${LABEL[step.do]} did not finish within ${Math.round(STEP_TIMEOUT_MS[step.do] / 1000)}s. ` +
  'Nothing was left half-open; try again.'
