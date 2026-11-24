import { spawn } from 'node:child_process'
import type { CloudStatus, Paths } from '../shared/types.js'
import { exists } from './util.js'

type Action = 'status' | 'login' | 'logout'
const failure = (error: string): CloudStatus => ({ authenticated: false, session_persisted: false, error })

/** The Python backend owns the session; credentials never enter argv or logs. */
export function cloudAuth(paths: Paths, action: Action, credentials?: { email: string; password: string }): Promise<CloudStatus> {
  if (!exists(paths.python)) return Promise.resolve(failure('Finish Patchbay setup before signing in to Cortex Cloud.'))
  return new Promise((resolve) => {
    let done = false
    let out = ''
    const env: NodeJS.ProcessEnv = { ...process.env, PYTHONIOENCODING: 'utf-8' }
    // The account sheet always uses the shared OS-protected session, including
    // when Patchbay inherited old shell-based cloud configuration.
    for (const key of ['QC_CLOUD_PASSWORD', 'QC_CLOUD_EMAIL', 'QC_CLOUD_ACCESS_TOKEN', 'QC_CLOUD_SESSION_FILE']) delete env[key]
    const child = spawn(paths.python, ['-m', 'qc_mcp.cloud_auth', action, '--json'], {
      cwd: paths.repo,
      env,
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'ignore']
    })
    const finish = (result: CloudStatus): void => {
      if (done) return
      done = true
      clearTimeout(timer)
      resolve(result)
    }
    // A native credential-store permission prompt can need user interaction.
    const timer = setTimeout(() => {
      child.kill()
      finish(failure('Sign-in timed out. Check for a system credential-store prompt, then try again.'))
    }, 120000)
    child.on('error', () => finish(failure('Could not start Cortex Cloud sign-in. Run Patchbay setup and try again.')))
    child.stdin.on('error', () => { /* exit/error below reports a safe, useful message */ })
    child.stdout.setEncoding('utf8')
    child.stdout.on('data', (data: string) => {
      out += data
      if (out.length > 65536) {
        child.kill()
        finish(failure('Cortex Cloud sign-in returned an unexpected response.'))
      }
    })
    child.on('close', (code) => {
      if (done) return
      try {
        const data: unknown = JSON.parse(out)
        if (!data || typeof data !== 'object') throw new Error('invalid response')
        const value = data as Record<string, unknown>
        if (typeof value.authenticated !== 'boolean' && typeof value.error !== 'string') throw new Error('invalid response')
        finish({
          authenticated: value.authenticated === true,
          session_persisted: value.session_persisted === true,
          ...(typeof value.email === 'string' ? { email: value.email.slice(0, 320) } : {}),
          ...(typeof value.error === 'string' ? { error: value.error.slice(0, 500) } : {})
        })
      } catch {
        finish(failure(code === 0
          ? 'Cortex Cloud sign-in returned an unexpected response.'
          : 'Cortex Cloud sign-in is unavailable. Run Patchbay setup to update its backend.'))
      }
    })
    child.stdin.end(action === 'login' && credentials
      ? JSON.stringify({ email: credentials.email, password: credentials.password, remember: true })
      : '')
  })
}
