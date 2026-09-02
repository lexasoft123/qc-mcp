import { app, net, shell } from 'electron'
import electronUpdater from 'electron-updater'
import type { UpdateState } from '../shared/types.js'
import { newer, reason, safeUrl } from './update-rules.js'
import { t } from '../shared/i18n/index.js'

/**
 * Releases are cut from the qc-mcp repository — Patchbay is packaged out of it
 * and its `v*` tags are what .github/workflows/release.yml publishes. The API
 * endpoint deliberately asks for `latest`, which GitHub defines as the newest
 * NON-prerelease: a hyphenated tag (v0.2.0-rc1) is uploaded with --prerelease,
 * so nobody is offered a release candidate they did not go looking for.
 */
const REPO = 'lexasoft123/qc-mcp'
const LATEST_API = `https://api.github.com/repos/${REPO}/releases/latest`
const RELEASES_PAGE = `https://github.com/${REPO}/releases/latest`
const CHECK_EVERY_MS = 6 * 60 * 60 * 1000
/** Long enough for a cold DNS + TLS handshake, short enough not to hang the state. */
const TIMEOUT_MS = 15_000

let current: UpdateState = { state: 'none' }
let notify: (s: UpdateState) => void = () => {}
let timer: NodeJS.Timeout | null = null
/** The one-shot check after launch. Tracked so `stop()` can actually cancel it. */
let firstCheck: NodeJS.Timeout | null = null

function set(s: UpdateState): void {
  current = s
  notify(s)
}

export const state = (): UpdateState => current

/** Forget the last result — what turning the `updates` preference off means. */
export function clear(): void {
  set({ state: 'none' })
}

/**
 * The running version — what the rail shows and what a tag is compared against.
 *
 * `||`, not `??`: a wrapper script that always exports PATCHBAY_FAKE_VERSION
 * hands us an empty string, and `??` would keep it. The rail would then read
 * "Patchbay  · macOS" and `newer(tag, '')` would parse to 0.0.0, making every
 * release look like an update for ever.
 */
export function version(): string {
  return process.env.PATCHBAY_FAKE_VERSION || app.getVersion()
}

// ── macOS: check, and offer the download ────────────────────────────────

/**
 * macOS is told about updates rather than given them.
 *
 * Not a signing problem — the dmg is Developer ID-signed and notarized (see
 * docs/MACOS-SIGNING.md). It is packaging: Squirrel.Mac installs from a `zip`
 * feed and `electron-builder.yml` builds `dmg` only. Adding a zip target means
 * a third artifact through Apple's notary queue on every release, per arch —
 * its own piece of work, not a flag to flip here.
 *
 * A latest-mac.yml IS written (dmg-builder sets isWriteUpdateInfo from the
 * blockmap) but it is not published, because the two arches are separate
 * invocations writing one filename and it ends up naming only the last. So
 * `available` is macOS's terminal state and the button opens the release page.
 *
 * The outcome is kept in a local and returned, rather than read back off
 * `current`: two checks can overlap (the six-hourly tick and a Check now
 * press), and the shared field hands each caller whichever finished last.
 */
async function checkViaGithub(): Promise<UpdateState> {
  const ctrl = new AbortController()
  const kill = setTimeout(() => ctrl.abort(), TIMEOUT_MS)
  let result: UpdateState = { state: 'none' }
  try {
    // Inside the try: `set` pushes to the renderer, and a push into a window
    // that is going away throws — which must not escape as a rejected check.
    set({ state: 'checking' })
    const res = await net.fetch(LATEST_API, {
      headers: { Accept: 'application/vnd.github+json' },
      signal: ctrl.signal
    })
    if (!res.ok) throw new Error(`GitHub answered ${res.status}`)
    const rel = (await res.json()) as { tag_name?: string; html_url?: string }
    const tag = rel.tag_name ?? ''
    if (tag && newer(tag, version())) {
      result = { state: 'available', version: tag.replace(/^v/, ''), url: safeUrl(rel.html_url, RELEASES_PAGE) }
    }
  } catch (err) {
    result = { state: 'error', message: reason(err, t('update.checkFailed')) }
  } finally {
    clearTimeout(kill)
  }
  set(result)
  return result
}

// ── Windows: the full electron-updater flow ─────────────────────────────

let wired = false
/** `download-progress` carries no version, so `update-available` parks it here. */
let downloading = ''

/** Download in the background, install on quit — or sooner, if the user asks. */
function checkViaElectronUpdater(): void {
  const { autoUpdater } = electronUpdater
  if (wired) {
    void autoUpdater.checkForUpdates().catch(() => {})
    return
  }
  wired = true
  // Test/self-host hook: point the updater at any generic feed directory.
  if (process.env.PATCHBAY_UPDATE_URL) {
    autoUpdater.setFeedURL({ provider: 'generic', url: process.env.PATCHBAY_UPDATE_URL })
  }
  autoUpdater.autoDownload = true
  autoUpdater.autoInstallOnAppQuit = true
  autoUpdater.on('checking-for-update', () => set({ state: 'checking' }))
  autoUpdater.on('update-available', (info) => {
    downloading = info.version
    set({ state: 'downloading', version: info.version, percent: 0 })
  })
  autoUpdater.on('download-progress', (p) =>
    set({ state: 'downloading', version: downloading, percent: Math.round(p.percent) })
  )
  autoUpdater.on('update-downloaded', (info) => set({ state: 'ready', version: info.version }))
  autoUpdater.on('update-not-available', () => set({ state: 'none' }))
  autoUpdater.on('error', (err) => set({ state: 'error', message: reason(err, t('update.checkFailed')) }))
  void autoUpdater.checkForUpdates().catch(() => {})
}

// ── what the rest of the app calls ──────────────────────────────────────

const useElectronUpdater = (): boolean =>
  process.platform === 'win32' && !process.env.PATCHBAY_TEST_UPDATER

/** Overlapping GitHub checks share one request, the way state.push() does. */
let inflight: Promise<UpdateState> | null = null

/**
 * Check now, whatever the preference says.
 *
 * macOS resolves with the answer. Windows cannot: electron-updater reports
 * through its events, so the returned state is 'checking' and the real outcome
 * arrives on the push — which is why the renderer subscribes rather than
 * awaiting this alone. Setting it here rather than leaving it to the
 * 'checking-for-update' event is what makes "Check now" visibly do something
 * on the first paint.
 */
export async function check(): Promise<UpdateState> {
  if (useElectronUpdater()) {
    set({ state: 'checking' })
    checkViaElectronUpdater()
    return current
  }
  if (inflight) return inflight
  const run = checkViaGithub().finally(() => { if (inflight === run) inflight = null })
  inflight = run
  return run
}

export function install(): void {
  if (useElectronUpdater() && current.state === 'ready') electronUpdater.autoUpdater.quitAndInstall()
}

/** Open the release page in the browser — the macOS "install" step. */
export async function openDownload(): Promise<void> {
  await shell.openExternal(current.state === 'available' ? current.url : RELEASES_PAGE)
}

/**
 * Start the background checks.
 *
 * `enabled` is read at each tick rather than captured, so turning the
 * preference off stops the next check without restarting anything — and turning
 * it back on does not need a relaunch either. An unpackaged build never checks:
 * `app.getVersion()` there is whatever package.json says, which would announce
 * an "update" to the version the developer is sitting on.
 */
export function start(onState: (s: UpdateState) => void, enabled: () => boolean): void {
  notify = onState
  const testMode = Boolean(process.env.PATCHBAY_TEST_UPDATER)
  if (!app.isPackaged && !testMode) return
  const tick = (): void => {
    // A downloaded update is worth more than a fresh answer: re-checking resets
    // the state to 'checking', which takes "Restart to update" out of the rail,
    // and a check that then fails strands an installer already on disk —
    // electron-updater will not re-emit update-downloaded for a cached one.
    if (current.state === 'ready' || current.state === 'downloading') return
    if (!enabled()) return
    // Nothing here can be recovered from, but an unhandled rejection is fatal:
    // Electron 33 runs Node 20, where the default is to throw.
    void check().catch(() => {})
  }
  firstCheck = setTimeout(tick, 3000)
  timer = setInterval(tick, CHECK_EVERY_MS)
}

export function stop(): void {
  if (firstCheck) clearTimeout(firstCheck)
  if (timer) clearInterval(timer)
  firstCheck = null
  timer = null
}
