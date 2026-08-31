import { type ChildProcess, spawn } from 'node:child_process'
import { connect } from 'node:net'
import { mkdirSync, readFileSync, unlinkSync } from 'node:fs'
import { dirname } from 'node:path'
import type { DaemonInfo, Mode, Paths, SessionMode } from '../shared/types.js'
import { IS_MAC } from './paths.js'
import { exists, sleep } from './util.js'

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

export class Daemon {
  private child: ChildProcess | null = null
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

  async start(onChange: () => void): Promise<void> {
    if (this.state !== 'stopped') return
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
    const child = spawn(this.paths.bin, ['--daemon', '--socket', socketPath, '--mode', this.mode], {
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
        this.error = stderr.trim().split('\n').slice(-3).join(' ').slice(0, 400)
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

  stop(): void {
    this.state = 'stopped'
    this.startedAt = null
    this.session = null
    const child = this.child
    this.child = null
    if (child && child.pid) {
      try { child.kill('SIGTERM') } catch { /* already gone */ }
      setTimeout(() => { try { child.kill('SIGKILL') } catch { /* already gone */ } }, 2000)
    }
    const socketPath = this.liveSocket ?? this.paths.socket
    this.liveSocket = null
    if (IS_MAC) { try { unlinkSync(socketPath) } catch { /* nothing to clean */ } }
  }
}
