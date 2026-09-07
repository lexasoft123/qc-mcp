import { spawn } from 'node:child_process'
import type { Paths } from '../shared/types.js'
import { BRIDGE_FIFOS, IS_MAC, bridgeScript, instrumentedApp } from './paths.js'
import { cortexPid } from './system.js'
import { exists, ps, run, sleep } from './util.js'

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
 * Launch the STOCK app, deliberately.
 *
 * Windows has no other kind, and on macOS this is the fallback when no
 * instrumented copy has been built — bridge mode will not work against it, and
 * the plan says so before getting here rather than discovering it later.
 */
export async function launchStock(paths: Paths): Promise<string | null> {
  if (!exists(paths.cortex)) return 'Cortex Control is not installed.'
  if (IS_MAC) spawn('open', ['-a', paths.cortex], { detached: true, stdio: 'ignore' }).unref()
  else spawn(paths.cortex, [], { detached: true, stdio: 'ignore' }).unref()
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

export async function quit(): Promise<void> {
  if (IS_MAC) {
    await run('osascript', ['-e', 'quit app "Cortex Control"'], { timeout: 10000 })
    // the instrumented copy is a separate bundle and ignores the AppleScript
    await run('pkill', ['-f', 'CortexControl-instrumented.app'], { timeout: 5000 })
    return
  }
  await run('taskkill', ['/IM', 'Cortex Control.exe'], { timeout: 10000 })
}

/**
 * Bring the running app forward — BY PID, never by bundle path.
 *
 * `open -a "/Applications/.../Cortex Control.app"` was launching a SECOND,
 * stock instance whenever the instrumented copy was the one up: two apps, two
 * device handles, and a bridge session that then died under the daemon. Ask the
 * window server to raise the process that is actually running instead.
 */
export async function focus(pid: number | null): Promise<void> {
  if (pid === null) return
  if (IS_MAC) {
    await run('osascript', ['-e',
      `tell application "System Events" to set frontmost of (first process whose unix id is ${pid}) to true`
    ], { timeout: 5000 })
    return
  }
  await ps(`(Get-Process -Id ${pid} -ErrorAction SilentlyContinue) | ForEach-Object {` +
           ` Add-Type -AssemblyName Microsoft.VisualBasic;` +
           ` [Microsoft.VisualBasic.Interaction]::AppActivate($_.Id) }`)
}

/** Is a Cortex Control up, and which build? Re-exported so the plan has one source. */
export { cortexPid } from './system.js'
