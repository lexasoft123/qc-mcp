import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import type { ClientTarget, Paths } from '../shared/types.js'
import { IS_MAC, tilde } from './paths.js'
import { exists, run } from './util.js'

const HOME = homedir()
const APPDATA = process.env.APPDATA || join(HOME, 'AppData', 'Roaming')

/** Our entry's key in every client's config. */
export const SERVER_KEY = 'quad-cortex'

interface Target {
  id: string
  name: string
  file: string
  /** where the servers map lives inside the JSON */
  at: string[]
}

/**
 * Each client keeps MCP servers in its own file under its own key. We only
 * ever touch that one entry and write the rest of the document back untouched.
 */
export function targets(): Target[] {
  return IS_MAC
    ? [
        { id: 'code', name: 'Claude Code', file: join(HOME, '.claude.json'), at: ['mcpServers'] },
        { id: 'desktop', name: 'Claude Desktop', file: join(HOME, 'Library', 'Application Support', 'Claude', 'claude_desktop_config.json'), at: ['mcpServers'] },
        { id: 'cursor', name: 'Cursor', file: join(HOME, '.cursor', 'mcp.json'), at: ['mcpServers'] },
        { id: 'vscode', name: 'VS Code', file: join(HOME, 'Library', 'Application Support', 'Code', 'User', 'mcp.json'), at: ['servers'] },
        { id: 'zed', name: 'Zed', file: join(HOME, '.config', 'zed', 'settings.json'), at: ['context_servers'] }
      ]
    : [
        { id: 'code', name: 'Claude Code', file: join(HOME, '.claude.json'), at: ['mcpServers'] },
        { id: 'desktop', name: 'Claude Desktop', file: join(APPDATA, 'Claude', 'claude_desktop_config.json'), at: ['mcpServers'] },
        { id: 'cursor', name: 'Cursor', file: join(HOME, '.cursor', 'mcp.json'), at: ['mcpServers'] },
        { id: 'vscode', name: 'VS Code', file: join(APPDATA, 'Code', 'User', 'mcp.json'), at: ['servers'] },
        { id: 'zed', name: 'Zed', file: join(APPDATA, 'Zed', 'settings.json'), at: ['context_servers'] }
      ]
}

function readJson(file: string): Record<string, unknown> {
  try {
    return JSON.parse(readFileSync(file, 'utf8')) as Record<string, unknown>
  } catch {
    return {}
  }
}

function bucket(doc: Record<string, unknown>, at: string[]): Record<string, unknown> {
  let node = doc
  for (const key of at) {
    if (typeof node[key] !== 'object' || node[key] === null) node[key] = {}
    node = node[key] as Record<string, unknown>
  }
  return node
}

/** A client counts as "found" if its config directory exists at all. */
function found(t: Target): boolean {
  return exists(t.file) || exists(dirname(t.file))
}

export function list(): ClientTarget[] {
  return targets().map((t) => {
    let node: unknown = readJson(t.file)
    for (const key of t.at) node = (node as Record<string, unknown> | undefined)?.[key]
    const entryVal = node && typeof node === 'object'
      ? (node as Record<string, unknown>)[SERVER_KEY]
      : undefined
    // An install.sh-era entry runs the stdio server with no arguments, so it
    // opens the device for itself and fails the moment a daemon holds one.
    const args = entryVal && typeof entryVal === 'object'
      ? (entryVal as { args?: unknown }).args
      : undefined
    return {
      id: t.id,
      name: t.name,
      path: tilde(t.file),
      found: found(t),
      installed: Boolean(entryVal),
      stale: Boolean(entryVal) && !(Array.isArray(args) && args.includes('--attach'))
    }
  })
}

function entry(paths: Paths): Record<string, unknown> {
  // --attach points the stdio server at the running daemon rather than opening
  // the device itself, so several clients can share one session.
  return { command: paths.bin, args: ['--attach', '--socket', paths.socket] }
}

/**
 * `skip` is not the same as "not wanted": a target someone else owns (Claude
 * Code, whose own CLI manages ~/.claude.json) must be left alone entirely.
 * Merely leaving it out of `wanted` makes the loop below DELETE its entry —
 * which silently undid the `claude mcp add` that had just written it.
 */
export function write(paths: Paths, wanted: string[], skip: string[] = []): void {
  for (const t of targets()) {
    if (skip.indexOf(t.id) >= 0) continue
    if (!found(t) && wanted.indexOf(t.id) < 0) continue
    const doc = readJson(t.file)
    const node = bucket(doc, t.at)
    if (wanted.indexOf(t.id) >= 0) node[SERVER_KEY] = entry(paths)
    else delete node[SERVER_KEY]
    try {
      mkdirSync(dirname(t.file), { recursive: true })
      writeFileSync(t.file, JSON.stringify(doc, null, 2) + '\n', 'utf8')
    } catch {
      // a client we cannot write to is reported as not installed on the next read
    }
  }
}

/**
 * Claude Code owns ~/.claude.json and rewrites it; going through its own CLI
 * keeps us out of a file it is actively managing. Falls back to the JSON write
 * above when the CLI is not on PATH.
 */
export async function writeClaudeCode(paths: Paths, install: boolean): Promise<boolean> {
  const probe = await run('claude', ['--version'], { timeout: 5000 })
  if (probe.code !== 0) return false
  await run('claude', ['mcp', 'remove', SERVER_KEY, '-s', 'user'], { timeout: 10000 })
  if (install) {
    await run('claude', ['mcp', 'add', '--scope', 'user', SERVER_KEY, '--', paths.bin, '--attach', '--socket', paths.socket], { timeout: 10000 })
  }
  return true
}
