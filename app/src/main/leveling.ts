import { type ChildProcess, spawn } from 'node:child_process'
import type {
  ApplyResult, AudioState, AutoResult, LevelEvent, Measurement, Paths, PresetFolder, PresetState,
  ReportResult, RiffList, SampleState, SceneRow
} from '../shared/types.js'
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

  // ── the measured half ───────────────────────────────────────────────────
  // These need the optional audio extra. The service answers with a plain
  // `available: false` rather than failing, so the view can explain itself.

  async audio(): Promise<AudioState> {
    const r = await this.call('audio', {}, 10000)
    return {
      available: r.available === true,
      error: r.error as string | undefined,
      hint: r.hint as string | undefined,
      quadCortex: (r.quad_cortex ?? null) as AudioState['quadCortex'],
      sample: (r.sample ?? null) as string | null
    }
  }

  async sampleArm(opts: { thresholdDbfs?: number; maxSeconds?: number } = {}) {
    return (await this.call('sample_arm', {
      threshold_dbfs: opts.thresholdDbfs ?? -40, max_seconds: opts.maxSeconds ?? 30
    }, 10000)) as unknown as SampleState
  }

  async sampleStatus(): Promise<SampleState> {
    return (await this.call('sample_status', {}, 8000)) as unknown as SampleState
  }

  /** Facts and envelope for the riff on disk, whoever recorded it and whenever. */
  async sampleInfo(): Promise<SampleState> {
    return (await this.call('sample_info', {}, 20000)) as unknown as SampleState
  }

  async sampleStop(): Promise<SampleState> {
    return (await this.call('sample_stop', {}, 20000)) as unknown as SampleState
  }

  async sampleDiscard(): Promise<SampleState> {
    return (await this.call('sample_discard', {}, 8000)) as unknown as SampleState
  }

  /**
   * Play the riff through the preset and measure what comes back. Writes nothing.
   *
   * No row: the service finds the lane the signal enters on and the one it leaves
   * by, which are routinely different. Naming a row here would only get that wrong.
   */
  async measure(perceived = false): Promise<Measurement> {
    const r = await this.call('measure', { perceived }, 120000)
    return (r.measurement ?? r) as unknown as Measurement
  }

  /**
   * Measure, trim, verify — until the preset lands on target.
   *
   * Slow by nature: every iteration plays the whole riff. The service emits an
   * `autolevel` event per pass so the view can follow rather than freeze, which
   * is why the timeout here is generous.
   */
  /** Play the riff through the preset so it can be heard. Returns once started. */
  async samplePlay(): Promise<{ playing: boolean; seconds: number }> {
    return (await this.call('sample_play', {}, 20000)) as unknown as
      { playing: boolean; seconds: number }
  }

  async sampleStopPlay(): Promise<void> {
    await this.call('sample_stop_play', {}, 8000)
  }

  /** Put back every fader an applied trim moved. */
  async revertLevels(): Promise<{ reverted: { row: number; db: number }[] }> {
    return (await this.call('revert_levels', {}, 15000)) as unknown as
      { reverted: { row: number; db: number }[] }
  }

  /** Measure every preset given and report what each needs. Writes nothing. */
  async measureMany(
    presets: { folder_key: string; position: number; name: string; cloud_id?: string }[],
    o: { target?: number; metric?: string } = {}
  ): Promise<ReportResult> {
    return (await this.call('measure_many', {
      presets, target: o.target ?? -18, metric: o.metric ?? 'lufs'
      // Generous: every preset is a recall plus a full playback of the riff.
    }, 600000)) as unknown as ReportResult
  }

  /** Measure every scene of the loaded preset. Writes nothing. */
  async measureScenes(o: { target?: number; scenes?: number[] } = {}):
    Promise<{ rows: SceneRow[] }> {
    return (await this.call('measure_scenes', {
      target: o.target ?? -18, scenes: o.scenes
    }, 600000)) as unknown as { rows: SceneRow[] }
  }

  /** Write the per-scene trims. Explicit: nothing here happens by default. */
  async levelScenes(o: { target?: number; scenes?: number[] } = {}): Promise<unknown> {
    return await this.call('level_scenes', {
      target: o.target ?? -18, scenes: o.scenes, dry_run: false
    }, 600000)
  }

  /**
   * Write one proposed correction, relative to where the fader is now.
   *
   * Separate from autolevel on purpose: the report shows a number, the user may
   * change it, and Apply has to write THAT. Re-measuring here would let the
   * table describe a change nobody is going to make.
   */
  /** The riffs the package ships, so nobody needs a guitar to start. */
  async riffs(): Promise<RiffList> {
    return (await this.call('riffs', {}, 30000)) as unknown as RiffList
  }

  async useRiff(name: string): Promise<SampleState> {
    // Rendering the first time takes a moment of pure numpy; after that it is
    // a file copy.
    return (await this.call('use_riff', { name }, 60000)) as unknown as SampleState
  }

  async applyTrim(o: {
    folderKey?: string; position?: number; isFactory?: boolean; cloudId?: string
    row?: number; db: number
  }): Promise<ApplyResult> {
    return (await this.call('apply_trim', {
      folder_key: o.folderKey, position: o.position,
      is_factory: o.isFactory ?? false, cloud_id: o.cloudId ?? '',
      row: o.row, db: o.db
    }, 120000)) as unknown as ApplyResult
  }

  async autolevel(opts: {
    target?: number; metric?: string; tolerance?: number; dryRun?: boolean
  } = {}): Promise<AutoResult> {
    return (await this.call('autolevel', {
      target: opts.target ?? -18, metric: opts.metric ?? 'lufs',
      // Reports by default: moving the player's faders is a separate decision
      // from measuring, so it has to be asked for.
      tolerance: opts.tolerance ?? 0.5, dry_run: opts.dryRun ?? true
    }, 300000)) as unknown as AutoResult
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
