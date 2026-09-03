/**
 * Download the `uv` binaries Patchbay ships.
 *
 * Patchbay does not bundle a Python environment (see docs/PACKAGING.md); it
 * bundles the one small tool that can *build* one on a machine that has no
 * usable Python at all — which is the normal case on both platforms we target:
 * macOS's /usr/bin/python3 is 3.9.6, below our >=3.10, and Windows ships none.
 *
 * Pinned version + pinned sha256: a release asset can be re-uploaded, and this
 * binary ends up inside a signed bundle, so "whatever is latest" is not good
 * enough. Bump both together when updating.
 *
 * Writes app/resources/uv/<target>/uv[.exe]. afterPack copies the one matching
 * the build's arch into the bundle, so a universal/lipo step is not needed.
 *
 *   node scripts/fetch-uv.mjs            # the targets this host can build for
 *   node scripts/fetch-uv.mjs --all      # every target (for a cache warm-up)
 */
import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const UV_VERSION = '0.12.7'

/** sha256 of each release archive, from the .sha256 files astral-sh publishes. */
const TARGETS = {
  'aarch64-apple-darwin': {
    archive: `uv-aarch64-apple-darwin.tar.gz`,
    sha256: '127ebdda7ad953cdf198e964b570ea5771b85467ea93eb7cb6d6f8e6f55408f3',
    bin: 'uv'
  },
  'x86_64-apple-darwin': {
    archive: `uv-x86_64-apple-darwin.tar.gz`,
    sha256: '06b8ae1da8c2661c5434507a66f8c2b0b835933bf955b5958a9ac357a37d1959',
    bin: 'uv'
  },
  'x86_64-pc-windows-msvc': {
    archive: `uv-x86_64-pc-windows-msvc.zip`,
    sha256: 'bf1518af459a3915511a11fdc6e2f43ef9a2afa138b9d498eeb9642fe9d85218',
    bin: 'uv.exe'
  }
}

/** The targets a host can build for. Anything else has nothing to fetch. */
const HOST_TARGETS = {
  darwin: ['aarch64-apple-darwin', 'x86_64-apple-darwin'],
  win32: ['x86_64-pc-windows-msvc']
}

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const UV_DIR = join(ROOT, 'resources', 'uv')

const uvPathFor = (target) => join(UV_DIR, target, TARGETS[target].bin)

async function fetchOne(target) {
  const spec = TARGETS[target]
  const dest = uvPathFor(target)
  const stamp = join(UV_DIR, target, '.version')
  if (existsSync(dest) && existsSync(stamp) && readFileSync(stamp, 'utf8').trim() === UV_VERSION) {
    console.log(`  • ${target}: already at ${UV_VERSION}`)
    return
  }
  const url = `https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${spec.archive}`
  process.stdout.write(`  • ${target}: downloading ${spec.archive} … `)
  const res = await fetch(url)
  if (!res.ok) throw new Error(`${url} -> HTTP ${res.status}`)
  const bytes = Buffer.from(await res.arrayBuffer())

  const got = createHash('sha256').update(bytes).digest('hex')
  if (got !== spec.sha256) throw new Error(`sha256 mismatch for ${spec.archive}\n  expected ${spec.sha256}\n  got      ${got}`)
  console.log(`${(bytes.length / 1e6).toFixed(1)} MB, sha256 ok`)

  // bsdtar reads both .tar.gz and .zip, and ships in the box on macOS and on
  // Windows 10 1803+, so one extractor covers every target.
  const work = mkdtempSync(join(tmpdir(), 'uv-'))
  try {
    const archive = join(work, spec.archive)
    writeFileSync(archive, bytes)
    execFileSync('tar', ['-xf', archive, '-C', work], { stdio: 'inherit' })
    // tar.gz nests under uv-<target>/, the zip puts the binaries at the root
    const inner = existsSync(join(work, `uv-${target}`, spec.bin))
      ? join(work, `uv-${target}`, spec.bin)
      : join(work, spec.bin)
    if (!existsSync(inner)) throw new Error(`${spec.bin} not found in ${spec.archive}`)
    mkdirSync(dirname(dest), { recursive: true })
    rmSync(dest, { force: true })
    // copy, not rename: on the Windows runner the temp dir is on C: and the
    // workspace on D:, and rename across devices is EXDEV.
    copyFileSync(inner, dest)
    writeFileSync(stamp, UV_VERSION + '\n', 'utf8')
  } finally {
    rmSync(work, { recursive: true, force: true })
  }
}

async function main() {
  const all = process.argv.includes('--all')
  const wanted = all ? Object.keys(TARGETS) : (HOST_TARGETS[process.platform] ?? [])
  if (!wanted.length) {
    console.log(`uv: nothing to fetch for ${process.platform} (Patchbay ships macOS and Windows only)`)
    return
  }
  console.log(`uv ${UV_VERSION} -> ${UV_DIR}`)
  for (const t of wanted) await fetchOne(t)
}

main().catch((e) => {
  console.error(`\nuv download failed: ${e.message}`)
  process.exit(1)
})
