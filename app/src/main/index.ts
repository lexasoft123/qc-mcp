import { BrowserWindow, app, dialog, ipcMain, shell } from 'electron'
import { join } from 'node:path'
import type { CheckId, Mode, Prefs, Progress, Snapshot } from '../shared/types.js'
import type { Goal } from '../shared/session.js'
import { modeSwitch } from '../shared/session.js'
import * as clients from './clients.js'
import * as cortex from './cortex.js'
import * as install from './install.js'
import * as logs from './logs.js'
import * as session from './session.js'
import * as state from './state.js'
import { Leveling } from './leveling.js'
import { IS_MAC } from './paths.js'
import { findPython } from './system.js'

let win: BrowserWindow | null = null
let ticker: NodeJS.Timeout | null = null
let leveling: Leveling | null = null

/** The bench, built on first use and always pointed at the current paths. */
function bench(): Leveling {
  if (!leveling) {
    leveling = new Leveling(state.getPaths())
    leveling.onEvent((e) => emit('leveling:event', e))
  } else {
    leveling.setPaths(state.getPaths())
  }
  return leveling
}

function emit(channel: string, payload: unknown): void {
  win?.webContents.send(channel, payload)
}

function create(): void {
  win = new BrowserWindow({
    width: 980,
    height: 716,
    minWidth: 720,
    minHeight: 520,
    show: false,
    backgroundColor: '#12100d',
    // frameless with the traffic lights inset, so the kit's .titlebar can own
    // the top strip and stay draggable
    titleBarStyle: IS_MAC ? 'hiddenInset' : 'hidden',
    trafficLightPosition: IS_MAC ? { x: 18, y: 18 } : undefined,
    frame: IS_MAC,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  })

  win.on('ready-to-show', () => win?.show())
  const notify = (): void => emit('window:maximized', Boolean(win?.isMaximized()))
  win.on('maximize', notify)
  win.on('unmaximize', notify)

  if (process.env.ELECTRON_RENDERER_URL) void win.loadURL(process.env.ELECTRON_RENDERER_URL)
  else void win.loadFile(join(__dirname, '../renderer/index.html'))
}

// ── IPC ─────────────────────────────────────────────────────────────────

function handlers(): void {
  ipcMain.handle('snapshot', () => state.current() ?? state.refresh())
  ipcMain.handle('checks:run', () => state.push(true))

  ipcMain.handle('setup:run', async (_e, only?: CheckId[]) => {
    const snap = state.current() ?? (await state.refresh())
    const queue = snap.checks
      .filter((c) => c.fixable && c.status !== 'ok' && (!only || only.includes(c.id)))
      .map((c) => c.id)

    let done = 0
    let notice: string | null = null
    const total = queue.length
    const step = (label: string, id?: CheckId): void => emit('progress', { label, done, total, step: id })
    // keep the step tag on the sub-line progress an installer streams, or the
    // list loses track of which row is in flight
    const sub = (id: CheckId) => (p: Progress): void => emit('progress', { ...p, step: id })

    for (const id of queue) {
      step(snap.checks.find((c) => c.id === id)?.title ?? id, id)
      let error: string | null = null
      if (id === 'venv') {
        const python = await findPython()
        error = await install.createVenv(state.getPaths(), python, sub('venv'))
      } else if (id === 'clang') {
        // Apple's installer cannot run silently, so this is a status, not a
        // failure — the remaining steps must still run.
        notice = await install.installClang()
      } else if (id === 'instrumented') {
        error = await install.buildInstrumented(state.getPaths(), sub('instrumented'))
      } else if (id === 'register') {
        await registerDefaults()
      }
      done += 1
      if (error) {
        emit('progress', { label: error, done, total, finished: true, error })
        return state.push(true)
      }
    }
    emit('progress', { label: notice ?? 'Done', done, total, finished: true })
    return state.push(true)
  })

  ipcMain.handle('clients:set', async (_e, ids: string[]) => {
    const paths = state.getPaths()
    const viaCli = await clients.writeClaudeCode(paths, ids.includes('code'))
    clients.write(paths, ids, viaCli ? ['code'] : [])
    return state.push(true)
  })

  // ── connect / disconnect / mode ───────────────────────────────────────
  //
  // Every one of these is the same call: decide a plan from what is true, run
  // it, report. They used to be six handlers that each did a piece — start the
  // daemon, launch the app, write a preference — and pressing them in an order
  // nobody had thought about left the app describing a session it did not have.
  const pursue = async (goal: Goal, mode?: Mode): Promise<Snapshot> => {
    const out = await session.pursue(goal, mode, (label) =>
      emit('progress', { label, done: 0, total: 0 }))
    emit('progress', {
      label: out.error ?? 'Done', done: 1, total: 1, finished: true,
      error: out.error ?? undefined
    })
    return state.push()
  }

  ipcMain.handle('session:connect', () => pursue('connect'))
  // Ending somebody else's session is its own verb, never a side effect of
  // pressing Connect.
  ipcMain.handle('session:takeOver', () => pursue('take-over'))
  ipcMain.handle('session:disconnect', () => pursue('disconnect'))
  ipcMain.handle('session:plan', (_e, goal: Goal, mode?: Mode) => session.preview(goal, mode))

  ipcMain.handle('daemon:start', () => pursue('connect'))
  ipcMain.handle('daemon:stop', async () => {
    // the bench is an attached client of that session — it cannot outlive it
    leveling?.stop()
    return pursue('disconnect')
  })

  /**
   * Switching mode is a re-connect, not a preference write.
   *
   * Setting `prefs.mode` alone left a live bridge session running under a
   * selector that said Direct. If nothing is connected the preference is all
   * there is to change; if something is, the session is rebuilt to match.
   */
  /**
   * Switching mode is a preference, and only ever a preference.
   *
   * With nothing connected it changes what Connect will do — no app is opened,
   * no daemon started. With a session live it is refused: reshaping a session
   * under the person using it is what Disconnect is for. Enforced here and not
   * only in the view, so no other caller can route around it.
   */
  const setMode = async (mode: Mode): Promise<Snapshot> => {
    const snap = state.current() ?? (await state.refresh())
    const gate = modeSwitch(session.factsFrom(snap, false), snap.daemon.state !== 'stopped')
    if (!gate.allowed) {
      emit('progress', { label: gate.why!, done: 1, total: 1, finished: true, error: gate.why! })
      return snap
    }
    state.updatePrefs({ mode })
    return state.push()
  }

  ipcMain.handle('daemon:mode', async (_e, mode: Mode) => setMode(mode))

  ipcMain.handle('cortex:launch', () => pursue('show-app'))
  ipcMain.handle('cortex:focus', () => pursue('show-app'))
  ipcMain.handle('cortex:quit', async () => {
    // Quitting the app under a bridge session kills the session's transport and
    // leaves a daemon holding a dead FIFO. Take the session down first.
    const snap = state.current() ?? (await state.refresh())
    if (snap.daemon.state === 'running' && snap.daemon.session === 'bridge') {
      leveling?.stop()
      await session.pursue('disconnect', undefined, () => {})
    }
    await cortex.quit(state.getPaths().repo)
    return state.push()
  })

  ipcMain.handle('cortex:rebuild', async () => {
    await cortex.quit(state.getPaths().repo)
    const err = await install.buildInstrumented(state.getPaths(), (p) => emit('progress', p))
    emit('progress', { label: err ?? 'Rebuilt', done: 1, total: 1, finished: true, error: err ?? undefined })
    return state.push(true)
  })

  ipcMain.handle('logs:read', (_e, limit: number) => logs.read(state.getPaths().logPath, limit))
  ipcMain.handle('logs:size', () => logs.size(state.getPaths().logPath))
  ipcMain.handle('logs:clear', () => { logs.clear(state.getPaths().logPath) })

  ipcMain.handle('prefs:get', () => state.getPrefs())
  ipcMain.handle('prefs:set', async (_e, patch: Partial<Prefs>) => {
    // The mode means the same thing in Preferences as it does on Home.
    const { mode, ...rest } = patch
    if (Object.keys(rest).length) state.updatePrefs(rest)
    if (mode && mode !== state.getPrefs().mode) return setMode(mode)
    return state.push()
  })

  ipcMain.handle('path:choose', async (_e, what: 'repo' | 'cortex') => {
    const r = await dialog.showOpenDialog(win!, {
      title: what === 'repo' ? 'Choose the qc-mcp folder' : 'Choose Cortex Control',
      properties: what === 'repo' ? ['openDirectory'] : IS_MAC ? ['openFile', 'treatPackageAsDirectory'] : ['openFile'],
      filters: what === 'cortex' && !IS_MAC ? [{ name: 'Application', extensions: ['exe'] }] : undefined
    })
    if (!r.canceled && r.filePaths[0]) state.updatePrefs({ [what]: r.filePaths[0] } as Partial<Prefs>)
    return state.push(true)
  })
  ipcMain.handle('shell:reveal', (_e, p: string) => { shell.showItemInFolder(p) })

  // ── the leveling bench ────────────────────────────────────────────────
  ipcMain.handle('leveling:start', () => { bench().start() })
  ipcMain.handle('leveling:stop', () => { bench().stop() })
  ipcMain.handle('leveling:state', () => bench().state())
  ipcMain.handle('leveling:folders', (_e, refresh?: boolean) => bench().folders(refresh))
  ipcMain.handle('leveling:open', (_e, key: string, pos: number, factory: boolean, cloud: string) =>
    bench().open(key, pos, factory, cloud))
  ipcMain.handle('leveling:level', (_e, row: number, db: number) => bench().level(row, db))
  ipcMain.handle('leveling:toggle', (_e, row: number, which: 'mute' | 'solo', on: boolean) =>
    bench().toggle(row, which, on))
  ipcMain.handle('leveling:scene', (_e, index: number) => bench().scene(index))
  ipcMain.handle('leveling:save', (_e, name?: string) => bench().save(name))
  ipcMain.handle('leveling:meter', (_e, on: boolean) => bench().meter(on))
  ipcMain.handle('leveling:audio', () => bench().audio())
  ipcMain.handle('leveling:sampleArm', (_e, o) => bench().sampleArm(o ?? {}))
  ipcMain.handle('leveling:sampleStatus', () => bench().sampleStatus())
  ipcMain.handle('leveling:sampleInfo', () => bench().sampleInfo())
  ipcMain.handle('leveling:sampleStop', () => bench().sampleStop())
  ipcMain.handle('leveling:sampleDiscard', () => bench().sampleDiscard())
  ipcMain.handle('leveling:measure', (_e, perceived?: boolean) => bench().measure(perceived))
  ipcMain.handle('leveling:autolevel', (_e, o) => bench().autolevel(o ?? {}))
  ipcMain.handle('leveling:applyTrim', (_e, o) => bench().applyTrim(o))
  ipcMain.handle('leveling:samplePlay', () => bench().samplePlay())
  ipcMain.handle('leveling:sampleStopPlay', () => bench().sampleStopPlay())
  ipcMain.handle('leveling:revertLevels', () => bench().revertLevels())
  ipcMain.handle('leveling:measureMany', (_e, presets, o) => bench().measureMany(presets, o ?? {}))
  ipcMain.handle('leveling:measureScenes', (_e, o) => bench().measureScenes(o ?? {}))
  ipcMain.handle('leveling:levelScenes', (_e, o) => bench().levelScenes(o ?? {}))

  ipcMain.handle('window:isMaximized', () => Boolean(win?.isMaximized()))
  ipcMain.on('window:minimize', () => win?.minimize())
  ipcMain.on('window:maximizeToggle', () => (win?.isMaximized() ? win.unmaximize() : win?.maximize()))
  ipcMain.on('window:close', () => win?.close())
}

/**
 * The poll, plus the one thing it is allowed to decide on its own.
 *
 * `Connect as soon as the Quad Cortex is plugged in` only fires when there is
 * nothing left to set up and the daemon has not already failed — a daemon that
 * refused to start is not retried every two seconds until the user acts.
 */
/** Consecutive polls that have not seen the Quad Cortex. */
let missing = 0

async function tick(): Promise<void> {
  const snap = await state.push()

  /*
   * Unplugging (or re-enumerating) the device invalidates the handle the daemon
   * is holding. It keeps running and looking healthy while every read fails for
   * ever, so the session has to be retired — the poll is the only thing that
   * knows. Two consecutive misses, because one `ioreg` hiccup should not tear
   * down a working session. Reconnecting then goes through the normal path
   * below, or the Connect button when autoconnect is off.
   */
  if (snap.daemon.state === 'running') {
    // A bridge session rides Cortex Control's own handle, so quitting the app
    // takes the session with it — every call then fails with "inject FIFO
    // unavailable" against a daemon that still looks healthy.
    const bridgeLost = snap.daemon.session === 'bridge' && !snap.cortex.running
    missing = snap.device.present && !bridgeLost ? 0 : missing + 1
    if (missing >= 2) {
      missing = 0
      leveling?.stop()
      await state.getDaemon().stop()
      await state.push()
      return
    }
  } else {
    missing = 0
  }

  if (!snap.prefs.autoconnect) return
  if (session.busy()) return                 // a plan is already running
  if (!snap.device.present || snap.daemon.state !== 'stopped') return
  if (snap.daemon.error || !snap.daemon.supported) return
  if (snap.checks.some((c) => c.fixable && c.status !== 'ok')) return
  // Through the plan, like every other route in. Starting the daemon directly
  // here meant autoconnect in Bridge mode fired at a closed Cortex Control and
  // failed on the daemon's own BridgeError — a connection the plan would have
  // opened the app for first.
  await session.pursue('connect', undefined, () => {})
  await state.push()
}

/** First install writes the clients that are actually present on the machine. */
async function registerDefaults(): Promise<void> {
  const paths = state.getPaths()
  const found = clients.list().filter((c) => c.found).map((c) => c.id)
  const viaCli = await clients.writeClaudeCode(paths, found.includes('code'))
  clients.write(paths, found, viaCli ? ['code'] : [])
}

// ── lifecycle ───────────────────────────────────────────────────────────

void app.whenReady().then(async () => {
  state.init()
  handlers()
  create()
  await state.push()

  // one cheap poll keeps the meter and the app/device state honest without
  // asking the renderer to guess
  ticker = setInterval(() => { void tick() }, 2000)
  void tick()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) create()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', () => {
  if (ticker) clearInterval(ticker)
  leveling?.stop()
  state.getDaemon()?.stop()
  if (state.getPrefs().quitApp) void cortex.quit(state.getPaths().repo)
})
