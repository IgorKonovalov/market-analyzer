import { SidecarPortSchema, SidecarStatusSchema } from './sidecar'

describe('SidecarPortSchema', () => {
  it('accepts a positive port and non-empty secret', () => {
    expect(() => SidecarPortSchema.parse({ port: 12345, secretToken: 'abc' })).not.toThrow()
  })

  it('rejects a zero or negative port', () => {
    expect(() => SidecarPortSchema.parse({ port: 0, secretToken: 'abc' })).toThrow()
    expect(() => SidecarPortSchema.parse({ port: -1, secretToken: 'abc' })).toThrow()
  })

  it('rejects an empty secretToken', () => {
    expect(() => SidecarPortSchema.parse({ port: 5000, secretToken: '' })).toThrow()
  })
})

describe('SidecarStatusSchema', () => {
  it('accepts starting / ready / crashed / fatal without secretToken', () => {
    expect(() => SidecarStatusSchema.parse({ kind: 'starting' })).not.toThrow()
    expect(() => SidecarStatusSchema.parse({ kind: 'ready', pid: 1234 })).not.toThrow()
    expect(() => SidecarStatusSchema.parse({ kind: 'crashed', message: 'boom' })).not.toThrow()
    expect(() => SidecarStatusSchema.parse({ kind: 'fatal', message: 'twice' })).not.toThrow()
  })

  it('rejects crashed without a message', () => {
    expect(() => SidecarStatusSchema.parse({ kind: 'crashed' })).toThrow(/message is required/)
  })

  it('rejects fatal without a message', () => {
    expect(() => SidecarStatusSchema.parse({ kind: 'fatal' })).toThrow(/message is required/)
  })

  it('accepts restarted with a non-empty secretToken', () => {
    expect(() =>
      SidecarStatusSchema.parse({ kind: 'restarted', pid: 9999, secretToken: 'newhex' }),
    ).not.toThrow()
  })

  it('rejects restarted without a secretToken', () => {
    expect(() => SidecarStatusSchema.parse({ kind: 'restarted', pid: 9999 })).toThrow(
      /secretToken is required/,
    )
  })

  it('rejects restarted with an empty secretToken', () => {
    expect(() =>
      SidecarStatusSchema.parse({ kind: 'restarted', pid: 9999, secretToken: '' }),
    ).toThrow()
  })

  it('rejects non-restarted kinds that carry a secretToken', () => {
    expect(() => SidecarStatusSchema.parse({ kind: 'ready', secretToken: 'leaked' })).toThrow(
      /secretToken must be absent/,
    )
  })

  it('accepts refreshed with both port and secretToken (Plan 0007 phase 4.3)', () => {
    expect(() =>
      SidecarStatusSchema.parse({
        kind: 'refreshed',
        port: 53221,
        secretToken: 'newhex',
        pid: 9999,
      }),
    ).not.toThrow()
  })

  it('rejects refreshed without a secretToken', () => {
    expect(() => SidecarStatusSchema.parse({ kind: 'refreshed', port: 53221 })).toThrow(
      /secretToken is required/,
    )
  })

  it('rejects refreshed without a port', () => {
    expect(() => SidecarStatusSchema.parse({ kind: 'refreshed', secretToken: 'newhex' })).toThrow(
      /port is required/,
    )
  })

  it('rejects refreshed with a non-positive port', () => {
    expect(() =>
      SidecarStatusSchema.parse({ kind: 'refreshed', port: 0, secretToken: 'newhex' }),
    ).toThrow()
  })

  it('rejects non-refreshed kinds that carry a port', () => {
    expect(() => SidecarStatusSchema.parse({ kind: 'ready', port: 53221 })).toThrow(
      /port must be absent/,
    )
  })
})
