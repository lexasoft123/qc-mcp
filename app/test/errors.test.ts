import { test } from 'node:test'
import assert from 'node:assert/strict'
import { lastErrorLine } from '../src/main/util.ts'

/**
 * The status bar has shown a raw traceback three times now. It gets a test.
 */

const REAL = `Traceback (most recent call last):
  File "/Users/x/qc-mcp/.venv/bin/qc-mcp", line 10, in <module>
    sys.exit(main())
             ~~~~^^
  File "/Users/x/qc-mcp/src/qc_mcp/daemon.py", line 439, in serve
    raise BridgeError(
    ...<2 lines>...
        "the stock app on Windows")
qc_mcp.backend.BridgeError: bridge mode needs Cortex Control running: the instrumented build on macOS (interceptor/run-bridge.sh), the stock app on Windows`

test('the sentence, not the wreckage above it', () => {
  const out = lastErrorLine(REAL)
  assert.equal(out,
    'bridge mode needs Cortex Control running: the instrumented build on macOS ' +
    '(interceptor/run-bridge.sh), the stock app on Windows')
  assert.ok(!out!.includes('Traceback'))
  assert.ok(!out!.includes('<2 lines>'))
  assert.ok(!out!.includes('File "'))
  assert.ok(!out!.includes('BridgeError'))
})

test('a bare message survives untouched', () => {
  assert.equal(lastErrorLine('no such option: --daemon'), 'no such option: --daemon')
})

test('the last exception wins when a traceback nests', () => {
  assert.equal(
    lastErrorLine('ValueError: inner\n\nDuring handling, another occurred:\n\nOSError: outer'),
    'outer')
})

test('empty stderr says nothing rather than something wrong', () => {
  assert.equal(lastErrorLine(''), null)
  assert.equal(lastErrorLine('\n  \n'), null)
})
