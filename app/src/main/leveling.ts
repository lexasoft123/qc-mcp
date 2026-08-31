import { type ChildProcess, spawn } from 'node:child_process'
import type { LevelEvent, Paths, PresetFolder, PresetState } from '../shared/types.js'
import { exists } from './util.js'

/**
 * Supervises the preset-leveling bench — `qc-mcp --leveling`, one more client
 * attached to the running daemon.
 *
 * It is a child process rather than TypeScript talking HID because everything
 * the bench needs already exists in Python: the framing, the protobuf pool, the
 * preset model and the parameter tapers. Re-implementing those here to move a
 * single dB value would be a second, diverging copy of the hardest code in the
 * repo.
 *
 * The wire is newline-delimited JSON, the same shape the daemon speaks: every
 * reply carries the `id` of its request, and anything with an `event` key is
 * unsolicited (meters, or a fatal that means the bench could not attach).
 */
export class Leveling {
  private child: ChildProcess | null = null
  private buf = ''
  private seq = 0
  private pending = new Map<number, { ok: (v: Reply) => void; fail: (e: Error) => void }>()
  private listeners = new Set<(e: LevelEvent) => void>()
  private stderr = ''

  constructor(private paths: Paths) {}

  setPaths(paths: Paths): void { this.paths = paths }

  get running(): boolean { return this.child !== null }

  onEvent(cb: (e: LevelEvent) => void): () => void {
    this.listeners.add(cb)
    return () => { this.listeners.delete(cb) }
  }

  private emit(e: LevelEvent): void {
    this.listeners.forEach((f) => f(e))
  }

  /** Idempotent: a second start on a live bench is a no-op, not a second child. */
  start(): void {
    if (this.child) return
    if (!exists(this.paths.bin)) {
      this.emit({ event: 'fatal', error: `qc-mcp is not installed at ${this.paths.bin}.` })
      return
    }
    this.stderr = ''
    const child = spawn(this.paths.bin, ['--leveling', '--socket', this.paths.socket], {
      cwd: this.paths.repo,
      stdio: ['pipe', 'pipe', 'pipe']
    })
    this.child = child
    child.stdout?.setEncoding('utf8')
    child.stdout?.on('data', (d: string) => this.feed(d))
    child.stderr?.on('data', (d) => { this.stderr += String(d) })
    child.on('error', (e) => { this.stderr += String(e) })
    child.on('exit', () => {
      this.child = null
      // Fail every in-flight call, or a renderer await hangs for ever.
      const why = this.stderr.trim().split('\n').slice(-2).join(' ').slice(0, 300)
      this.pending.forEach((p) => p.fail(new Error(why || 'the leveling bench exited')))
      this.pending.clear()
      this.emit({ event: 'stopped', error: why || null })
    })
  }

  stop(): void {
    const child = this.child
    this.child = null
    this.pending.forEach((p) => p.fail(new Error('the leveling bench was stopped')))
    this.pending.clear()
    if (child) {
      try { child.stdin?.end() } catch { /* already closed */ }
      setTimeout(() => { try { child.kill('SIGKILL') } catch { /* gone */ } }, 1500)
    }
  }

  private feed(chunk: string): void {
    this.buf += chunk
    let nl: number
    while ((nl = this.buf.indexOf('\n')) >= 0) {
      const line = this.buf.slice(0, nl).trim()
      this.buf = this.buf.slice(nl + 1)
      if (!line) continue
      let msg: Reply
      try { msg = JSON.parse(line) as Reply } catch { continue }
      if (typeof msg.event === 'string') { this.emit(msg as unknown as LevelEvent); continue }
      const waiter = this.pending.get(msg.id as number)
      if (!waiter) continue
      this.pending.delete(msg.id as number)
      waiter.ok(msg)
    }
  }

  /** One request/reply. Rejects on a bench-side error so the renderer can toast it. */
  call(op: string, args: Record<string, unknown> = {}, timeoutMs = 30000): Promise<Reply> {
    if (!this.child) this.start()
    const child = this.child
    if (!child) return Promise.reject(new Error('the leveling bench is not running'))
    const id = ++this.seq
    return new Promise<Reply>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id)
        reject(new Error(`the bench did not answer '${op}' within ${Math.round(timeoutMs / 1000)}s`))
      }, timeoutMs)
      this.pending.set(id, {
        ok: (v) => {
          clearTimeout(timer)
          if (v.ok === false) reject(new Error(String(v.error ?? `'${op}' failed`)))
          else resolve(v)
        },
        fail: (e) => { clearTimeout(timer); reject(e) }
      })
      try {
        child.stdin?.write(`${JSON.stringify({ ...args, op, id })}\n`)
      } catch (e) {
        clearTimeout(timer)
        this.pending.delete(id)
        reject(e as Error)
      }
    })
  }

  // ── typed calls ───────────────────────────────────────────────────────
  //
  // The bench speaks the repo's snake_case; the renderer is given camelCase, so
  // the rename happens once, here, instead of in every component.

  async state(): Promise<PresetState> {
    return toPreset((await this.call('state')).preset)
  }

  async folders(refresh = false): Promise<PresetFolder[]> {
    const r = await this.call('folders', { refresh }, refresh ? 90000 : 30000)
    return (r.folders as RawFolder[]).map((f) => ({
      key: f.key,
      name: f.name,
      isFactory: f.is_factory,
      isDownloads: f.is_downloads,
      presets: f.presets.map((p) => ({
        position: p.position, name: p.name, cloudId: p.cloud_id
      }))
    }))
  }

  async open(
    folderKey: string, position: number, isFactory: boolean, cloudId: string
  ): Promise<PresetState> {
    const r = await this.call('open', {
      folder_key: folderKey, position, is_factory: isFactory, cloud_id: cloudId
    })
    return toPreset(r.preset)
  }

  async level(row: number, db: number): Promise<number> {
    return (await this.call('level', { row, db }, 8000)).db as number
  }

  async toggle(row: number, which: 'mute' | 'solo', on: boolean): Promise<boolean> {
    return (await this.call('switch', { row, which, on }, 8000)).on as boolean
  }

  async scene(index: number): Promise<number> {
    return (await this.call('scene', { index }, 8000)).scene as number
  }

  async save(name = ''): Promise<{ name: string; position: number }> {
    return (await this.call('save', { name })).saved as { name: string; position: number }
  }

  async meter(on: boolean): Promise<boolean> {
    return (await this.call('meter', { on }, 8000)).metering as boolean
  }
}

interface RawLane {
  row: number; db: number; pan: number; mute: boolean; solo: boolean
  out_portid: number; out: string; active: boolean; physical: boolean; blocks: number
}

interface RawFolder {
  key: string; name: string; is_factory: boolean; is_downloads: boolean
  presets: { position: number; name: string; cloud_id: string }[]
}

function toPreset(raw: unknown): PresetState {
  const p = raw as {
    name: string; folder_key: string; position: number | null; is_factory: boolean
    scene: number; scene_labels: string[]; lanes: RawLane[]
  }
  return {
    name: p.name,
    folderKey: p.folder_key,
    position: p.position,
    isFactory: p.is_factory,
    scene: p.scene,
    sceneLabels: p.scene_labels ?? [],
    lanes: (p.lanes ?? []).map((l) => ({
      row: l.row, db: l.db, pan: l.pan, mute: l.mute, solo: l.solo,
      outPortId: l.out_portid, out: l.out, active: l.active,
      physical: l.physical, blocks: l.blocks
    }))
  }
}

export interface Reply {
  id?: number
  ok?: boolean
  error?: string
  event?: string
  [k: string]: unknown
}
