import { test } from 'node:test'
import assert from 'node:assert/strict'
import { modeSwitch, planFor, resolveSession } from '../src/shared/session.ts'
import { runPlan } from '../src/shared/run-plan.ts'
import type { Facts, Goal } from '../src/shared/session.ts'
import type { Mode } from '../src/shared/types.ts'
import { GOALS, MODES, allWorlds, ops, say, stepNames, type World } from './world.ts'

/**
 * Open, connect, switch mode — every combination of them.
 *
 * The bug reports this suite exists for were all the same shape: a sequence of
 * presses nobody had thought about, ending with the app describing a session it
 * did not have. So the test does not press a few buttons in a sensible order —
 * it enumerates every state the machine can be in, and for each one checks both
 * what the plan SAYS and what running it DOES.
 */

const WORLDS = allWorlds()
const each = (fn: (f: Facts) => void): void => {
  for (const f of WORLDS) {
    try { fn(f) } catch (e) {
      (e as Error).message = `${(e as Error).message}\n  world: ${say(f)}`
      throw e
    }
  }
}

test(`the state space is complete (${WORLDS.length} coherent worlds)`, () => {
  // 26 on macOS and 9 on Windows — every arrangement of device, app build,
  // bridge and session that the machine can actually be in. Pinned, so that
  // widening the facts without widening the enumeration is caught here.
  assert.equal(WORLDS.length, 63)
  assert.equal(WORLDS.filter((f) => f.platform === 'mac').length, 46)
  assert.equal(WORLDS.filter((f) => f.platform === 'win').length, 17)
  assert.ok(WORLDS.some((f) => f.heldBy?.owner === 'mcp'), 'no MCP-held world')
  assert.ok(WORLDS.some((f) => f.heldBy && !f.heldBy.ours), 'no foreign-owner world')
  assert.ok(WORLDS.some((f) => f.heldBy?.ours), 'no world we own')
  assert.ok(WORLDS.some((f) => f.heldBy === null), 'no unheld world')
  for (const s of ['bridge', 'shared', 'direct'] as const) {
    assert.ok(WORLDS.some((f) => f.daemonSession === s), `no ${s} session in the space`)
  }
  assert.ok(WORLDS.some((f) => f.cortexRunning && !f.cortexInstrumented), 'no stock-app world')
  assert.ok(WORLDS.some((f) => f.cortexInstrumented), 'no instrumented-app world')
})

// ── what the plan says ────────────────────────────────────────────────────

test('a plan is either blocked or actionable, never both', () => {
  each((f) => {
    for (const mode of MODES) for (const goal of GOALS) {
      const p = planFor(goal, mode, f)
      if (p.blocked) assert.deepEqual(p.steps, [], 'blocked plan carried steps')
      if (p.satisfied) assert.deepEqual(p.steps, [], 'satisfied plan carried steps')
    }
  })
})

test('connect is blocked with no device, and never otherwise for want of one', () => {
  each((f) => {
    for (const mode of MODES) {
      const p = planFor('connect', mode, f)
      if (!f.devicePresent) assert.ok(p.blocked, 'planned a connect with no device')
      else assert.notEqual(p.blocked, 'Plug the Quad Cortex in over USB.')
    }
  })
})

test('connect opens exactly one daemon, and it is the last thing it does', () => {
  each((f) => {
    for (const mode of MODES) {
      const steps = stepNames(planFor('connect', mode, f))
      const starts = steps.filter((s) => s === 'start-daemon')
      if (steps.length === 0) continue
      assert.equal(starts.length, 1, `${starts.length} start-daemon steps`)
      assert.equal(steps.at(-1), 'start-daemon', 'daemon started before the world was ready')
    }
  })
})

test('a session that is open and wrong is torn down before another is opened', () => {
  each((f) => {
    for (const mode of MODES) {
      const p = planFor('connect', mode, f)
      if (!p.steps.length) continue
      const steps = stepNames(p)
      if (f.daemonRunning) {
        assert.equal(steps[0], 'stop-daemon', 'started a second session over a live one')
      } else {
        assert.ok(!steps.includes('stop-daemon'), 'stopped a daemon that was not running')
      }
    }
  })
})

test('the session a connect reaches is the one the mode resolves to', () => {
  each((f) => {
    for (const mode of MODES) {
      const p = planFor('connect', mode, f)
      if (p.blocked) continue
      assert.equal(p.session, resolveSession(mode, f))
    }
  })
})

test('direct never starts while Cortex Control still holds the device', () => {
  each((f) => {
    for (const mode of MODES) {
      const p = planFor('connect', mode, f)
      if (p.session !== 'direct' || !p.steps.length) continue
      if (!f.cortexRunning) continue
      const steps = stepNames(p)
      assert.ok(steps.indexOf('quit-cortex') >= 0, 'seized a device Cortex Control was holding')
      assert.ok(steps.indexOf('quit-cortex') < steps.indexOf('start-daemon'))
    }
  })
})

test('a shared session never quits the app it is about to share', () => {
  each((f) => {
    for (const mode of MODES) {
      const p = planFor('connect', mode, f)
      if (p.session === 'direct') continue
      assert.ok(!stepNames(p).includes('quit-cortex'), 'quit the app the session rides')
    }
  })
})

test('bridge without an instrumented copy is refused, not attempted', () => {
  each((f) => {
    if (f.platform !== 'mac' || f.instrumentedBuilt) return
    if (!f.devicePresent) return
    const p = planFor('connect', 'bridge', f)
    assert.ok(p.blocked, 'planned a bridge with nothing to bridge through')
    // Somebody else holding the device is the nearer obstacle and is named
    // first; the missing copy is what the take-over would then run into.
    if (f.heldBy && !f.heldBy.ours) return
    assert.match(p.blocked, /instrumented/i)
  })
})

test('Windows never reaches for the interposer', () => {
  each((f) => {
    if (f.platform !== 'win') return
    for (const mode of MODES) for (const goal of GOALS) {
      const steps = stepNames(planFor(goal, mode, f))
      assert.ok(!steps.includes('launch-bridge'), 'launched the bridge script on Windows')
      assert.ok(!steps.includes('await-bridge'), 'waited for a bridge on Windows')
    }
    assert.notEqual(resolveSession('bridge', f), 'bridge')
  })
})

test('auto starts no application that was not already running', () => {
  each((f) => {
    const p = planFor('connect', 'auto', f)
    if (f.cortexRunning) return
    const steps = stepNames(p)
    assert.ok(!steps.includes('launch-bridge'), 'auto opened Cortex Control unasked')
    assert.ok(!steps.includes('launch-stock'), 'auto opened Cortex Control unasked')
  })
})

test('auto and the daemon agree on what auto means', () => {
  // qc_mcp/daemon.py serve(): a live bridge (or an app holding the device) is
  // shared; an idle machine is taken directly. The launcher used to disagree.
  each((f) => {
    const want = f.bridgeReady || f.cortexRunning
      ? (f.platform === 'win' ? 'shared' : 'bridge')
      : 'direct'
    assert.equal(resolveSession('auto', f), want)
  })
})

// ── Show Cortex Control ───────────────────────────────────────────────────

test('Show Cortex Control never launches a second copy over a running one', () => {
  each((f) => {
    for (const mode of MODES) {
      const steps = stepNames(planFor('show-app', mode, f))
      if (!f.cortexRunning) continue
      assert.deepEqual(steps, ['focus-cortex'],
        'launched an app bundle while one was already running')
    }
  })
})

test('Show Cortex Control opens the instrumented build wherever bridge mode could use it', () => {
  each((f) => {
    if (f.platform !== 'mac' || f.cortexRunning || !f.instrumentedBuilt) return
    if (f.daemonSession === 'direct') return   // covered below: it is refused
    for (const mode of ['auto', 'bridge'] as Mode[]) {
      assert.ok(stepNames(planFor('show-app', mode, f)).includes('launch-bridge'),
        'opened the stock app when the instrumented one was available')
    }
    // Direct mode has no use for the interposer, so it opens the plain app —
    // when there is one to open.
    const direct = planFor('show-app', 'direct', f)
    if (f.cortexInstalled) assert.deepEqual(stepNames(direct), ['launch-stock'])
    else assert.ok(direct.blocked)
  })
})

test('Show Cortex Control is refused while a direct session holds the device', () => {
  // Launching Cortex Control into a device the daemon owns exclusively kills
  // it — EXC_BAD_ACCESS on its message thread, nine seconds after launch. It
  // is not a thing to attempt and recover from; it is a thing not to do.
  each((f) => {
    if (f.daemonSession !== 'direct') return
    for (const mode of MODES) {
      const p = planFor('show-app', mode, f)
      assert.ok(p.blocked, 'planned to launch Cortex Control over a direct session')
      assert.match(p.blocked, /Disconnect first|switch to Bridge/i)
    }
  })
})

test('Show Cortex Control never launches into a device somebody else holds', async () => {
  for (const f of WORLDS) {
    for (const mode of MODES) {
      const plan = planFor('show-app', mode, f)
      if (plan.blocked) continue
      const launches = stepNames(plan).some((s) => s.startsWith('launch'))
      if (!launches) continue
      assert.notEqual(f.daemonSession, 'direct', `${say(f)} · mode=${mode}`)
    }
  }
})

test('Show Cortex Control never touches the daemon', () => {
  each((f) => {
    for (const mode of MODES) {
      const steps = stepNames(planFor('show-app', mode, f))
      assert.ok(!steps.includes('start-daemon'))
      assert.ok(!steps.includes('stop-daemon'))
      assert.ok(!steps.includes('quit-cortex'))
    }
  })
})

// ── disconnect ────────────────────────────────────────────────────────────

test('disconnect stops what is running and nothing else', () => {
  each((f) => {
    for (const quitApp of [false, true]) {
      const steps = stepNames(planFor('disconnect', 'auto', f, quitApp))
      const ends = steps.includes('stop-daemon') || steps.includes('take-over')
      assert.equal(ends, f.daemonRunning)
      assert.equal(steps.includes('quit-cortex'), quitApp && f.cortexRunning)
      if (steps.includes('quit-cortex') && f.daemonRunning) {
        const first = Math.max(steps.indexOf('stop-daemon'), steps.indexOf('take-over'))
        assert.ok(first < steps.indexOf('quit-cortex'), 'quit the app before the session riding it')
      }
    }
  })
})

test("disconnecting somebody else's session is named an eviction", () => {
  // Not 'stop-daemon'. The step that ends a session Patchbay did not start says
  // so, in the plan and in the progress line, so it can never be mistaken for
  // ordinary housekeeping.
  each((f) => {
    if (!f.daemonRunning) return
    const steps = stepNames(planFor('disconnect', 'auto', f))
    const foreign = Boolean(f.heldBy && !f.heldBy.ours)
    assert.equal(steps.includes('take-over'), foreign, say(f))
    assert.equal(steps.includes('stop-daemon'), !foreign, say(f))
  })
})

// ── what running it actually does ─────────────────────────────────────────

const play = async (f: Facts, goal: Goal, mode: Mode, quitApp = false): Promise<{
  world: World; ran: string[]; ok: boolean; error: string | null
}> => {
  const world: World = { ...f }
  const o = ops(world)
  const plan = planFor(goal, mode, f, quitApp)
  const out = plan.satisfied || plan.blocked
    ? { ok: !plan.blocked, error: plan.blocked, ran: [] }
    : await runPlan(plan, o)
  return { world, ran: o.log, ok: out.ok, error: out.error }
}

test('connect: every world, every mode, ends in the session it promised', async () => {
  for (const f of WORLDS) {
    if (!f.devicePresent) continue
    for (const mode of MODES) {
      const plan = planFor('connect', mode, f)
      const r = await play(f, 'connect', mode)
      const where = `${say(f)} · mode=${mode} · ran=[${r.ran}]`
      if (plan.blocked) { assert.equal(r.ok, false, where); continue }
      assert.equal(r.ok, true, `${where}\n  failed: ${r.error}`)
      assert.equal(r.world.daemonRunning, true, where)
      assert.equal(r.world.daemonSession, plan.session, where)
    }
  }
})

test('connect converges: doing it twice changes nothing the second time', async () => {
  for (const f of WORLDS) {
    if (!f.devicePresent) continue
    for (const mode of MODES) {
      const first = planFor('connect', mode, f)
      if (first.blocked) continue
      const r = await play(f, 'connect', mode)
      const again = planFor('connect', mode, r.world)
      assert.ok(again.satisfied, `re-planned ${stepNames(again)} on ${say(r.world)}`)
    }
  }
})

test('switching mode from any live session reaches the new one', async () => {
  for (const f of WORLDS) {
    if (!f.devicePresent) continue
    for (const from of MODES) {
      const opened = planFor('connect', from, f)
      if (opened.blocked) continue
      const a = await play(f, 'connect', from)
      for (const to of MODES) {
        const plan = planFor('connect', to, a.world)
        const b = await play(a.world, 'connect', to)
        const where = `${say(f)} · ${from}→${to} · ran=[${b.ran}]`
        if (plan.blocked) { assert.equal(b.ok, false, where); continue }
        assert.equal(b.ok, true, `${where}\n  failed: ${b.error}`)
        assert.equal(b.world.daemonSession, plan.session, where)
      }
    }
  }
})

test('disconnect leaves nothing running, then reconnect works', async () => {
  for (const f of WORLDS) {
    if (!f.devicePresent) continue
    for (const quitApp of [false, true]) {
      const d = await play(f, 'disconnect', 'auto', quitApp)
      assert.equal(d.world.daemonRunning, false, say(f))
      if (quitApp) assert.equal(d.world.cortexRunning, false, say(f))
      for (const mode of MODES) {
        const plan = planFor('connect', mode, d.world)
        const r = await play(d.world, 'connect', mode)
        if (plan.blocked) continue
        assert.equal(r.ok, true, `${say(f)} · reconnect ${mode}: ${r.error}`)
      }
    }
  }
})

test('Show Cortex Control, from any world, leaves exactly one app running', async () => {
  for (const f of WORLDS) {
    for (const mode of MODES) {
      const plan = planFor('show-app', mode, f)
      const r = await play(f, 'show-app', mode)
      if (plan.blocked) continue
      const where = `${say(f)} · mode=${mode} · ran=[${r.ran}]`
      if (r.ok) assert.equal(r.world.cortexRunning, true, where)
      // it must never start one while one is up — the fake asserts the focus
      // case, this catches the launch case
      if (f.cortexRunning) {
        assert.ok(!r.ran.some((s) => s.startsWith('launch')), where)
      }
    }
  }
})

test('a live bridge session is never left riding an app that was told to quit', async () => {
  // ops.quitCortex throws if it happens; this drives every path through it.
  for (const f of WORLDS) {
    for (const mode of MODES) for (const goal of GOALS) {
      for (const quitApp of [false, true]) {
        await play(f, goal, mode, quitApp)
      }
    }
  }
})

// ── when a step fails ─────────────────────────────────────────────────────

const BREAKABLE = ['startDaemon', 'launchBridge', 'launchStock', 'awaitBridge', 'awaitCortex'] as const

test('a failing step stops the plan and is reported, with no half-open session', async () => {
  for (const f of WORLDS) {
    if (!f.devicePresent) continue
    for (const mode of MODES) {
      for (const breaks of BREAKABLE) {
        const plan = planFor('connect', mode, f)
        if (plan.blocked || plan.satisfied) continue
        const world: World = { ...f, breaks }
        const o = ops(world)
        const out = await runPlan(plan, o)
        const where = `${say(f)} · ${mode} · broke ${breaks} · ran=[${o.log}]`
        if (out.ok) {
          // the broken op was not on this plan's path
          assert.ok(!o.log.some((s) => s.startsWith(stepFor(breaks))), where)
          continue
        }
        assert.ok(out.error && out.error.length > 0, `${where}: failed with no message`)
        // nothing may claim a session it did not open
        if (!o.log.some((s) => s.startsWith('start-daemon'))) {
          assert.equal(world.daemonRunning, false, `${where}: session appeared anyway`)
        }
        // and the plan must have stopped at the failure, not carried on
        assert.ok(out.ran.length <= plan.steps.length, where)
      }
    }
  }
})

const stepFor = (op: (typeof BREAKABLE)[number]): string => ({
  startDaemon: 'start-daemon', launchBridge: 'launch-bridge', launchStock: 'launch-stock',
  awaitBridge: 'await-bridge', awaitCortex: 'await-cortex'
})[op]

test('a failed connect can be retried, and the retry is a fresh decision', async () => {
  for (const f of WORLDS) {
    if (!f.devicePresent) continue
    for (const mode of MODES) {
      const plan = planFor('connect', mode, f)
      if (plan.blocked || plan.satisfied) continue
      const world: World = { ...f, breaks: 'startDaemon' }
      await runPlan(plan, ops(world))
      delete world.breaks
      const retry = planFor('connect', mode, world)
      if (retry.blocked || retry.satisfied) continue
      const o = ops(world)
      const out = await runPlan(retry, o)
      assert.equal(out.ok, true, `${say(f)} · ${mode} · retry failed: ${out.error} · [${o.log}]`)
      assert.equal(world.daemonSession, retry.session)
    }
  }
})

test('any three mode switches in a row, from any world, still land where they say', async () => {
  // Order-dependence is what the bug reports were made of: each press was fine
  // on its own. Every path of length three through the mode selector.
  for (const f of WORLDS) {
    if (!f.devicePresent) continue
    for (const a of MODES) for (const b of MODES) for (const c of MODES) {
      let world: World = { ...f }
      const trail: string[] = []
      for (const mode of [a, b, c]) {
        const plan = planFor('connect', mode, world)
        trail.push(`${mode}:${plan.blocked ? 'blocked' : plan.session}`)
        if (plan.blocked) continue
        const o = ops(world)
        const out = plan.satisfied ? { ok: true, error: null } : await runPlan(plan, o)
        const where = `${say(f)} · ${a}→${b}→${c} · at ${trail.join(' ')} · [${o.log}]`
        assert.equal(out.ok, true, `${where}\n  failed: ${out.error}`)
        assert.equal(world.daemonSession, plan.session, where)
      }
    }
  }
})

test('interleaving Show Cortex Control with mode switches breaks nothing', async () => {
  for (const f of WORLDS) {
    if (!f.devicePresent) continue
    for (const a of MODES) for (const b of MODES) {
      const world: World = { ...f }
      for (const [goal, mode] of [['connect', a], ['show-app', a], ['connect', b],
                                  ['show-app', b], ['disconnect', b]] as [Goal, Mode][]) {
        const plan = planFor(goal, mode, world, false)
        if (plan.blocked || plan.satisfied) continue
        const o = ops(world)
        const out = await runPlan(plan, o)
        const where = `${say(f)} · ${a}/${b} · ${goal} · [${o.log}]`
        assert.equal(out.ok, true, `${where}\n  failed: ${out.error}`)
      }
      assert.equal(world.daemonRunning, false, `${say(f)} · ${a}/${b}: disconnect left a session`)
    }
  }
})

test('the stock app is never opened on top of the instrumented one — the reported fault', async () => {
  // A world where run-bridge.sh has swapped in the instrumented copy, then
  // every button in turn. `open -a <stock bundle>` used to start a SECOND app.
  const live: Facts = {
    platform: 'mac', devicePresent: true, cortexInstalled: true, cortexRunning: true,
    cortexInstrumented: true, instrumentedBuilt: true, bridgeReady: true,
    daemonRunning: true, daemonSession: 'bridge',
    heldBy: { owner: 'daemon', mode: 'bridge', ours: true, adoptable: true }
  }
  for (const mode of MODES) {
    const r = await play(live, 'show-app', mode)
    assert.deepEqual(r.ran, ['focus-cortex'], `mode=${mode} ran ${r.ran}`)
    assert.equal(r.world.cortexInstrumented, true, 'the instrumented app was replaced')
    assert.equal(r.world.daemonSession, 'bridge', 'the session did not survive')
  }
})

// ── the mode selector ─────────────────────────────────────────────────────

test('changing the mode never opens an app or starts a daemon', () => {
  // The whole point of the switch being a preference: with nothing connected
  // it changes what Connect WILL do, and touches the machine not at all.
  each((f) => {
    const gate = modeSwitch(f)
    assert.equal(gate.allowed, !f.daemonRunning, say(f))
    if (gate.allowed) assert.equal(gate.why, null)
    else assert.match(gate.why!, /Disconnect first/)
  })
})

test('the mode cannot be changed under a live session', () => {
  each((f) => {
    if (!f.daemonRunning) {
      // ...and a session that is merely starting is live enough to lock it
      assert.equal(modeSwitch(f, true).allowed, false, say(f))
      return
    }
    assert.equal(modeSwitch(f).allowed, false, say(f))
  })
})

test('a locked switch leaves the session exactly as it was', async () => {
  for (const f of WORLDS) {
    if (!f.daemonRunning) continue
    const world: World = { ...f }
    for (const mode of MODES) {
      if (modeSwitch(world).allowed) assert.fail(`switch allowed on ${say(world)}`)
      // refused means nothing ran at all
      assert.deepEqual({ ...world }, { ...f }, `${say(f)} · ${mode} changed the world`)
    }
  }
})

test('waiting for the bridge is inseparable from launching it', () => {
  // The boot-storm settle lives on the await step. If a plan could ever launch
  // the app without then awaiting it, the daemon would attach mid-boot and kill
  // Cortex Control — which is precisely the crash this pairing prevents.
  each((f) => {
    for (const mode of MODES) for (const goal of GOALS) {
      const steps = stepNames(planFor(goal, mode, f))
      const li = steps.indexOf('launch-bridge')
      const ai = steps.indexOf('await-bridge')
      if (li >= 0) assert.equal(ai, li + 1, `launch-bridge without an await after it: ${steps}`)
      if (ai >= 0) assert.equal(li, ai - 1, `await-bridge without a launch before it: ${steps}`)
      const ls = steps.indexOf('launch-stock')
      const as = steps.indexOf('await-cortex')
      if (ls >= 0 && goal === 'connect') assert.equal(as, ls + 1, `launch-stock unawaited: ${steps}`)
    }
  })
})

test('the daemon is never started in the same breath as launching the app', () => {
  // start-daemon must be separated from any launch by the wait that lets the
  // app finish booting.
  each((f) => {
    for (const mode of MODES) {
      const steps = stepNames(planFor('connect', mode, f))
      const start = steps.indexOf('start-daemon')
      if (start < 0) continue
      const launched = steps.findIndex((s) => s.startsWith('launch'))
      if (launched < 0) continue
      assert.ok(steps.slice(launched, start).some((s) => s.startsWith('await')),
        `daemon started right after a launch, with no wait: ${steps}`)
    }
  })
})
