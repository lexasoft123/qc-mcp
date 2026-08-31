/*
 * The updater's pure decisions. Run with `npm test` (node --test, which strips
 * the types itself — no build step, and nothing here imports Electron).
 *
 * Deliberately outside tsconfig.node.json's `include`, so tsc never sees the
 * .ts import specifier below; node needs the real extension to resolve it.
 */
import { deepEqual, equal, ok } from 'node:assert/strict'
import { describe, it } from 'node:test'
import { newer, parse, reason, safeUrl } from '../src/main/update-rules.ts'

const PAGE = 'https://github.com/lexasoft123/qc-mcp/releases/latest'

describe('parse', () => {
  it('strips the v and the build metadata', () => {
    deepEqual(parse('v1.2.3'), { nums: [1, 2, 3], pre: '' })
    deepEqual(parse('1.2.3+build.7'), { nums: [1, 2, 3], pre: '' })
  })

  it('keeps the whole prerelease, hyphens and all', () => {
    deepEqual(parse('v0.2.0-rc-1'), { nums: [0, 2, 0], pre: 'rc-1' })
  })

  it('pads a short tag and survives a non-numeric one', () => {
    deepEqual(parse('v2'), { nums: [2, 0, 0], pre: '' })
    deepEqual(parse('nightly'), { nums: [0, 0, 0], pre: '' })
  })
})

describe('newer', () => {
  it('compares major, minor and patch in order', () => {
    ok(newer('v0.1.3', '0.1.2'))
    ok(newer('v0.2.0', '0.1.9'))
    ok(newer('v1.0.0', '0.9.9'))
    ok(!newer('v0.1.2', '0.1.3'))
    ok(!newer('v0.1.2', '0.1.2'))
  })

  it('offers the release to someone on its own prerelease', () => {
    // the whole reason this is not a plain three-element loop: parseInt('0-rc1')
    // is 0, so the numbers alone read these two as equal
    ok(newer('v0.2.0', '0.2.0-rc1'))
  })

  it('does not offer a release older than the prerelease in hand', () => {
    ok(!newer('v0.1.0', '0.2.0-rc1'))
  })

  it('does not treat a release as newer than itself', () => {
    ok(!newer('v0.2.0', '0.2.0'))
  })

  it('reads a missing base as 0.0.0 rather than throwing', () => {
    ok(newer('v0.1.2', ''))
  })
})

describe('reason', () => {
  it('keeps the first line of a multi-line error', () => {
    const err = new Error('Cannot find latest.yml: HttpError: 404\nHeaders: {\n  "server": "github.com"\n}')
    equal(reason(err), 'Cannot find latest.yml: HttpError: 404')
  })

  it('caps a single very long line', () => {
    const out = reason(new Error('x'.repeat(500)))
    equal(out.length, 160)
    ok(out.endsWith('…'))
  })

  it('has something to say about a non-Error and an empty message', () => {
    equal(reason('plain string'), 'plain string')
    equal(reason(new Error('')), 'check failed')
    equal(reason(new Error('   \n  ')), 'check failed')
  })
})

describe('safeUrl', () => {
  it('passes GitHub over https through', () => {
    const url = 'https://github.com/lexasoft123/qc-mcp/releases/tag/v0.1.3'
    equal(safeUrl(url, PAGE), url)
    equal(safeUrl('https://api.github.com/x', PAGE), 'https://api.github.com/x')
  })

  it('refuses anything that is not GitHub over https', () => {
    equal(safeUrl('http://github.com/x', PAGE), PAGE)
    equal(safeUrl('file:///etc/passwd', PAGE), PAGE)
    equal(safeUrl('javascript:alert(1)', PAGE), PAGE)
    equal(safeUrl('https://evil.com/x', PAGE), PAGE)
  })

  it('is not fooled by a hostname that merely contains github.com', () => {
    equal(safeUrl('https://github.com.evil.com/x', PAGE), PAGE)
    equal(safeUrl('https://notgithub.com/x', PAGE), PAGE)
  })

  it('falls back on undefined and on unparseable input', () => {
    equal(safeUrl(undefined, PAGE), PAGE)
    equal(safeUrl('not a url', PAGE), PAGE)
  })
})
