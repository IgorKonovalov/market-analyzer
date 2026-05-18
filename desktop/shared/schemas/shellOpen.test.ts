import { ShellOpenExternalSchema } from './shellOpen'

describe('ShellOpenExternalSchema', () => {
  it('accepts http URLs', () => {
    expect(() => ShellOpenExternalSchema.parse({ url: 'http://example.com' })).not.toThrow()
  })

  it('accepts https URLs', () => {
    expect(() => ShellOpenExternalSchema.parse({ url: 'https://example.com' })).not.toThrow()
  })

  it('rejects file URLs', () => {
    expect(() => ShellOpenExternalSchema.parse({ url: 'file:///etc/passwd' })).toThrow()
  })

  it('rejects javascript URLs', () => {
    expect(() => ShellOpenExternalSchema.parse({ url: 'javascript:alert(1)' })).toThrow()
  })

  it('rejects malformed URLs', () => {
    expect(() => ShellOpenExternalSchema.parse({ url: 'not a url' })).toThrow()
  })
})
