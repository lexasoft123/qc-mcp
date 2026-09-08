import { test } from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, rmSync, writeFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { read, lockPath, isOurs } from '../src/main/lock.ts'

/**
 * The session lock is written in Python and read in TypeScript, so the test
 * that matters is the one that crosses the language boundary: qc_mcp writes it,
 * lock.ts reads it, and the field names have to agree. A drift there is exactly
 * the silent disagreement the lock exists to abolish.
 */

const REPO = resolve(import.meta.dirname, '..', '..')
const PY = join(REPO, '.venv', 'bin', 'python')
const havePython = existsSync(PY)

const python = (dir: string, body: string): string =>
  execFileSync(PY, ['-c',
    `import sys; sys.path.insert(0, ${JSON.stringify(join(REPO, 'src'))})\n` +
    `from qc_mcp import lockfile as L\nSOCK = ${JSON.stringify(join(dir, 'daemon.sock'))}\n` + body
  ], { encoding: 'utf8' })

const withDir = (fn: (d: string) => void): void => {
  const d = mkdtempSync(join(tmpdir(), 'qc-lock-'))
  try { fn(d) } finally { rmSync(d, { recursive: true, force: true }) }
}

test('nothing on disk is nobody holding the device', () => {
  withDir((d) => assert.equal(read(join(d, 'daemon.sock')), null))
})

test('unreadable rubbish is nobody holding the device', () => {
  withDir((d) => {
    writeFileSync(lockPath(join(d, 'daemon.sock')), '{ not json')
    assert.equal(read(join(d, 'daemon.sock')), null)
    writeFileSync(lockPath(join(d, 'daemon.sock')), '[1,2,3]')
    assert.equal(read(join(d, 'daemon.sock')), null)
  })
})

test('a record whose process is gone is not an owner', () => {
  withDir((d) => {
    writeFileSync(lockPath(join(d, 'daemon.sock')), JSON.stringify({
      pid: 999999999, owner: 'daemon', mode: 'bridge', socket: join(d, 'daemon.sock')
    }))
    assert.equal(read(join(d, 'daemon.sock')), null)
  })
})

test('a live record with a nonsense owner or mode is not trusted', () => {
  withDir((d) => {
    const p = lockPath(join(d, 'daemon.sock'))
    writeFileSync(p, JSON.stringify({ pid: process.pid, owner: 'hacker', mode: 'bridge' }))
    assert.equal(read(join(d, 'daemon.sock')), null)
    writeFileSync(p, JSON.stringify({ pid: process.pid, owner: 'daemon', mode: 'sideways' }))
    assert.equal(read(join(d, 'daemon.sock')), null)
  })
})

test('ours is only what Patchbay started', () => {
  const base = { pid: 1, owner: 'daemon' as const, mode: 'bridge' as const, socket: '',
                 startedAt: null, firmware: null, appPid: null }
  assert.equal(isOurs({ ...base, launchedBy: 'patchbay' }), true)
  assert.equal(isOurs({ ...base, launchedBy: 'cli' }), false)
  assert.equal(isOurs({ ...base, launchedBy: null }), false)
  assert.equal(isOurs(null), false)
})

test('qc_mcp writes it and lock.ts reads every field', { skip: !havePython }, () => {
  withDir((d) => {
    python(d, `L.acquire("daemon", "bridge", SOCK, launched_by="patchbay",
                          firmware="4.1.0", app_pid=4242)
L.update(SOCK, firmware="4.1.0")`)
    // Python wrote it under its own pid, which has exited — rewrite just the pid
    // so the liveness test passes while every other field stays as Python left it.
    const p = lockPath(join(d, 'daemon.sock'))
    const raw = JSON.parse(execFileSync('cat', [p], { encoding: 'utf8' }))
    raw.pid = process.pid
    writeFileSync(p, JSON.stringify(raw))

    const got = read(join(d, 'daemon.sock'))
    assert.ok(got, 'lock.ts could not read what qc_mcp wrote')
    assert.equal(got.owner, 'daemon')
    assert.equal(got.mode, 'bridge')
    assert.equal(got.launchedBy, 'patchbay')
    assert.equal(got.firmware, '4.1.0')
    assert.equal(got.appPid, 4242)
    assert.equal(got.socket, join(d, 'daemon.sock'))
    assert.ok(got.startedAt && got.startedAt > 1_600_000_000_000,
      'started_at should arrive in milliseconds')
  })
})

test('qc_mcp refuses to claim a device this process is holding', { skip: !havePython }, () => {
  withDir((d) => {
    // a live pid that is not the Python process: this test runner
    python(d, `L._write(L.path(SOCK), {"pid": ${process.pid}, "owner": "mcp", "mode": "direct"})`)
    const out = python(d, `
try:
    L.acquire("daemon", "bridge", SOCK)
    print("CLAIMED")
except L.Held as e:
    print("HELD:", e)`)
    assert.match(out, /HELD:/)
    assert.match(out, /mcp/)
    assert.match(out, /direct/)
    // and lock.ts agrees about who that is
    const got = read(join(d, 'daemon.sock'))
    assert.equal(got?.owner, 'mcp')
    assert.equal(got?.mode, 'direct')
    assert.equal(isOurs(got), false)
  })
})
