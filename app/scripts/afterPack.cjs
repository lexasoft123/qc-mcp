/**
 * electron-builder afterPack hook: place the arch-matching `uv`, then ad-hoc
 * sign the macOS bundle.
 *
 * uv lands here rather than in extraResources because extraResources is one
 * list for every target, while a mac build produces an arm64 AND an x64 bundle
 * from the same config. afterPack is the first place that knows which arch it
 * is packing. On macOS it must also run BEFORE the signature below, so the
 * binary is covered by it.
 */
const { execFileSync } = require('node:child_process')
const { copyFileSync, existsSync, mkdirSync, readdirSync, rmSync, statSync } = require('node:fs')
const path = require('node:path')
const { Arch } = require('electron-builder')

const UV_TARGETS = {
  'darwin-arm64': 'aarch64-apple-darwin',
  'darwin-x64': 'x86_64-apple-darwin',
  'win32-x64': 'x86_64-pc-windows-msvc'
}

/** Resources/ inside the packed bundle, per platform. */
function resourcesDir(context) {
  return context.electronPlatformName === 'darwin'
    ? path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`, 'Contents', 'Resources')
    : path.join(context.appOutDir, 'resources')
}

function placeUv(context) {
  const platform = context.electronPlatformName
  const arch = Arch[context.arch]
  const target = UV_TARGETS[`${platform}-${arch}`]
  if (!target) throw new Error(`no uv target for ${platform}-${arch}`)

  const exe = platform === 'win32' ? 'uv.exe' : 'uv'
  const src = path.join(__dirname, '..', 'resources', 'uv', target, exe)
  if (!existsSync(src)) {
    // Not a warning: a build that silently omits uv produces an app that tells
    // the user to go install Python by hand, which is the thing uv is here to
    // prevent — and nothing about the finished bundle would reveal it.
    throw new Error(
      `${src} is missing — run "npm run fetch:uv" before packaging.\n` +
      `  (set PATCHBAY_NO_UV=1 to build without it, on purpose.)`
    )
  }
  const dir = path.join(resourcesDir(context), 'uv')
  mkdirSync(dir, { recursive: true })
  copyFileSync(src, path.join(dir, exe))
  console.log(`  • bundled uv (${target})`)
}

/**
 * Drop the Chromium locales we do not ship — ~40 MB of real bytes across 54
 * .pak files, for an app whose own strings are English only.
 *
 * electron-builder's own `electronLanguages` cannot do this on macOS: it looks
 * in Contents/Resources, which holds nothing but EMPTY stub .lproj directories,
 * so it deletes 54 zero-byte folders and reports success while every actual
 * locale.pak survives inside the framework. The real ones live here. On Windows
 * its path (resources/../locales) is right, so this is a no-op there.
 *
 * Must run before the signature below — removing files from a signed bundle
 * invalidates it.
 */
function trimLocales(context) {
  if (context.electronPlatformName !== 'darwin') return
  const wanted = new Set(
    (context.packager.config.electronLanguages || ['en']).concat('en')
  )
  const dir = path.join(
    resourcesDir(context), '..', 'Frameworks', 'Electron Framework.framework',
    'Versions', 'A', 'Resources'
  )
  if (!existsSync(dir)) return
  let freed = 0
  let removed = 0
  for (const entry of readdirSync(dir)) {
    if (!entry.endsWith('.lproj')) continue
    if (wanted.has(entry.slice(0, -'.lproj'.length))) continue
    const full = path.join(dir, entry)
    for (const f of readdirSync(full)) {
      try { freed += statSync(path.join(full, f)).size } catch { /* gone */ }
    }
    rmSync(full, { recursive: true, force: true })
    removed++
  }
  if (removed) console.log(`  • dropped ${removed} Chromium locales (${(freed / 1e6).toFixed(1)} MB)`)
}

module.exports = async function afterPack(context) {
  if (process.env.PATCHBAY_NO_UV === '1') {
    console.log('  • PATCHBAY_NO_UV=1 — building without uv; setup will need a system Python 3.10+')
  } else {
    placeUv(context)
  }

  trimLocales(context)

  if (context.electronPlatformName !== 'darwin') return
  // Real signing configured? electron-builder signs AFTER this hook (afterPack
  // then signApp, in platformPackager), so an ad-hoc pass here would just be
  // ~200 MB of deep-signing thrown away moments later. CSC_KEYCHAIN is in the
  // list because that — with a bare CSC_NAME — is how release CI hands over the
  // identity; it deliberately never sets CSC_LINK. See docs/MACOS-SIGNING.md.
  if (
    process.env.CSC_LINK ||
    process.env.CSC_NAME ||
    process.env.CSC_KEYCHAIN ||
    process.env.CSC_KEY_PASSWORD
  ) {
    console.log('  • real signing identity configured — skipping ad-hoc sign')
    return
  }
  // Otherwise this signature is the final one, and it has to exist. When
  // electron-builder finds no Developer ID identity it logs "skipped macOS
  // application code signing" and leaves the bundle alone — carrying the
  // prebuilt Electron binary's original signature, which our repacked
  // resources invalidate. A *broken* signature plus quarantine makes macOS say
  // "app is damaged" with no right-click escape; a valid ad-hoc signature
  // downgrades that to the ordinary "unidentified developer" prompt.
  const appPath = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`)
  execFileSync('codesign', ['--force', '--deep', '--sign', '-', appPath], { stdio: 'inherit' })
  execFileSync('codesign', ['--verify', '--deep', '--strict', appPath], { stdio: 'inherit' })
  console.log(`  • ad-hoc signed ${path.basename(appPath)}`)
}
