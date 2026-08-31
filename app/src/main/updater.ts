import { app, net, shell } from 'electron'
import electronUpdater from 'electron-updater'
import type { UpdateState } from '../shared/types.js'

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

function set(s: UpdateState): void {
  current = s
  notify(s)
}

export const state = (): UpdateState => current

/** The running version — what the rail shows and what a tag is compared against. */
export function version(): string {
  return process.env.PATCHBAY_FAKE_VERSION ?? app.getVersion()
}

// ── version comparison ──────────────────────────────────────────────────

function parse(tag: string): { nums: number[]; pre: string } {
  const v = tag.replace(/^v/, '').split('+')[0]
  const dash = v.indexOf('-')
  const core = dash === -1 ? v : v.slice(0, dash)
  const nums = core.split('.').map((n) => parseInt(n, 10) || 0)
  return { nums: [nums[0] ?? 0, nums[1] ?? 0, nums[2] ?? 0], pre: dash === -1 ? '' : v.slice(dash + 1) }
}

/**
 * Is `tag` a newer release than `base`?
 *
 * major.minor.patch numerically, then semver's rule that a prerelease sorts
 * BELOW its own release. That last clause is the whole reason this is not a
 * three-element loop: `parseInt('0-rc1')` is 0, so 0.2.0-rc1 and 0.2.0 compare
 * equal on the numbers alone and someone running a release candidate would
 * never be told the final shipped. Two prereleases are never compared — GitHub's
 * `latest` skips them, so a prerelease only ever appears as `base`.
 */
function newer(tag: string, base: string): boolean {
  const a = parse(tag)
  const b = parse(base)
  for (let i = 0; i < 3; i++) {
    if (a.nums[i] !== b.nums[i]) return a.nums[i] > b.nums[i]
  }
  return !a.pre && b.pre !== ''
}

/**
 * A release URL is a value read off the network, and it ends up in
 * `shell.openExternal` — which will happily launch a `file:` or custom-scheme
 * handler. Only GitHub over https survives; anything else falls back to the
 * releases page we composed ourselves.
 */
function safeUrl(raw: string | undefined): string {
  try {
    const u = new URL(raw ?? '')
    const ok = u.protocol === 'https:' && (u.hostname === 'github.com' || u.hostname.endsWith('.github.com'))
    return ok ? u.toString() : RELEASES_PAGE
  } catch {
    return RELEASES_PAGE
  }
}

// ── macOS: check, and offer the download ────────────────────────────────

/**
 * macOS is told about updates rather than given them.
 *
 * Not a signing problem — the dmg is Developer ID-signed and notarized (see
 * docs/MACOS-SIGNING.md). It is packaging. Squirrel.Mac installs from a `zip`
 * feed, and `electron-builder.yml` builds `dmg` only: DmgTarget does not even
 * set isWriteUpdateInfo, so there is no latest-mac.yml for an updater to read.
 * Adding a zip target means a third artifact through Apple's notary queue on
 * every release, per arch — its own piece of work, not a flag to flip here.
 * Until then `available` is macOS's terminal state and the button opens the
 * release page.
 */
async function checkViaGithub(): Promise<UpdateState> {
  set({ state: 'checking' })
  const ctrl = new AbortController()
  const kill = setTimeout(() => ctrl.abort(), TIMEOUT_MS)
  try {
    const res = await net.fetch(LATEST_API, {
      headers: { Accept: 'application/vnd.github+json' },
      signal: ctrl.signal
    })
    if (!res.ok) throw new Error(`GitHub answered ${res.status}`)
    const rel = (await res.json()) as { tag_name?: string; html_url?: string }
    const tag = rel.tag_name ?? ''
    if (tag && newer(tag, version())) {
      set({ state: 'available', version: tag.replace(/^v/, ''), url: safeUrl(rel.html_url) })
    } else {
      set({ state: 'none' })
    }
  } catch (err) {
    set({ state: 'error', message: String(err instanceof Error ? err.message : err) })
  } finally {
    clearTimeout(kill)
  }
  return current
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
  autoUpdater.on('error', (err) => set({ state: 'error', message: String(err instanceof Error ? err.message : err) }))
  void autoUpdater.checkForUpdates().catch(() => {})
}

// ── what the rest of the app calls ──────────────────────────────────────

const useElectronUpdater = (): boolean =>
  process.platform === 'win32' && !process.env.PATCHBAY_TEST_UPDATER

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
  return checkViaGithub()
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
  const tick = (): void => { if (enabled()) void check() }
  setTimeout(tick, 3000)
  timer = setInterval(tick, CHECK_EVERY_MS)
}

export function stop(): void {
  if (timer) clearInterval(timer)
  timer = null
}
