/**
 * CSP ↔ theme-bootstrap drift guard (Plan 0033 phase 1, ADR-0039).
 *
 * The production CSP header is `script-src 'self'` with NO 'unsafe-inline', so
 * the pre-paint inline theme bootstrap in `renderer/index.html` is admitted by
 * SHA-256 hash. This test recomputes that hash from index.html and asserts the
 * prod policy carries it — if the inline script body is reformatted, the hash
 * changes and this fails, printing the value to paste into THEME_BOOTSTRAP_HASH.
 */
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { prodCsp } from './window'

/** SHA-256 (base64, CSP-quoted) of the bare `<script>` body in index.html. */
function bootstrapHash(): string {
  const html = readFileSync(join(__dirname, '..', 'renderer', 'index.html'), 'utf8')
  const match = html.match(/<script>([\s\S]*?)<\/script>/)
  if (!match) throw new Error('no bare <script> bootstrap found in renderer/index.html')
  const digest = createHash('sha256').update(match[1], 'utf8').digest('base64')
  return `'sha256-${digest}'`
}

describe('prod CSP admits the theme bootstrap by hash', () => {
  const csp = prodCsp(54321)

  it('carries the current bootstrap hash in script-src', () => {
    const expected = bootstrapHash()
    // If this fails, paste `expected` into THEME_BOOTSTRAP_HASH in window.ts.
    expect(csp).toContain(expected)
  })

  it('does not weaken script-src with unsafe-inline', () => {
    const scriptSrc = csp.split('; ').find((d) => d.startsWith('script-src'))
    expect(scriptSrc).toBeDefined()
    expect(scriptSrc).not.toContain("'unsafe-inline'")
    expect(scriptSrc).toContain("'self'")
  })

  it('pins connect-src to the given sidecar port', () => {
    expect(csp).toContain("connect-src 'self' http://127.0.0.1:54321")
  })

  it('does not admit arbitrary https: hosts in img-src (Plan 0072 phase 6)', () => {
    const imgSrc = csp.split('; ').find((d) => d.startsWith('img-src'))
    expect(imgSrc).toBe("img-src 'self' data:")
    expect(imgSrc).not.toContain('https:')
  })
})
