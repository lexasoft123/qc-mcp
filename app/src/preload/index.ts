import { contextBridge, ipcRenderer } from 'electron'
import type { Api, CheckId, LogLine, Mode, Prefs, Progress, Snapshot } from '../shared/types.js'

/** Subscribe to a main-process push; returns the unsubscribe. */
function on<T>(channel: string, cb: (v: T) => void): () => void {
  const handler = (_e: unknown, v: T): void => cb(v)
  ipcRenderer.on(channel, handler)
  return () => { ipcRenderer.off(channel, handler) }
}

const api: Api = {
  snapshot: () => ipcRenderer.invoke('snapshot') as Promise<Snapshot>,
  onSnapshot: (cb) => on<Snapshot>('snapshot', cb),
  onProgress: (cb) => on<Progress>('progress', cb),

  runChecks: () => ipcRenderer.invoke('checks:run') as Promise<Snapshot>,
  runSetup: (ids?: CheckId[]) => ipcRenderer.invoke('setup:run', ids) as Promise<Snapshot>,

  setClients: (ids: string[]) => ipcRenderer.invoke('clients:set', ids) as Promise<Snapshot>,

  daemonStart: () => ipcRenderer.invoke('daemon:start') as Promise<Snapshot>,
  daemonStop: () => ipcRenderer.invoke('daemon:stop') as Promise<Snapshot>,
  setMode: (mode: Mode) => ipcRenderer.invoke('daemon:mode', mode) as Promise<Snapshot>,

  cortexLaunch: () => ipcRenderer.invoke('cortex:launch') as Promise<Snapshot>,
  cortexQuit: () => ipcRenderer.invoke('cortex:quit') as Promise<Snapshot>,
  cortexRebuild: () => ipcRenderer.invoke('cortex:rebuild') as Promise<Snapshot>,

  readLog: (limit: number) => ipcRenderer.invoke('logs:read', limit) as Promise<LogLine[]>,
  clearLog: () => ipcRenderer.invoke('logs:clear') as Promise<void>,
  logSize: () => ipcRenderer.invoke('logs:size') as Promise<number>,

  getPrefs: () => ipcRenderer.invoke('prefs:get') as Promise<Prefs>,
  setPrefs: (patch: Partial<Prefs>) => ipcRenderer.invoke('prefs:set', patch) as Promise<Snapshot>,

  choosePath: (what) => ipcRenderer.invoke('path:choose', what) as Promise<Snapshot>,
  reveal: (p: string) => ipcRenderer.invoke('shell:reveal', p) as Promise<void>,

  window: {
    isMaximized: () => ipcRenderer.invoke('window:isMaximized') as Promise<boolean>,
    onMaximized: (cb) => on<boolean>('window:maximized', cb),
    minimize: () => ipcRenderer.send('window:minimize'),
    maximizeToggle: () => ipcRenderer.send('window:maximizeToggle'),
    close: () => ipcRenderer.send('window:close')
  }
}

contextBridge.exposeInMainWorld('patchbay', api)
