import { spawn } from 'node:child_process'
import type { Paths } from '../shared/types.js'
import { IS_MAC, bridgeScript, instrumentedApp } from './paths.js'
import { exists, run } from './util.js'

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
export async function focus(paths: Paths): Promise<void> {
  if (IS_MAC) await run('open', ['-a', paths.cortex], { timeout: 5000 })
}
