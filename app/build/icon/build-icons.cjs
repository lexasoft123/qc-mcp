/**
 * Build build/icon.icns and build/icon.ico from forge.html.
 *
 * Two marks, one family: the chain across the full Grid everywhere it can be
 * read, and the chain alone at 16 and 32 px, where the slot field can only turn
 * to mush. Apple simplifies its own icons the same way, and both share the
 * tile, the spike and the light, so they read as one app.
 *
 *   node_modules/.bin/electron build/icon/build-icons.cjs
 */
const { app, BrowserWindow } = require('electron')
const { execFileSync } = require('node:child_process')
const { mkdirSync, rmSync, writeFileSync } = require('node:fs')
const path = require('node:path')

const BUILD = path.join(__dirname, '..')
const SMALL = 32   // at or below this, the slot field is mush — drop it
const variantFor = (px) => (px <= SMALL ? 'small' : 'grid')

/** macOS wants both @1x and @2x of each logical size. */
const ICNS = [
  ['icon_16x16', 16], ['icon_16x16@2x', 32],
  ['icon_32x32', 32], ['icon_32x32@2x', 64],
  ['icon_128x128', 128], ['icon_128x128@2x', 256],
  ['icon_256x256', 256], ['icon_256x256@2x', 512],
  ['icon_512x512', 512], ['icon_512x512@2x', 1024]
]
const ICO = [16, 24, 32, 48, 64, 128, 256]

app.disableHardwareAcceleration()
app.whenReady().then(async () => {
  const win = new BrowserWindow({ show: false, width: 100, height: 100, webPreferences: { offscreen: true } })
  await win.loadFile(path.join(__dirname, 'forge.html'))
  const png = async (px) => {
    const url = await win.webContents.executeJavaScript(
      `window.exportIcon(${JSON.stringify(variantFor(px))}, ${px})`)
    return Buffer.from(url.split(',')[1], 'base64')
  }

  // ── macOS ──
  const iconset = path.join(__dirname, 'Patchbay.iconset')
  rmSync(iconset, { recursive: true, force: true })
  mkdirSync(iconset, { recursive: true })
  for (const [name, px] of ICNS) writeFileSync(path.join(iconset, `${name}.png`), await png(px))
  execFileSync('iconutil', ['-c', 'icns', iconset, '-o', path.join(BUILD, 'icon.icns')], { stdio: 'inherit' })
  rmSync(iconset, { recursive: true, force: true })
  console.log('wrote build/icon.icns')

  // ── Windows ──
  const icoDir = path.join(__dirname, 'ico')
  rmSync(icoDir, { recursive: true, force: true })
  mkdirSync(icoDir, { recursive: true })
  for (const px of ICO) writeFileSync(path.join(icoDir, `${px}.png`), await png(px))
  writeFileSync(path.join(icoDir, 'pack.py'), PACK_PY)
  // uv is already a build dependency (scripts/fetch-uv.mjs), so Pillow costs
  // nothing but a cached wheel and no interpreter has to be present.
  const uv = path.join(BUILD, '..', 'resources', 'uv',
    process.platform === 'win32' ? 'x86_64-pc-windows-msvc'
      : process.arch === 'x64' ? 'x86_64-apple-darwin' : 'aarch64-apple-darwin',
    process.platform === 'win32' ? 'uv.exe' : 'uv')
  execFileSync(uv, ['run', '--quiet', '--with', 'pillow', 'python',
    path.join(icoDir, 'pack.py'), icoDir, path.join(BUILD, 'icon.ico')], { stdio: 'inherit' })
  rmSync(icoDir, { recursive: true, force: true })
  console.log('wrote build/icon.ico')

  // a 1024 png for anything that wants a flat image
  writeFileSync(path.join(BUILD, 'icon.png'), await png(1024))
  console.log('wrote build/icon.png')
  app.quit()
})

const PACK_PY = `"""Pack the rendered PNGs into a multi-size .ico."""
import sys, pathlib
from PIL import Image
src, out = pathlib.Path(sys.argv[1]), sys.argv[2]
pngs = sorted(src.glob("*.png"), key=lambda p: int(p.stem))
imgs = [Image.open(p).convert("RGBA") for p in pngs]
imgs[-1].save(out, format="ICO", sizes=[(i.width, i.height) for i in imgs])
print("packed", len(imgs), "sizes into", out)
`
