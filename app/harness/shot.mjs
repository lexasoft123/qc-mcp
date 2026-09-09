/*
 * Render a harness page to a PNG, with Electron itself as the browser.
 *
 *   npx electron harness/shot.mjs <url> <out.png> [waitMs=4000] [width=1120] [height=880]
 *
 * For looking at the bench at three window heights without a display attached
 * — the Browser pane is not always there, and a PNG is what a review needs.
 */
import { app, BrowserWindow } from 'electron'
import { writeFileSync } from 'node:fs'

const [url, out, waitMs = '4000', width = '1120', height = '880'] = process.argv.slice(2)
if (!url || !out) { console.error('usage: shot.mjs <url> <out.png> [waitMs] [w] [h]'); process.exit(2) }

app.whenReady().then(async () => {
  const win = new BrowserWindow({ width: Number(width), height: Number(height), show: false,
                                  webPreferences: { offscreen: true } })
  win.webContents.on('console-message', (_e, level, msg) => { if (level >= 2) console.error('[page]', msg) })
  await win.loadURL(url)
  await new Promise((r) => setTimeout(r, Number(waitMs)))
  const img = await win.webContents.capturePage()
  writeFileSync(out, img.toPNG())
  console.log('wrote', out, img.getSize())
  app.quit()
})
