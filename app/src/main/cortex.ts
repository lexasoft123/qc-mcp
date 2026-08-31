import { spawn } from 'node:child_process'
import type { Paths } from '../shared/types.js'
import { BRIDGE_FIFOS, IS_MAC, bridgeScript, instrumentedApp } from './paths.js'
import { cortexPid } from './system.js'
import { exists, run, sleep } from './util.js'

/**
 * Launch Cortex Control.
 *
 * On macOS bridge mode needs the INSTRUMENTED copy — run-bridge.sh injects the
 * interposer and opens the two FIFOs the daemon rides. On Windows there is no
 * interposer at all, so the stock app is launched as-is and the daemon opens
 * its own non-exclusive handle beside it.
 */
export async function launch(paths: Paths): Promise<string | null> {
  if (IS_MAC) {
    const script = bridgeScript(paths.repo)
    if (exists(instrumentedApp(paths.repo)) && exists(script)) {
      spawn(script, [], { cwd: paths.repo, detached: true, stdio: 'ignore' }).unref()
      return null
    }
    if (!exists(paths.cortex)) return 'Cortex Control is not installed.'
    // no instrumented copy: the stock app still works, bridge mode will not
    spawn('open', ['-a', paths.cortex], { detached: true, stdio: 'ignore' }).unref()
    return null
  }
  if (!exists(paths.cortex)) return 'Cortex Control is not installed.'
  spawn(paths.cortex, [], { detached: true, stdio: 'ignore' }).unref()
  return null
}

/**
 * Is the bridge actually usable? The same test the daemon makes when it picks
 * its own mode (server._bridge_running): both FIFOs present AND the
 * instrumented app alive.
 */
export async function bridgeReady(repo: string): Promise<boolean> {
  if (!IS_MAC) return false
  if (!BRIDGE_FIFOS.every(exists)) return false
  return (await cortexPid(repo)).instrumented
}

/**
 * Wait for it. launch() only spawns run-bridge.sh and returns, but the
 * instrumented app takes ~20s cold to come up and open the FIFOs — and the
 * daemon chooses bridge vs direct ONCE, at startup. Starting the daemon into a
 * half-open bridge silently gets direct mode, which seizes the device and
 * leaves the interposer we just built unused.
 *
 * Returns false on timeout rather than throwing: direct mode still works, so a
 * slow launch should degrade, not fail.
 */
export async function waitForBridge(repo: string, timeoutMs = 45000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await bridgeReady(repo)) return true
    await sleep(500)
  }
  return false
}

/**
 * Bring a RUNNING Cortex Control to the front. macOS only, and deliberately so:
 * quitting it there would kill the app whose session bridge mode rides, so the
 * launcher offers this instead. Windows has no session to protect and offers
 * quit, like the Console page does.
 */
export async function focus(paths: Paths): Promise<void> {
  if (IS_MAC) await run('open', ['-a', paths.cortex], { timeout: 5000 })
}

export async function quit(): Promise<void> {
  if (IS_MAC) {
    await run('osascript', ['-e', 'quit app "Cortex Control"'], { timeout: 10000 })
    // the instrumented copy is a separate bundle and ignores the AppleScript
    await run('pkill', ['-f', 'CortexControl-instrumented.app'], { timeout: 5000 })
    return
  }
  await run('taskkill', ['/IM', 'Cortex Control.exe'], { timeout: 10000 })
}

/** Bring the running app forward without touching its session. */
