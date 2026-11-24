// Run with `node app/scripts/test-backend.cjs`; uses Node's assertions, not a
// test framework. Transpile the actual modules so no Electron process, vault,
// cloud account, or hardware is needed.
const assert = require('node:assert/strict')
const { EventEmitter } = require('node:events')
const { readFileSync } = require('node:fs')
const { join } = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

function load(name, mocks) {
  const file = join(__dirname, '../src/main', name + '.ts')
  const code = ts.transpileModule(readFileSync(file, 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS }
  }).outputText
  const module = { exports: {} }
  vm.runInNewContext(code, {
    module, exports: module.exports, process, setTimeout, clearTimeout,
    require: (id) => Object.hasOwn(mocks, id) ? mocks[id] : require(id)
  }, { filename: file })
  return module.exports
}

async function main() {
  const paths = { bin: '/test/.venv/qc-mcp', python: '/test/.venv/python', repo: '/test', show: {} }
  let installed = true
  let exitCode = 1
  const calls = []
  const system = load('system', {
    '../shared/i18n/index.js': { t: (key) => key },
    './paths.js': {},
    './util.js': {
      exists: () => installed,
      run: async (...args) => { calls.push(args); return { code: exitCode, out: '', err: 'missing dependency' } }
    }
  })
  // The old executable still exists, but absent new dependencies must fail.
  assert.equal(await system.backendReady(paths), false)
  assert.equal(calls[0][0], paths.python)
  assert.equal(calls[0][1][0], '-c')
  assert.match(calls[0][1][1], /'verify' in inspect.signature\(CloudClient.status\).parameters/)
  exitCode = 0
  assert.equal(await system.backendReady(paths), true)
  installed = false
  assert.equal(await system.backendReady(paths), false)
  assert.equal(calls.length, 2, 'a missing environment should not spawn Python')

  let ready = false
  let probes = 0
  class Daemon {
    setMode() {}
    setPaths() {}
    setClients() {}
    info() { return { state: 'stopped' } }
  }
  const defaults = { mode: 'auto', repo: null, cortex: null }
  const state = load('state', {
    electron: { app: { getPreferredSystemLanguages: () => ['en'], getLocale: () => 'en' }, BrowserWindow: { getAllWindows: () => [] } },
    '../shared/i18n/index.js': { t: (key) => key, getLocale: () => 'en', setLocale: () => {}, resolveLocale: () => 'en' },
    './lock.js': { read: () => null },
    './updater.js': { version: () => '0.2.0' },
    './clients.js': { list: () => [] },
    './logs.js': { rate: () => 0 },
    './prefs.js': { DEFAULTS: defaults, load: () => defaults, save: () => {} },
    './daemon.js': { Daemon },
    './paths.js': { IS_MAC: false, QC_PIDS: {}, QC_VID: 0, PLATFORM: 'win', findRepo: () => '/test', pathsFor: () => paths },
    './util.js': { exists: () => true },
    './system.js': {
      backendReady: async () => { probes++; return ready },
      findPython: async () => ({ ok: true }), hasClang: async () => true,
      pythonDetail: () => 'Python', cortexPid: async () => ({ pid: null }),
      readCortex: async () => ({ installed: false }), readDevice: async () => ({ present: false })
    }
  })
  state.init()
  const stale = (await state.refresh()).checks.find((check) => check.id === 'venv')
  assert.equal(stale.status, 'missing')
  assert.equal(stale.fixable, true)
  assert.match(stale.detail, /check.venv.upgrade/)
  await state.refresh()
  assert.equal(probes, 1, 'shallow polling must reuse the dependency probe')
  ready = true
  const updated = (await state.refresh(true)).checks.find((check) => check.id === 'venv')
  assert.equal(updated.status, 'ok')
  assert.equal(probes, 2, 'Re-check/setup completion must rerun the dependency probe')
  console.log('PASS backend compatibility probe and cached upgrade checks')

  const replies = [
    { authenticated: true, session_persisted: true, error: 'Cloud temporarily unavailable' },
    { error: 'Cloud session expired', signInRequired: true },
    { error: 'Cloud session expired', signInRequired: true },
    { error: 'Cloud temporarily unavailable' }
  ]
  const cloud = load('cloud', {
    './util.js': { exists: () => true },
    'node:child_process': { spawn(_python, args) {
      if (replies.length === 4) assert.ok(args.includes('--verify'))
      const child = new EventEmitter()
      child.stdout = new EventEmitter()
      child.stdout.setEncoding = () => {}
      child.stdin = new EventEmitter()
      child.stdin.end = () => {
        child.stdout.emit('data', JSON.stringify(replies.shift()))
        child.emit('close', 1)
      }
      return child
    } }
  })
  const status = await cloud.cloudAuth(paths, 'status')
  assert.equal(status.authenticated, true, 'verification failure preserves the saved session')
  assert.equal(status.error, 'Cloud temporarily unavailable')
  assert.equal((await cloud.searchCloud(paths, 'captures', 'test', 0)).signInRequired, true)
  assert.equal((await cloud.cloudPresetDetails(paths, 'preset-id')).signInRequired, true)
  assert.equal((await cloud.searchCloud(paths, 'captures', 'test', 0)).signInRequired, undefined)
  console.log('PASS Cloud verification and expiry responses')
}

main().catch((error) => { console.error(error); process.exitCode = 1 })
