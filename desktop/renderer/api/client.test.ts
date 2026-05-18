import { ApiError, sanitizeApiErrorBody } from './client'

describe('sanitizeApiErrorBody', () => {
  it('extracts FastAPI detail from a JSON body', () => {
    const out = sanitizeApiErrorBody('{"detail": "symbol not found"}')
    expect(out).toBe('symbol not found')
  })

  it('falls through to the raw body when JSON has no detail', () => {
    const out = sanitizeApiErrorBody('{"error": "something"}')
    expect(out).toBe('{"error": "something"}')
  })

  it('masks absolute Windows paths', () => {
    const out = sanitizeApiErrorBody('failed at C:\\Users\\alice\\AppData\\sidecar\\bar.py')
    expect(out).not.toContain('alice')
    expect(out).not.toContain('AppData')
    expect(out).toContain('<path>')
  })

  it('masks absolute POSIX paths', () => {
    const out = sanitizeApiErrorBody('failed at /Users/alice/code/sidecar/bar.py')
    expect(out).not.toContain('alice')
    expect(out).toContain('<path>')
  })

  it('drops Python traceback frames', () => {
    const body = [
      'Traceback (most recent call last):',
      '  File "/home/runner/app/main.py", line 42, in handler',
      '    raise ValueError("bad symbol")',
      'ValueError: bad symbol',
    ].join('\n')
    const out = sanitizeApiErrorBody(body)
    expect(out).not.toContain('Traceback')
    expect(out).not.toContain('main.py')
    expect(out).toContain('ValueError: bad symbol')
  })

  it('returns "(empty body)" for empty input', () => {
    expect(sanitizeApiErrorBody('')).toBe('(empty body)')
  })

  it('clamps absurdly long bodies', () => {
    const long = 'x'.repeat(2000)
    const out = sanitizeApiErrorBody(long)
    expect(out.length).toBeLessThanOrEqual(280)
    expect(out.endsWith('…')).toBe(true)
  })
})

describe('ApiError', () => {
  it('uses the sanitized body in .message and keeps the raw on .body', () => {
    const raw = '{"detail": "failed at C:\\\\Users\\\\alice\\\\code\\\\main.py line 12"}'
    const err = new ApiError(500, raw)
    expect(err.body).toBe(raw)
    expect(err.message).toContain('sidecar 500:')
    expect(err.message).not.toContain('alice')
    expect(err.message).toContain('<path>')
  })

  it('handles an empty body without losing the status', () => {
    const err = new ApiError(404, '')
    expect(err.message).toBe('sidecar 404: (empty body)')
    expect(err.body).toBe('')
  })
})
