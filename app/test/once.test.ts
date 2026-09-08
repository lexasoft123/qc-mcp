import { test } from 'node:test'
import assert from 'node:assert/strict'
import { singleFlight } from '../src/shared/once.ts'

/**
 * The guard that stops a two-second poll re-entering a twenty-second plan.
 * It killed Cortex Control nine times in four minutes; it gets a test.
 */

const tick = (ms = 5): Promise<void> => new Promise((r) => setTimeout(r, ms))

test('concurrent callers run the body once and all get its result', async () => {
  let runs = 0
  const f = singleFlight(async () => { runs++; await tick(20); return runs })
  const all = await Promise.all([f(), f(), f(), f()])
  assert.equal(runs, 1)
  assert.deepEqual(all, [1, 1, 1, 1])
})

test('busy() is true for exactly as long as the body is running', async () => {
  const f = singleFlight(async () => { await tick(20) })
  assert.equal(f.busy(), false)
  const p = f()
  assert.equal(f.busy(), true)
  await p
  assert.equal(f.busy(), false)
})

test('the next call after it settles is a fresh run', async () => {
  let runs = 0
  const f = singleFlight(async () => { runs++; await tick(5); return runs })
  assert.equal(await f(), 1)
  assert.equal(await f(), 2)
})

test('a throwing body releases the flight rather than wedging it', async () => {
  let runs = 0
  const f = singleFlight(async () => { runs++; await tick(5); throw new Error('no') })
  await assert.rejects(f())
  assert.equal(f.busy(), false)
  await assert.rejects(f())
  assert.equal(runs, 2)
})

test('a poll firing every tick through a long run starts nothing', async () => {
  // The shape of the actual fault: a plan that takes 200ms, polled every 10ms.
  let launches = 0
  const connect = singleFlight(async () => { launches++; await tick(200) })
  const poll = setInterval(() => { if (!connect.busy()) void connect() }, 10)
  void connect()
  await tick(150)
  clearInterval(poll)
  await tick(120)
  assert.equal(launches, 1, `Cortex Control would have been launched ${launches} times`)
})
