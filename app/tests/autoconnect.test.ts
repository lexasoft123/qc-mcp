import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import {
  BRIDGE_RETRY_LIMIT, bridgeDiedYoung, shouldAutoconnect, type AutoFacts
} from '../src/shared/session.js'

const ready = (over: Partial<AutoFacts> = {}): AutoFacts => ({
  autoconnect: true,
  stayDisconnected: false,
  retriesExhausted: false,
  busy: false,
  devicePresent: true,
  daemonState: 'stopped',
  daemonError: false,
  daemonSupported: true,
  setupPending: false,
  ...over
})

describe('shouldAutoconnect', () => {
  it('connects when the device is there and nothing is in the way', () => {
    assert.equal(shouldAutoconnect(ready()), true)
  })

  it('DOES NOT reconnect after the user pressed Disconnect', () => {
    // The whole bug: the poll re-ran this on every tick with no memory of the
    // press, so Disconnect stopped the daemon and the next tick started it
    // again — visibly, three times in a row, with a segfault on each stop.
    assert.equal(shouldAutoconnect(ready({ stayDisconnected: true })), false)
  })

  it('stays out of the way while a plan is running', () => {
    assert.equal(shouldAutoconnect(ready({ busy: true })), false)
  })

  it('waits for a device', () => {
    assert.equal(shouldAutoconnect(ready({ devicePresent: false })), false)
  })

  it('leaves a live or starting session alone', () => {
    assert.equal(shouldAutoconnect(ready({ daemonState: 'running' })), false)
    assert.equal(shouldAutoconnect(ready({ daemonState: 'starting' })), false)
  })

  it('does not retry into a daemon that failed or is unsupported', () => {
    assert.equal(shouldAutoconnect(ready({ daemonError: true })), false)
    assert.equal(shouldAutoconnect(ready({ daemonSupported: false })), false)
  })

  it('lets the user finish setup first', () => {
    assert.equal(shouldAutoconnect(ready({ setupPending: true })), false)
  })

  it('honours the preference above everything else', () => {
    assert.equal(shouldAutoconnect(ready({ autoconnect: false })), false)
  })

  it('still reconnects after the device is unplugged and put back', () => {
    // A session dropped by the device going away is not a user decision, so
    // nothing sets stayDisconnected and the poll picks it up again.
    assert.equal(shouldAutoconnect(ready({ devicePresent: false })), false)
    assert.equal(shouldAutoconnect(ready({ devicePresent: true })), true)
  })
})

describe('the bridge retry budget', () => {
  it('stops reopening Cortex Control once the budget is spent', () => {
    // The loop this ends: the app segfaults, the poll sees the bridge gone,
    // stops the daemon, autoconnect launches the app again, it segfaults
    // again. Four launches and four crashes in two minutes, on a real run.
    assert.equal(shouldAutoconnect(ready({ retriesExhausted: true })), false)
  })

  it('counts a session that never really started', () => {
    const now = 1_000_000
    assert.equal(bridgeDiedYoung(now - 3_000, now), true)
    assert.equal(bridgeDiedYoung(null, now), true, 'never opened counts as young')
  })

  it('does not count a session that ran for a while', () => {
    const now = 1_000_000
    assert.equal(bridgeDiedYoung(now - 10 * 60_000, now), false)
  })

  it('gives up only after several failures, not the first', () => {
    assert.ok(BRIDGE_RETRY_LIMIT >= 2, 'one crash must not disable the mode')
  })
})
