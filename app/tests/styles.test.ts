import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

/**
 * Every blurred surface has a Windows override.
 *
 * `backdrop-filter: blur()` re-rasters everything behind the surface on every
 * frame. Measured on the field laptop — an HD 4600 driving a QHD+ panel — one
 * blurred card is a visible hitch; the app keeps solid surfaces under
 * `body.win`. A new glass panel without its override is exactly the kind of
 * regression nobody sees on a Mac, so this reads the stylesheet and checks.
 */
const css = readFileSync(fileURLToPath(new URL('../src/renderer/src/styles.css', import.meta.url)), 'utf8')

/** Top-level rules as [selector, body], skipping at-blocks' internals. */
function rules(src: string): [string, string][] {
  const out: [string, string][] = []
  const clean = src.replace(/\/\*[\s\S]*?\*\//g, '')
  let i = 0
  while (i < clean.length) {
    const open = clean.indexOf('{', i)
    if (open < 0) break
    const sel = clean.slice(i, open).trim()
    let depth = 0, j = open
    for (; j < clean.length; j++) {
      if (clean[j] === '{') depth++
      else if (clean[j] === '}' && --depth === 0) break
    }
    const body = clean.slice(open + 1, j)
    if (sel.startsWith('@')) out.push(...rules(body))
    else out.push([sel, body])
    i = j + 1
  }
  return out
}

test('every backdrop-filter: blur() selector has a body.win override to none', () => {
  const all = rules(css)
  const blurred = all.filter(([, b]) => /backdrop-filter:\s*blur\(/.test(b)).flatMap(([s]) => s.split(',').map((x) => x.trim()))
  assert.ok(blurred.length > 0, 'the app has glass surfaces; the test would be vacuous without them')
  const overridden = new Set(
    all.filter(([s, b]) => s.includes('body.win') && /backdrop-filter:\s*none/.test(b))
       .flatMap(([s]) => s.split(',').map((x) => x.trim().replace(/^body\.win\s+/, '')))
  )
  for (const sel of blurred) {
    assert.ok(overridden.has(sel), `${sel} blurs but has no "body.win ${sel} { backdrop-filter: none }"`)
  }
})

test('the leveling bench adds no blur of its own', () => {
  // The bench is meant to run on that laptop for a whole set. Its surfaces
  // are plain alpha; if a blur creeps in, it needs an override AND a reason.
  const bench = rules(css).filter(([s]) => /^\.(lvl|irail|xport|bt-|drw|dock)/.test(s))
  for (const [s, b] of bench) assert.ok(!/backdrop-filter:\s*blur\(/.test(b), `${s} blurs`)
})
