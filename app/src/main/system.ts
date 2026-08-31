import { join } from 'node:path'
import type { CortexInfo, DeviceInfo, InstrumentedInfo, Paths } from '../shared/types.js'
import { IS_MAC, QC_PIDS, QC_VID, UV_PYTHON, instrumentedApp, uvBin } from './paths.js'
import { exists, ps, run } from './util.js'

// ── python ──────────────────────────────────────────────────────────────

export interface PythonInfo {
  /** The command that builds the environment: bundled uv, or a system Python. */
  path: string
  version: string | null
  ok: boolean
  /** True when `path` is the bundled uv, which installs its own interpreter. */
  uv: boolean
}

/**
 * What will BUILD the environment — not the interpreter that ends up inside it.
 *
 * The bundled uv wins whenever it is present, and it is the reason this check
 * can pass on a stock machine at all: macOS's /usr/bin/python3 is 3.9.6, under
 * the >=3.10 the package requires, and Windows ships no Python. Falling back to
 * a system Python keeps a plain `git clone` + `npm run dev` working with no
 * fetch step.
 */
export async function findPython(): Promise<PythonInfo> {
  const uv = uvBin()
  if (uv) {
    const r = await run(uv, ['--version'], { timeout: 5000 })
    const v = r.out.trim().match(/uv (\S+)/)?.[1] ?? null
    if (r.code === 0) return { path: uv, version: v, ok: true, uv: true }
  }
  const candidates = IS_MAC
    ? [['python3', []], ['/usr/bin/python3', []]] as const
    : [['py', ['-3']], ['python', []]] as const
  for (const [cmd, pre] of candidates) {
    const r = await run(cmd, [...pre, '--version'], { timeout: 5000 })
    const m = (r.out + r.err).match(/Python (\d+)\.(\d+)\.(\d+)/)
    if (!m) continue
    const ok = Number(m[1]) > 3 || (Number(m[1]) === 3 && Number(m[2]) >= 10)
    return { path: pre.length ? `${cmd} ${pre.join(' ')}` : cmd, version: m[0].replace('Python ', ''), ok, uv: false }
  }
  return { path: IS_MAC ? 'python3' : 'py -3', version: null, ok: false, uv: false }
}

/** Human label for the python check — the uv case has nothing to install. */
export function pythonDetail(p: PythonInfo): string {
  if (p.uv) return `bundled <code>uv ${p.version ?? ''}</code> installs Python ${UV_PYTHON} — nothing to set up`
  return p.version ? `<code>${p.path}</code> · ${p.version}` : `<code>${p.path}</code> — not found`
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
  const absent: DeviceInfo = { present: false, model: null, serial: null, firmware: null }
  const hex = (n: number): string => n.toString(16).toUpperCase().padStart(4, '0')
  const pids = Object.keys(QC_PIDS).map(Number)

  if (IS_MAC) {
    if (!deep) {
      // The device tree WITHOUT -l is a few hundred bytes; -l dumps every
      // property of every USB node and is far too heavy to poll. Every model
      // in the family is named "Quad Cortex ..." in the tree.
      const t = await run('ioreg', ['-p', 'IOUSB', '-w0'], { timeout: 5000 })
      return { ...absent, present: /\bQuad Cortex\b/.test(t.out) }
    }
    const r = await run('ioreg', ['-p', 'IOUSB', '-l', '-w0'], { timeout: 8000 })
    // One node per `+-o` block. Vendor and product have to come from the SAME
    // block: tested separately over the whole dump, an unrelated device sharing
    // this (USB-audio middleware) vendor and any device whose product id
    // happens to collide would together fake a match.
    const nodes = r.out.split('+-o ')
    // ioreg prints the ids in decimal. pids is in QC_PIDS order, so with two
    // models attached this picks the same one as the transports do.
    let found: { pid: number; node: string } | null = null
    for (const p of pids) {
      const node = nodes.find(
        (b) => b.includes(`"idVendor" = ${QC_VID}`) && b.includes(`"idProduct" = ${p}`)
      )
      if (node !== undefined) {
        found = { pid: p, node }
        break
      }
    }
    if (!found) return absent
    const serial = found.node.match(/"USB Serial Number" = "([^"]+)"/)?.[1] ?? null
    return { present: true, model: QC_PIDS[found.pid], serial, firmware: null }
  }

  const filter = pids.map((p) => `$_.InstanceId -like '*VID_${hex(QC_VID)}&PID_${hex(p)}*'`).join(' -or ')
  // Every match, not `-First 1`: with two models attached the first row is
  // whatever PnP happened to return. Choose in QC_PIDS order instead, so the
  // checklist names the same unit the transports open.
  const out = await ps(
    `(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue | ` +
    `Where-Object { ${filter} } | ` +
    `Select-Object -ExpandProperty InstanceId)`
  )
  const ids = out.split(/\r?\n/).map((s) => s.trim()).filter(Boolean)
  if (!ids.length) return absent
  const pid = pids.find((p) => ids.some((i) => i.toUpperCase().includes(`PID_${hex(p)}`)))
  const id = pid === undefined
    ? ids[0]
    : ids.find((i) => i.toUpperCase().includes(`PID_${hex(pid)}`)) ?? ids[0]
  return {
    present: true,
    model: pid === undefined ? null : QC_PIDS[pid],
    serial: id.split('\\').pop() ?? null,
    firmware: null
  }
}
