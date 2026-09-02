/*
 * Just enough TOML to own one table in someone else's file.
 *
 * Codex keeps its MCP servers in ~/.codex/config.toml, and the rest of that
 * file — model, sandbox policy, plugins — is the user's. A real TOML library
 * would parse and re-serialise all of it, losing comments and reordering
 * keys the moment it wrote; this touches only the lines between our header
 * and the next one, and leaves every other byte where it was.
 *
 * Nothing here may import anything: it is exercised by `node --test`.
 */

/** `[a.b]` / `[[a.b]]` → the dotted path with quotes stripped, or null. */
function headerOf(line: string): { path: string; array: boolean } | null {
  const m = /^\s*(\[\[?)\s*([^\]]+?)\s*\]\]?\s*(?:#.*)?$/.exec(line)
  if (!m) return null
  const path = m[2]
    .split('.')
    .map((seg) => seg.trim().replace(/^"(.*)"$/, '$1').replace(/^'(.*)'$/, '$1'))
    .join('.')
  return { path, array: m[1] === '[[' }
}

/**
 * The line range [start, end) of `[header]` and everything under it — its
 * keys and any sub-table `[header.x]` — or null when the file has no such table.
 */
export function findTable(text: string, header: string): { start: number; end: number } | null {
  const lines = text.split('\n')
  let start = -1
  for (let i = 0; i < lines.length; i++) {
    const h = headerOf(lines[i])
    if (!h) continue
    if (start < 0) {
      if (!h.array && h.path === header) start = i
      continue
    }
    if (h.path === header || h.path.startsWith(header + '.')) continue
    return { start, end: i }
  }
  return start < 0 ? null : { start, end: lines.length }
}

/** The lines under `[header]` (the header itself excluded), or null. */
export function readTable(text: string, header: string): string | null {
  const r = findTable(text, header)
  if (!r) return null
  return text.split('\n').slice(r.start + 1, r.end).join('\n').replace(/\n+$/, '')
}

/**
 * Replace `[header]` and its body with `body` (the lines under the header),
 * append it at the end when absent, or remove it when `body` is null. The
 * rest of the document comes back byte for byte.
 */
export function writeTable(text: string, header: string, body: string | null): string {
  const lines = text.split('\n')
  const block = body === null ? [] : [`[${header}]`, ...body.replace(/\n+$/, '').split('\n'), '']
  const r = findTable(text, header)
  if (r) {
    // keep exactly one blank line between us and whatever follows
    let end = r.end
    while (end < lines.length && lines[end].trim() === '' && end > r.start) end++
    const after = end < lines.length ? [''] : []
    lines.splice(r.start, end - r.start, ...(body === null ? [] : [...block]), ...after)
    if (body === null) {
      // a removal must not leave two blank lines where the table was
      const out = lines.join('\n').replace(/\n{3,}/g, '\n\n')
      return out.trim() === '' ? '' : out.replace(/\n*$/, '\n')
    }
    return lines.join('\n').replace(/\n{3,}/g, '\n\n').replace(/\n*$/, '\n')
  }
  if (body === null) return text
  const head = text.trim() === '' ? '' : text.replace(/\n*$/, '\n') + '\n'
  return head + block.join('\n')
}

/** A TOML basic string: `"…"` with backslashes and quotes escaped. */
export function tomlString(s: string): string {
  return '"' + s.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/[\x00-\x1f]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}`) + '"'
}

/** The strings in `key = ["a", "b"]`, one line or several, or null when absent. */
export function arrayStrings(body: string, key: string): string[] | null {
  const m = new RegExp(`^\\s*${key}\\s*=\\s*\\[([^\\]]*)\\]`, 'm').exec(body)
  if (!m) return null
  const out: string[] = []
  for (const q of m[1].matchAll(/"((?:[^"\\]|\\.)*)"|'([^']*)'/g)) {
    out.push(q[1] !== undefined ? q[1].replace(/\\(.)/g, '$1') : q[2])
  }
  return out
}
