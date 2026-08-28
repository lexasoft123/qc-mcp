import { join } from 'node:path'
import type { CortexInfo, DeviceInfo, InstrumentedInfo, Paths } from '../shared/types.js'
import { IS_MAC, QC_PID, QC_VID, instrumentedApp } from './paths.js'
import { exists, ps, run } from './util.js'

// ── python ──────────────────────────────────────────────────────────────

export interface PythonInfo { path: string; version: string | null; ok: boolean }

/** The interpreter used to CREATE the venv, not the one inside it. */
export async function findPython(): Promise<PythonInfo> {
  const candidates = IS_MAC
    ? [['python3', []], ['/usr/bin/python3', []]] as const
    : [['py', ['-3']], ['python', []]] as const
  for (const [cmd, pre] of candidates) {
    const r = await run(cmd, [...pre, '--version'], { timeout: 5000 })
    const m = (r.out + r.err).match(/Python (\d+)\.(\d+)\.(\d+)/)
    if (!m) continue
    const ok = Number(m[1]) > 3 || (Number(m[1]) === 3 && Number(m[2]) >= 10)
    return { path: pre.length ? `${cmd} ${pre.join(' ')}` : cmd, version: m[0].replace('Python ', ''), ok }
  }
  return { path: IS_MAC ? 'python3' : 'py -3', version: null, ok: false }
}

export async function hasClang(): Promise<boolean> {
  if (!IS_MAC) return true
  const r = await run('clang', ['--version'], { timeout: 5000 })
  return r.code === 0
}

// ── Cortex Control ──────────────────────────────────────────────────────

async function bundleVersion(appPath: string): Promise<string | null> {
  if (!exists(appPath)) return null
  if (IS_MAC) {
    const r = await run('defaults', ['read', join(appPath, 'Contents', 'Info'), 'CFBundleShortVersionString'], { timeout: 5000 })
    return r.code === 0 ? r.out.trim() : null
  }
  const v = await ps(`(Get-Item '${appPath.replace(/'/g, "''")}').VersionInfo.ProductVersion`)
  return v || null
}

/**
 * Whether the instrumented copy can actually be injected into. Both halves
 * matter: the hardened runtime must be OFF and library validation disabled,
 * or DYLD_INSERT_LIBRARIES is ignored and bridge mode silently never starts.
 */
async function readInstrumented(repo: string): Promise<InstrumentedInfo> {
  const path = instrumentedApp(repo)
  if (!IS_MAC || !exists(path)) {
    return { built: false, version: null, hardenedRuntimeOff: false, libraryValidationOff: false }
  }
  const sign = await run('codesign', ['-dvvv', path], { timeout: 8000 })
  const flags = (sign.out + sign.err).match(/flags=([^\s]+)/)?.[1] ?? ''
  const ents = await run('codesign', ['-d', '--entitlements', ':-', path], { timeout: 8000 })
  return {
    built: true,
    version: await bundleVersion(path),
    hardenedRuntimeOff: !/runtime/i.test(flags),
    libraryValidationOff: (ents.out + ents.err).includes('disable-library-validation')
  }
}

/** pid of a running Cortex Control, instrumented or stock. Cheap: polled. */
export async function cortexPid(repo: string): Promise<{ pid: number | null; instrumented: boolean }> {
  if (IS_MAC) {
    const r = await run('pgrep', ['-f', 'Contents/MacOS/Cortex Control'], { timeout: 5000 })
    const pids = r.out.trim().split('\n').filter(Boolean).map(Number)
    for (const pid of pids) {
      const c = await run('ps', ['-o', 'command=', '-p', String(pid)], { timeout: 5000 })
      if (c.out.includes('CortexControl-instrumented.app')) return { pid, instrumented: true }
    }
    return { pid: pids[0] ?? null, instrumented: false }
  }
  const out = await ps('(Get-Process -Name "Cortex Control" -ErrorAction SilentlyContinue | Select-Object -First 1).Id')
  const pid = Number(out)
  return { pid: Number.isFinite(pid) && pid > 0 ? pid : null, instrumented: false }
}

export async function readCortex(paths: Paths): Promise<CortexInfo> {
  const version = await bundleVersion(paths.cortex)
  const instrumented = IS_MAC ? await readInstrumented(paths.repo) : null
  const { pid } = await cortexPid(paths.repo)
  return {
    installed: exists(paths.cortex),
    path: paths.cortex,
    version,
    running: pid !== null,
    pid,
    instrumented,
    // a version drift means the injected copy is running last week's app
    needsRebuild: Boolean(
      instrumented?.built && version && instrumented.version && instrumented.version !== version
    )
  }
}

// ── the device ──────────────────────────────────────────────────────────

/**
 * Is the Quad Cortex on USB? Deliberately does NOT open the device: the daemon
 * or Cortex Control may hold it, and probing would fight them for the handle.
 */
export async function readDevice(deep = true): Promise<DeviceInfo> {
  if (IS_MAC) {
    if (!deep) {
      // The device tree WITHOUT -l is a few hundred bytes; -l dumps every
      // property of every USB node and is far too heavy to poll.
      const t = await run('ioreg', ['-p', 'IOUSB', '-w0'], { timeout: 5000 })
      return { present: /\bQuad Cortex\b/.test(t.out), serial: null, firmware: null }
    }
    const r = await run('ioreg', ['-p', 'IOUSB', '-l', '-w0'], { timeout: 8000 })
    const present = r.out.includes(`"idVendor" = ${QC_VID}`) && r.out.includes(`"idProduct" = ${QC_PID}`)
    if (!present) return { present: false, serial: null, firmware: null }
    const idx = r.out.indexOf(`"idProduct" = ${QC_PID}`)
    const serial = r.out.slice(Math.max(0, idx - 4000), idx + 4000)
      .match(/"USB Serial Number" = "([^"]+)"/)?.[1] ?? null
    return { present: true, serial, firmware: null }
  }
  const hex = (n: number): string => n.toString(16).toUpperCase().padStart(4, '0')
  const id = await ps(
    `(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | ` +
    `Where-Object { $_.InstanceId -like '*VID_${hex(QC_VID)}&PID_${hex(QC_PID)}*' } | ` +
    `Select-Object -First 1 -ExpandProperty InstanceId)`
  )
  if (!id) return { present: false, serial: null, firmware: null }
  return { present: true, serial: id.split('\\').pop() ?? null, firmware: null }
}
