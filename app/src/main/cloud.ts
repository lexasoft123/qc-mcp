import { spawn } from 'node:child_process'
import type { CloudCategory, CloudPresetDetails, CloudProductPage, CloudRequestError, CloudStatus, Paths } from '../shared/types.js'
import { exists } from './util.js'

type Action = 'status' | 'login' | 'logout'
const failure = (error: string): CloudStatus => ({ authenticated: false, session_persisted: false, error })

/** The Python backend owns the session; credentials never enter argv or logs. */
export async function cloudAuth(paths: Paths, action: Action, credentials?: { email: string; password: string }): Promise<CloudStatus> {
  const input = action === 'login' && credentials ? { ...credentials, remember: true } : undefined
  const args = action === 'status' ? [action, '--json', '--verify'] : [action, '--json']
  const value = await runCloud(paths, args, input)
  const error = typeof value.error === 'string' ? value.error.slice(0, 500) : undefined
  if (typeof value.authenticated !== 'boolean') return failure(error ?? 'Cortex Cloud returned an unexpected sign-in response.')
  return {
    authenticated: value.authenticated,
    session_persisted: value.session_persisted === true,
    ...(typeof value.email === 'string' ? { email: value.email.slice(0, 320) } : {}),
    ...(error ? { error } : {})
  }
}

export async function searchCloud(paths: Paths, category: CloudCategory, query: string, page: number): Promise<CloudProductPage> {
  const result = await runCloud(paths, ['search', '--json', '--category=' + category, '--query=' + query, '--page=' + page])
  if (Array.isArray(result.products) && typeof result.hasNext === 'boolean') {
    return { products: result.products, hasNext: result.hasNext }
  }
  return requestError(result, 'Cortex Cloud returned an unexpected search response.')
}

export async function cloudPresetDetails(paths: Paths, productId: string): Promise<CloudPresetDetails> {
  const result = await runCloud(paths, ['preset-details', '--json', '--product-id=' + productId])
  const { compatibility, blocks, scenes } = result
  if (Array.isArray(compatibility) && Array.isArray(blocks) && Array.isArray(scenes)) {
    return { compatibility, blocks, scenes }
  }
  return requestError(result, 'Cortex Cloud returned unexpected preset details.')
}

function requestError(result: Record<string, unknown>, fallback: string): CloudRequestError {
  return {
    error: typeof result.error === 'string' ? result.error.slice(0, 500) : fallback,
    ...(result.signInRequired === true ? { signInRequired: true } : {})
  }
}

function runCloud(paths: Paths, args: string[], input?: object): Promise<Record<string, unknown>> {
  if (!exists(paths.python)) return Promise.resolve({ error: 'Finish Patchbay setup before using Cortex Cloud.' })
  return new Promise((resolve) => {
    let done = false
    let out = ''
    const env: NodeJS.ProcessEnv = { ...process.env, PYTHONIOENCODING: 'utf-8' }
    // The account sheet always uses the shared OS-protected session, including
    // when Patchbay inherited old shell-based cloud configuration.
    for (const key of ['QC_CLOUD_PASSWORD', 'QC_CLOUD_EMAIL', 'QC_CLOUD_ACCESS_TOKEN', 'QC_CLOUD_SESSION_FILE']) delete env[key]
    const child = spawn(paths.python, ['-m', 'qc_mcp.cloud_auth', ...args], {
      cwd: paths.repo,
      env,
      windowsHide: true,
      stdio: ['pipe', 'pipe', 'ignore']
    })
    const finish = (result: Record<string, unknown>): void => {
      if (done) return
      done = true
      clearTimeout(timer)
      resolve(result)
    }
    // A native credential-store permission prompt can need user interaction.
    const timer = setTimeout(() => {
      child.kill()
      finish({ error: 'Cortex Cloud timed out. Check for a system credential-store prompt, then try again.' })
    }, 120000)
    child.on('error', () => finish({ error: 'Could not start Cortex Cloud. Run Patchbay setup and try again.' }))
    child.stdin.on('error', () => { /* exit/error below reports a safe, useful message */ })
    child.stdout.setEncoding('utf8')
    child.stdout.on('data', (data: string) => {
      out += data
      if (out.length > 262144) {
        child.kill()
        finish({ error: 'Cortex Cloud returned an unexpected response.' })
      }
    })
    child.on('close', (code) => {
      if (done) return
      try {
        const data: unknown = JSON.parse(out)
        if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('invalid response')
        finish(data as Record<string, unknown>)
      } catch {
        finish({ error: code === 0
          ? 'Cortex Cloud returned an unexpected response.'
          : 'Cortex Cloud is unavailable. Run Patchbay setup to update its backend.' })
      }
    })
    child.stdin.end(input ? JSON.stringify(input) : '')
  })
}
