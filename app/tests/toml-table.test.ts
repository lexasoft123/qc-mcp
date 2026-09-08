/*
 * The one-table TOML editor behind the Codex registration. Run with
 * `npm test`; nothing here imports Electron.
 */
import { deepEqual, equal } from 'node:assert/strict'
import { describe, it } from 'node:test'
import { arrayStrings, findTable, readTable, tomlString, writeTable } from '../src/main/toml-table.ts'

const H = 'mcp_servers.quad-cortex'
const BODY = 'command = "/x/qc-mcp"\nargs = ["--attach", "--socket", "/x/daemon.sock"]'

const CONFIG = `model = "gpt-5"
approval_policy = "on-request"

[mcp_servers.context7]
command = "npx"
args = ["-y", "@upstash/context7-mcp"]

[plugins."browser@openai-bundled"]
enabled = true
`

describe('findTable', () => {
  it('finds nothing in a file without the table', () => {
    equal(findTable(CONFIG, H), null)
    equal(readTable('', H), null)
  })

  it('accepts the quoted spellings of the key', () => {
    equal(readTable('[mcp_servers."quad-cortex"]\ncommand = "a"\n', H), 'command = "a"')
    equal(readTable("[mcp_servers.'quad-cortex']\ncommand = \"a\"\n", H), 'command = "a"')
  })

  it('is not fooled by an array of tables or a longer name', () => {
    equal(readTable('[[mcp_servers.quad-cortex]]\ncommand = "a"\n', H), null)
    equal(readTable('[mcp_servers.quad-cortex-old]\ncommand = "a"\n', H), null)
  })
})

describe('writeTable', () => {
  it('appends to a file that lacks it, and touches nothing else', () => {
    const out = writeTable(CONFIG, H, BODY)
    equal(out, CONFIG + `\n[${H}]\n${BODY}\n`)
    equal(readTable(out, H), BODY)
  })

  it('starts an empty file cleanly', () => {
    equal(writeTable('', H, BODY), `[${H}]\n${BODY}\n`)
    equal(writeTable('\n\n', H, BODY), `[${H}]\n${BODY}\n`)
  })

  it('replaces a table in the middle and keeps its neighbours', () => {
    const mid = `${CONFIG}\n[${H}]\ncommand = "/old/qc-mcp"\n\n[trailing]\nx = 1\n`
    const out = writeTable(mid, H, BODY)
    equal(readTable(out, H), BODY)
    equal(out.includes('[trailing]\nx = 1\n'), true)
    equal(out.includes('/old/'), false)
    equal(out.startsWith(CONFIG), true)
  })

  it('removes the table and its sub-tables, leaving one blank line', () => {
    const mid = `${CONFIG}\n[${H}]\ncommand = "/old/qc-mcp"\n\n[${H}.env]\nFOO = "1"\n\n[trailing]\nx = 1\n`
    const out = writeTable(mid, H, null)
    equal(readTable(out, H), null)
    equal(out, `${CONFIG}\n[trailing]\nx = 1\n`)
  })

  it('removing from a file that lacks it is a no-op', () => {
    equal(writeTable(CONFIG, H, null), CONFIG)
  })

  it('round-trips: write, then read back what was written', () => {
    const out = writeTable(writeTable(CONFIG, H, 'a = 1'), H, BODY)
    equal(readTable(out, H), BODY)
    equal(out.split(`[${H}]`).length, 2)
  })
})

describe('tomlString', () => {
  it('escapes Windows paths and quotes', () => {
    equal(tomlString('C:\\Users\\me\\qc-mcp.exe'), '"C:\\\\Users\\\\me\\\\qc-mcp.exe"')
    equal(tomlString('say "hi"'), '"say \\"hi\\""')
  })
})

describe('arrayStrings', () => {
  it('reads a one-line array', () => {
    deepEqual(arrayStrings(BODY, 'args'), ['--attach', '--socket', '/x/daemon.sock'])
  })

  it('reads a multi-line array and literal strings', () => {
    deepEqual(arrayStrings("args = [\n  '--attach',\n  \"--socket\",\n]\n", 'args'), ['--attach', '--socket'])
  })

  it('is null when the key is absent', () => {
    equal(arrayStrings('command = "x"', 'args'), null)
  })
})
