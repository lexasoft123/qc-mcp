/**
 * The updater's pure decisions: is that tag newer, is that URL safe to open,
 * and what does an error actually say.
 *
 * Split out of updater.ts so they can be tested without Electron — the module
 * they came from imports `electron` and `electron-updater` at the top, which no
 * plain `node --test` process can load. Nothing here may import anything.
 */

export function parse(tag: string): { nums: number[]; pre: string } {
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
export function newer(tag: string, base: string): boolean {
  const a = parse(tag)
  const b = parse(base)
  for (let i = 0; i < 3; i++) {
    if (a.nums[i] !== b.nums[i]) return a.nums[i] > b.nums[i]
  }
  return !a.pre && b.pre !== ''
}

/**
 * One readable line out of whatever threw.
 *
 * Measured, not guessed: when the newest release carries no latest.yml — every
 * release cut before the updater existed, and any release where the upload is
 * missed — electron-updater's 404 arrives as ~2 KB. The URL, then GitHub's full
 * response headers, then a stack trace through the asar. Put verbatim into a
 * Preferences row that is a wall of text, so this keeps the first line (which
 * is the part that says what went wrong) and caps it.
 */
export function reason(err: unknown, fallback = 'check failed'): string {
  const raw = err instanceof Error ? err.message : String(err)
  const line = raw.split('\n')[0].trim()
  if (!line) return fallback
  return line.length > 160 ? line.slice(0, 159) + '…' : line
}

/**
 * A release URL is a value read off the network, and it ends up in
 * `shell.openExternal` — which will happily launch a `file:` or custom-scheme
 * handler. Only GitHub over https survives; anything else falls back to the
 * page the caller composed itself.
 */
export function safeUrl(raw: string | undefined, fallback: string): string {
  try {
    const u = new URL(raw ?? '')
    const ok = u.protocol === 'https:' && (u.hostname === 'github.com' || u.hostname.endsWith('.github.com'))
    return ok ? u.toString() : fallback
  } catch {
    return fallback
  }
}
