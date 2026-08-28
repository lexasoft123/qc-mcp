/**
 * Render the icon artwork to PNG with Electron's own Chromium — the same
 * renderer the app uses, so what forge.html draws is exactly what ships.
 *   node_modules/.bin/electron build/icon/render.cjs <variant> <size> [outfile]
 */
const { app, BrowserWindow } = require('electron')
const { writeFileSync } = require('node:fs')
const path = require('node:path')

const [variant = 'A', size = '1024', out] = process.argv.slice(2).filter(a => !a.startsWith('--'))

app.disableHardwareAcceleration()
app.whenReady().then(async () => {
  const win = new BrowserWindow({ show: false, width: 100, height: 100, webPreferences: { offscreen: true } })
  await win.loadFile(path.join(__dirname, 'forge.html'))
  // Ask the page which variants it has, so removing one here cannot hang the
  // render on a rejected executeJavaScript that nothing ever settles.
  const have = await win.webContents.executeJavaScript('Object.keys(window.VARIANTS || {})')
  const list = variant === 'all' ? have : [variant]
  try {
    for (const v of list) {
      if (!have.includes(v)) { console.error(`no variant ${v} (have: ${have.join(', ')})`); continue }
      const sheet = String(size) === 'sheet'
      const call = sheet ? `window.exportSheet(${JSON.stringify(v)})`
                         : `window.exportIcon(${JSON.stringify(v)}, ${Number(size)})`
      const url = await win.webContents.executeJavaScript(call)
      const file = out && list.length === 1 ? out
                 : path.join(__dirname, `patchbay-${v}-${size}.png`)
      writeFileSync(file, Buffer.from(url.split(',')[1], 'base64'))
      console.log('wrote', file)
    }
  } catch (e) {
    console.error('render failed:', e.message)
    process.exitCode = 1
  }
  app.quit()
})
