import { test } from 'node:test'
import assert from 'node:assert/strict'
import { singleFlight } from '../src/shared/once.ts'

/**
 * The guard that stops a two-second poll re-entering a twenty-second plan.
 * It killed Cortex Control nine times in four minutes; it gets a test.
 */

const tick = (ms = 5): Promise<void> => new Promise((r) => setTimeout(r, ms))

test('concurrent callers asking the same thing run the body once', async () => {
  let runs = 0
  const f = singleFlight(async () => { runs++; await tick(20); return runs })
  const all = await Promise.all([f(), f(), f(), f()])
  assert.equal(runs, 1)
  assert.deepEqual(all, [1, 1, 1, 1])
})

test('a DIFFERENT request waits its turn instead of stealing the answer', async () => {
  // Pressing Disconnect during a twenty-second connect must disconnect, not be
  // handed the connect's promise and told it succeeded.
  const order: string[] = []
  const f = singleFlight(
    async (what: string) => { order.push(`start:${what}`); await tick(30); order.push(`end:${what}`); return what },
    (what) => what
  )
  const a = f('connect')
  const b = f('disconnect')
  assert.deepEqual(await Promise.all([a, b]), ['connect', 'disconnect'])
  assert.deepEqual(order, ['start:connect', 'end:connect', 'start:disconnect', 'end:disconnect'])
})

test('a failed request does not cancel the one queued behind it', async () => {
  const f = singleFlight(
    async (what: string) => { await tick(10); if (what === 'bad') throw new Error('no'); return what },
    (what) => what
  )
  const bad = f('bad')
  const good = f('good')
  await assert.rejects(bad)
  assert.equal(await good, 'good')
})

test('busy() stays true while anything is queued behind the run', async () => {
  const f = singleFlight(async (what: string) => { await tick(25); return what }, (w) => w)
  const a = f('one')
  const b = f('two')
  assert.equal(f.busy(), true)
  await a
  assert.equal(f.busy(), true, 'the queued request is still pending')
  await b
  assert.equal(f.busy(), false)
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
  const connect = singleFlight(async () => { launches++; await tick(200) }, () => 'connect')
  const poll = setInterval(() => { if (!connect.busy()) void connect() }, 10)
  void connect()
  await tick(150)
  clearInterval(poll)
  await tick(120)
  assert.equal(launches, 1, `Cortex Control would have been launched ${launches} times`)
})
