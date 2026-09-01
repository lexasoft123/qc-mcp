import { BrowserWindow, app } from 'electron'
import type { Check, ClientTarget, Prefs, Snapshot } from '../shared/types.js'
import { getLocale, resolveLocale, setLocale, t } from '../shared/i18n/index.js'
import * as clients from './clients.js'
import * as logs from './logs.js'
import * as prefsStore from './prefs.js'
import { Daemon } from './daemon.js'
import { IS_MAC, PLATFORM, QC_PIDS, QC_VID, findRepo, pathsFor } from './paths.js'
import { cortexPid, findPython, hasClang, pythonDetail, readCortex, readDevice } from './system.js'
import { clear as clearUpdate, version } from './updater.js'
import { exists } from './util.js'

/** 0x880a — how the checklist prints a USB id. */
const hex = (n: number): string => `0x${n.toString(16).padStart(4, '0')}`

let prefs: Prefs = prefsStore.DEFAULTS
let paths = pathsFor(process.cwd())
let daemon: Daemon
let snapshot: Snapshot | null = null

/**
 * The probes that cost real subprocesses and almost never change: the Python
 * version, clang, and Cortex Control's version + codesign state. None of them
 * can move without an install, a rebuild or a path change, so they are read on
 * demand (startup, Re-check, after an install/rebuild/launch) and reused by the
 * poll, which only re-reads what is genuinely live.
 */
interface Slow {
  python: Awaited<ReturnType<typeof findPython>>
  clang: boolean
  cortex: Snapshot['cortex']
  device: Snapshot['device']
}
let slow: Slow | null = null
let inflight: Promise<Snapshot> | null = null

/**
 * The machine's languages, most preferred first, as BCP-47 tags — macOS gives
 * `zh-Hans-CN`, Windows `zh-CN`. `getLocale()` alone is Chromium's single
 * pick, which can be English on a machine whose owner reads Chinese second.
 */
function systemTags(): string[] {
  try {
    const list = app.getPreferredSystemLanguages()
    if (list.length) return list
  } catch { /* older Electron */ }
  try { return [app.getLocale()] } catch { return [] }
}

const systemLocale = (): Snapshot['systemLocale'] => resolveLocale('system', systemTags())

function applyLocale(): void {
  setLocale(resolveLocale(prefs.language, systemTags()))
}

export function init(): void {
  prefs = prefsStore.load()
  applyLocale()
  paths = pathsFor(findRepo(prefs.repo), prefs.cortex)
  daemon = new Daemon(paths)
  daemon.setMode(prefs.mode)
}

export const current = (): Snapshot | null => snapshot
export const getPaths = (): typeof paths => paths
export const getPrefs = (): Prefs => prefs
export const getDaemon = (): Daemon => daemon

function checksFrom(
  python: Awaited<ReturnType<typeof findPython>>,
  clang: boolean,
  cortex: Snapshot['cortex'],
  device: Snapshot['device'],
  targets: ClientTarget[]
): Check[] {
  const list: Check[] = [
    {
      id: 'python',
      title: python.uv ? t('check.python.bundled') : t('check.python'),
      detail: pythonDetail(python),
      status: python.ok ? 'ok' : 'missing',
      fixable: false
    },
    {
      id: 'venv',
      title: t('check.venv'),
      detail: t('check.venv.detail'),
      status: exists(paths.bin) ? 'ok' : 'missing',
      fixable: true
    }
  ]
  // the compiler and the injected copy serve the macOS-only interposer
  if (IS_MAC) {
    list.push({
      id: 'clang',
      title: t('check.clang'),
      detail: t('check.clang.detail'),
      status: clang ? 'ok' : 'missing',
      fixable: true
    })
  }
  list.push({
    id: 'app',
    title: t('check.app'),
    detail: cortex.version
      ? t('check.app.detail', { path: paths.show.cortex, version: cortex.version })
      : t('check.app.missing', { path: paths.show.cortex }),
    status: cortex.installed ? 'ok' : 'missing',
    fixable: false
  })
  if (IS_MAC) {
    const inst = cortex.instrumented
    list.push({
      id: 'instrumented',
      title: t('check.instrumented'),
      detail: inst?.built
        ? t('check.instrumented.built', { state: t(inst.hardenedRuntimeOff ? 'check.hardened.off' : 'check.hardened.on') })
        : t('check.instrumented.todo'),
      status: inst?.built && inst.hardenedRuntimeOff && inst.libraryValidationOff ? 'ok' : 'missing',
      fixable: true
    })
  }
  list.push(
    {
      id: 'register',
      title: t('check.register'),
      detail: t('check.register.detail'),
      status: targets.some((c) => c.installed) ? 'ok' : 'missing',
      fixable: true
    },
    {
      id: 'device',
      title: t('check.device'),
      detail: device.present
        ? `${device.model ?? 'Quad Cortex'}${device.serial ? ` · ${device.serial}` : ''}`
        : t('check.device.ids', { vid: hex(QC_VID), pids: Object.keys(QC_PIDS).map(Number).map(hex).join(' / ') }),
      status: device.present ? 'ok' : 'missing',
      fixable: false
    }
  )
  return list
}

/** Re-read the world. `deep` also re-runs the expensive, slow-changing probes. */
export async function refresh(deep = false): Promise<Snapshot> {
  paths = pathsFor(findRepo(prefs.repo), prefs.cortex)
  daemon.setPaths(paths)

  if (deep || !slow) {
    const [python, clang, cortex, device] = await Promise.all([
      findPython(),
      hasClang(),
      readCortex(paths),
      readDevice(true)
    ])
    slow = { python, clang, cortex, device }
  }

  // the only things that can change between two polls
  const [present, proc] = await Promise.all([readDevice(false), cortexPid(paths.repo)])
  const python = slow.python
  const clang = slow.clang
  const cortex = { ...slow.cortex, running: proc.pid !== null, pid: proc.pid }
  const device = { ...slow.device, present: present.present }
  const targets = clients.list()
  daemon.setClients(targets.filter((c) => c.installed).map((c) => c.name))

  const info = daemon.info()
  info.reportsPerSecond = info.state === 'running' || cortex.running ? logs.rate(paths.logPath) : 0

  snapshot = {
    platform: PLATFORM,
    // the updater's, not app.getVersion() directly: one definition of "the
    // running version", so PATCHBAY_FAKE_VERSION moves the rail and the check
    // together instead of leaving them disagreeing
    version: version(),
    locale: getLocale(),
    systemLocale: systemLocale(),
    paths,
    checks: checksFrom(python, clang, cortex, device, targets),
    clients: targets,
    daemon: info,
    cortex,
    device,
    prefs
  }
  return snapshot
}

export function broadcast(s: Snapshot): void {
  for (const w of BrowserWindow.getAllWindows()) w.webContents.send('snapshot', s)
}

/** Refresh and broadcast. Overlapping shallow polls share one in-flight run,
 *  so a slow machine cannot pile them up. */
export function push(deep = false): Promise<Snapshot> {
  if (!deep && inflight) return inflight
  const run = refresh(deep).then((s) => { broadcast(s); return s })
  if (!deep) {
    inflight = run
    void run.catch(() => null).finally(() => { if (inflight === run) inflight = null })
  }
  return run
}

export function updatePrefs(patch: Partial<Prefs>): void {
  prefs = { ...prefs, ...patch }
  prefsStore.save(prefs)
  if (patch.mode) daemon.setMode(patch.mode)
  // The checklist is rebuilt on every refresh, so the next push speaks the
  // new language; the renderer switches on the snapshot's `locale`.
  if (patch.language !== undefined) applyLocale()
  // The preference promises to silence the rail chip, not merely to stop future
  // checks — a cached 'available' would otherwise sit there for the session.
  if (patch.updates === false) clearUpdate()
  // a new repo or app location invalidates everything cached about them
  if (patch.repo !== undefined || patch.cortex !== undefined) slow = null
}
