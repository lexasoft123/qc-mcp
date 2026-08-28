/**
 * electron-builder afterPack hook: ad-hoc sign the macOS bundle.
 *
 * With identity:null electron-builder skips signing entirely, leaving the
 * prebuilt Electron binary's original signature — which our repacked resources
 * invalidate. A *broken* signature plus quarantine makes macOS say "app is
 * damaged" with no right-click escape; a valid ad-hoc signature downgrades that
 * to the ordinary "unidentified developer" prompt.
 */
const { execFileSync } = require('node:child_process')
const path = require('node:path')

module.exports = async function afterPack(context) {
  if (context.electronPlatformName !== 'darwin') return
  // Real signing configured? electron-builder already signed with the actual
  // identity — do not clobber it with an ad-hoc signature.
  if (process.env.CSC_LINK || process.env.CSC_NAME || process.env.CSC_KEY_PASSWORD) {
    console.log('  • real signing identity configured — skipping ad-hoc sign')
    return
  }
  const appPath = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`)
  execFileSync('codesign', ['--force', '--deep', '--sign', '-', appPath], { stdio: 'inherit' })
  execFileSync('codesign', ['--verify', '--deep', '--strict', appPath], { stdio: 'inherit' })
  console.log(`  • ad-hoc signed ${path.basename(appPath)}`)
}
