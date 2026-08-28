import { BrowserWindow, app, dialog, ipcMain, shell } from 'electron'
import { join } from 'node:path'
import type { CheckId, Mode, Prefs, Progress } from '../shared/types.js'
import * as clients from './clients.js'
import * as cortex from './cortex.js'
import * as install from './install.js'
import * as logs from './logs.js'
import * as state from './state.js'
import { IS_MAC } from './paths.js'
import { findPython } from './system.js'

let win: BrowserWindow | null = null
let ticker: NodeJS.Timeout | null = null

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

  ipcMain.handle('daemon:start', async () => {
    await state.getDaemon().start(() => void state.push())
    return state.push()
  })
  ipcMain.handle('daemon:stop', async () => {
    state.getDaemon().stop()
    return state.push()
  })
  ipcMain.handle('daemon:mode', async (_e, mode: Mode) => {
    state.updatePrefs({ mode })
    return state.push()
  })

  ipcMain.handle('cortex:launch', async () => {
    const paths = state.getPaths()
    const err = await cortex.launch(paths)
    if (err) emit('progress', { label: err, done: 0, total: 0, finished: true, error: err })
    // Wait for the bridge rather than just for the process: whoever starts the
    // daemon next needs it actually open, or auto silently picks direct.
    if (!err && IS_MAC && state.getPrefs().mode !== 'direct') {
      emit('progress', { label: 'Opening Cortex Control', done: 0, total: 0 })
      const ok = await cortex.waitForBridge(paths.repo)
      emit('progress', {
        label: ok ? 'Cortex Control is up' : 'Cortex Control did not open in time; using direct mode',
        done: 0, total: 0, finished: true
      })
    }
    return state.push(true)
  })
  ipcMain.handle('cortex:quit', async () => {
    await cortex.quit()
    return state.push()
  })
  ipcMain.handle('cortex:focus', async () => {
    await cortex.focus(state.getPaths())
    return state.push()
  })
  ipcMain.handle('cortex:rebuild', async () => {
    await cortex.quit()
    const err = await install.buildInstrumented(state.getPaths(), (p) => emit('progress', p))
    emit('progress', { label: err ?? 'Rebuilt', done: 1, total: 1, finished: true, error: err ?? undefined })
    return state.push(true)
  })

  ipcMain.handle('logs:read', (_e, limit: number) => logs.read(state.getPaths().logPath, limit))
  ipcMain.handle('logs:size', () => logs.size(state.getPaths().logPath))
  ipcMain.handle('logs:clear', () => { logs.clear(state.getPaths().logPath) })

  ipcMain.handle('prefs:get', () => state.getPrefs())
  ipcMain.handle('prefs:set', async (_e, patch: Partial<Prefs>) => {
    state.updatePrefs(patch)
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
async function tick(): Promise<void> {
  const snap = await state.push()
  if (!snap.prefs.autoconnect) return
  if (!snap.device.present || snap.daemon.state !== 'stopped') return
  if (snap.daemon.error || !snap.daemon.supported) return
  if (snap.checks.some((c) => c.fixable && c.status !== 'ok')) return
  await state.getDaemon().start(() => void state.push())
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
  state.getDaemon()?.stop()
  if (state.getPrefs().quitApp) void cortex.quit()
})
