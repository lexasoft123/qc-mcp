import { type ChildProcess, spawn } from 'node:child_process'
import { connect } from 'node:net'
import { mkdirSync, readFileSync, unlinkSync } from 'node:fs'
import { dirname } from 'node:path'
import type { DaemonInfo, Mode, Paths, SessionMode } from '../shared/types.js'
import { read as readLock } from './lock.js'
import { IS_MAC } from './paths.js'
import { exists, lastErrorLine, sleep } from './util.js'

/**
 * Supervises the long-lived qc-mcp daemon — the process that owns the device
 * (directly, or through Cortex Control's session) and serves every MCP client
 * over one socket.
 *
 * The daemon entry point is the ONE piece of this launcher that the qc-mcp
 * package does not ship yet: `qc-mcp` today is a stdio MCP server started by
 * each client. This module is written against the contract the server needs to
 * grow, and reports honestly when the installed build does not have it:
 *
 *     qc-mcp --daemon --socket <path>     hold the device, serve clients
 *     qc-mcp --attach --socket <path>     stdio shim that proxies to the daemon
 *
 * Nothing here fakes a running daemon. If the binary rejects --daemon, `state`
 * stays 'stopped', `supported` goes false and `error` carries its stderr.
 */
/** Windows has no AF_UNIX everywhere, so the daemon writes its loopback port
 *  next to the endpoint file. */
function portFor(socketPath: string): number {
  try {
    return Number(readFileSync(`${socketPath}.port`, 'utf8').trim())
  } catch {
    return 0
  }
}

/** Who the lock says holds the device, in the shape evict() wants. */
const lockOwner = (socket: string): { pid: number; owner: string } | null => {
  const l = readLock(socket)
  return l ? { pid: l.pid, owner: l.owner } : null
}

export class Daemon {
  private child: ChildProcess | null = null
  /** Running, but not ours: adopted rather than spawned. */
  private external = false
  /** The endpoint the RUNNING process was spawned with. `paths` can change
   *  under us (the poll re-reads Preferences), and cleaning up the new path
   *  would leave the real socket behind for endpointUp() to believe in. */
  private liveSocket: string | null = null
  private startedAt: number | null = null
  private supported = true
  private error: string | null = null
  private mode: Mode = 'bridge'
  /** What the daemon said it actually opened — `auto` only resolves there. */
  private session: SessionMode | null = null
  private state: DaemonInfo['state'] = 'stopped'
  private clientNames: string[] = []
  /** One start at a time. Two overlapping presses used to have the second
   *  return instantly on `state !== 'stopped'`, so its caller reported a
   *  finished connect over a start that was still in flight. */
  private starting: Promise<void> | null = null

  constructor(private paths: Paths) {}

  setPaths(paths: Paths): void { this.paths = paths }
  setMode(mode: Mode): void { this.mode = mode }
  setClients(names: string[]): void { this.clientNames = names }

  info(): DaemonInfo {
    return {
      state: this.state,
      pid: this.child?.pid ?? null,
      startedAt: this.startedAt,
      socket: this.paths.socket,
      mode: this.mode,
      session: this.session,
      external: this.external,
      supported: this.supported,
      error: this.error,
      reportsPerSecond: 0,
      clients: this.clientNames
    }
  }

  /**
   * Actually connect. A socket file outlives a crashed daemon, so existence
   * alone lies — the same trap the repo already documents for the bridge FIFOs.
   */
  private endpointUp(): Promise<boolean> {
    if (!exists(this.paths.socket)) return Promise.resolve(false)
    return new Promise((resolve) => {
      const done = (v: boolean): void => { try { sock.destroy() } catch { /* gone */ } resolve(v) }
      const sock = IS_MAC
        ? connect(this.paths.socket)
        : connect(portFor(this.paths.socket), '127.0.0.1')
      sock.setTimeout(1000)
      sock.once('connect', () => done(true))
      sock.once('error', () => done(false))
      sock.once('timeout', () => done(false))
    })
  }

  /**
   * Is something already serving on our socket?
   *
   * A daemon started by hand — or left behind by a previous run of this app — is
   * a perfectly good daemon, and deleting its socket to start our own is both
   * rude and confusing: the UI said "stopped" while the device was plainly in
   * use. Connecting is the only honest test; the socket file existing is not.
   */
  private probe(timeoutMs = 700): Promise<boolean> {
    if (!IS_MAC) return Promise.resolve(false)
    return new Promise((resolve) => {
      const sock = connect(this.paths.socket)
      const done = (ok: boolean): void => {
        sock.removeAllListeners()
        sock.destroy()
        resolve(ok)
      }
      sock.setTimeout(timeoutMs, () => done(false))
      sock.once('connect', () => done(true))
      sock.once('error', () => done(false))
    })
  }

  /** Does the endpoint answer? The socket FILE existing proves nothing — it
   *  outlives the process that made it, and a process can outlive its socket. */
  reachable(): Promise<boolean> { return this.endpointUp() }

  /** Take over reporting for a daemon we did not spawn. */
  private adopt(onChange: () => void): void {
    this.state = 'running'
    this.external = true
    this.error = null
    this.startedAt = this.startedAt ?? Date.now()
    this.liveSocket = this.paths.socket
    onChange()
  }

  start(onChange: () => void): Promise<void> {
    if (this.starting) return this.starting
    if (this.state === 'running') return Promise.resolve()
    const run = this.begin(onChange).finally(() => { this.starting = null })
    this.starting = run
    return run
  }

  private async begin(onChange: () => void): Promise<void> {
    if (await this.probe()) { this.adopt(onChange); return }
    if (!exists(this.paths.bin)) {
      this.error = `qc-mcp is not installed at ${this.paths.bin} — run setup first.`
      onChange()
      return
    }
    this.state = 'starting'
    this.error = null
    this.session = null
    onChange()

    if (IS_MAC) {
      try { mkdirSync(dirname(this.paths.socket), { recursive: true }) } catch { /* already there */ }
      try { unlinkSync(this.paths.socket) } catch { /* no stale socket */ }
    }

    const socketPath = this.paths.socket
    this.liveSocket = socketPath
    let stderr = ''
    // --launched-by is what makes the lock able to say "ours": everything else
    // on this machine that opens the device is somebody's to take over, not
    // ours to stop.
    const child = spawn(this.paths.bin,
      ['--daemon', '--socket', socketPath, '--mode', this.mode, '--launched-by', 'patchbay'], {
      cwd: this.paths.repo,
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: false
    })
    this.child = child
    // The daemon announces what it resolved on stdout:
    //   qc-mcp daemon listening on <path> (mode=bridge|shared|direct, firmware=…)
    // Without reading it every view has to guess from the preference, and `auto`
    // then reads as bridge even when the daemon opened the device directly.
    child.stdout?.on('data', (d) => {
      const m = /\bmode=(bridge|shared|direct)\b/.exec(String(d))
      if (m && m[1] !== this.session) {
        this.session = m[1] as SessionMode
        onChange()
      }
    })
    child.stderr?.on('data', (d) => { stderr += String(d) })
    child.on('error', (e) => { stderr += String(e) })
    child.on('exit', () => {
      this.child = null
      this.startedAt = null
      if (this.state !== 'stopped') {
        this.state = 'stopped'
        this.error = (lastErrorLine(stderr) ?? '').slice(0, 400)
          || 'The daemon exited immediately.'
        // an immediate exit with an argument error means this build has no daemon
        if (/unrecognized arguments|no such option|--daemon/i.test(stderr)) this.supported = false
        onChange()
      }
    })

    // Wait generously: serve() opens the device, handshakes and detects the
    // firmware BEFORE it listens, so a busy device or a cold USB bus can push
    // first light well past a few seconds.
    for (let i = 0; i < 150; i++) {
      if (this.child === null) return
      if (await this.endpointUp()) {
        this.state = 'running'
        this.startedAt = Date.now()
        onChange()
        return
      }
      await sleep(200)
    }
    // A timeout is not proof the entry point is missing — only an argument
    // error is (handled on 'exit'). Leave `supported` alone so one slow start
    // does not disable autoconnect for the rest of the session.
    this.error =
      `${socketPath} never opened, 30s after starting ${this.paths.bin} --daemon. ` +
      'Check Logs, then try again.'
    this.stop()
    onChange()
  }

  /**
   * End the session the lock names — somebody else's.
   *
   * By PID, from the record they wrote. The old code reached for
   * `pkill -f 'qc-mcp --daemon'`, which is both too broad (every daemon on the
   * machine) and too narrow (it never matched an MCP server that had opened the
   * device itself). Returns a sentence if it will not go.
   */
  async evict(owner: { pid: number; owner: string } | null): Promise<string | null> {
    if (!owner) return null
    if (owner.pid === process.pid) return null
    try {
      process.kill(owner.pid, 'SIGTERM')
    } catch (e) {
      const code = (e as NodeJS.ErrnoException).code
      if (code === 'ESRCH') return null                 // already gone
      if (code === 'EPERM') return `The ${owner.owner} holding the device belongs to another user.`
      return `Could not stop the ${owner.owner} holding the device: ${String(e)}`
    }
    for (let i = 0; i < 40; i++) {
      try { process.kill(owner.pid, 0) } catch { return null }
      await sleep(250)
    }
    return `The ${owner.owner} holding the device (pid ${owner.pid}) did not stop.`
  }

  /**
   * Stop it — including one we only adopted.
   *
   * This used to kill `child` and then unlink the socket unconditionally. For an
   * ADOPTED daemon there is no child, so the daemon kept running and holding the
   * device while its socket file was deleted out from under it: probe() could
   * never find it again, the UI said 'stopped' forever, and every later connect
   * failed on a device that was plainly in use. Now an adopted daemon is stopped
   * by pid, and a socket we did not create is never removed.
   */
  async stop(): Promise<void> {
    const wasExternal = this.external
    const child = this.child
    this.state = 'stopped'
    this.startedAt = null
    this.session = null
    this.child = null
    this.external = false

    if (child && child.pid) {
      try { child.kill('SIGTERM') } catch { /* already gone */ }
      setTimeout(() => { try { child.kill('SIGKILL') } catch { /* already gone */ } }, 2000)
    } else if (wasExternal) {
      // Adopted: no child to kill, so use the pid in the lock. `pkill -f
      // 'qc-mcp --daemon'` used to stand in for this — every daemon on the
      // machine, and never the MCP server that had opened the device itself.
      await this.evict(lockOwner(this.paths.socket))
      // it owns the socket, so it cleans up after itself; wait for the endpoint
      // to actually close rather than deleting the file and calling it stopped
      for (let i = 0; i < 25 && (await this.endpointUp()); i++) await sleep(200)
      this.liveSocket = null
      return
    }

    const socketPath = this.liveSocket
    this.liveSocket = null
    if (IS_MAC && socketPath) { try { unlinkSync(socketPath) } catch { /* nothing to clean */ } }
  }
}
