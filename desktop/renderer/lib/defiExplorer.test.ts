import {
  displayPositionId,
  explorerAddressUrl,
  explorerName,
  parsePositionId,
  shortRef,
} from './defiExplorer'

const ADDR = '0x' + 'aB'.repeat(20) // 40 hex, mixed case

describe('parsePositionId', () => {
  it('splits chain:protocol:ref, recognizing a known chain', () => {
    expect(parsePositionId('base:Aerodrome V3:a2f686e487a70f')).toEqual({
      chain: 'base',
      protocol: 'Aerodrome V3',
      ref: 'a2f686e487a70f',
      raw: 'base:Aerodrome V3:a2f686e487a70f',
    })
  })

  it('keeps a ref that itself contains a colon intact', () => {
    expect(parsePositionId('ethereum:uniswap-v3:token:42').ref).toBe('token:42')
  })

  it('marks an unknown first segment as chain=null but still parses the rest', () => {
    const parsed = parsePositionId('solana:orca:pool1')
    expect(parsed.chain).toBeNull()
    expect(parsed.protocol).toBe('orca')
    expect(parsed.ref).toBe('pool1')
  })

  it('fills trailing fields with null when segments are missing', () => {
    expect(parsePositionId('base')).toEqual({
      chain: 'base',
      protocol: null,
      ref: null,
      raw: 'base',
    })
    expect(parsePositionId('base:aerodrome')).toEqual({
      chain: 'base',
      protocol: 'aerodrome',
      ref: null,
      raw: 'base:aerodrome',
    })
  })
})

describe('explorerName', () => {
  it('names each known chain and returns null for none', () => {
    expect(explorerName('base')).toBe('Basescan')
    expect(explorerName('ethereum')).toBe('Etherscan')
    expect(explorerName(null)).toBeNull()
    expect(explorerName(undefined)).toBeNull()
  })
})

describe('explorerAddressUrl', () => {
  it('builds a lower-cased /address/ URL for a valid chain + address', () => {
    expect(explorerAddressUrl('base', ADDR)).toBe(
      `https://basescan.org/address/${ADDR.toLowerCase()}`,
    )
  })

  it('returns null when the address is not a 40-hex 0x value', () => {
    expect(explorerAddressUrl('base', 'a2f686e487a70f')).toBeNull() // group-id hash, not an address
    expect(explorerAddressUrl('base', '0x1234')).toBeNull() // too short
    expect(explorerAddressUrl('base', null)).toBeNull()
  })

  it('returns null when the chain is unknown', () => {
    expect(explorerAddressUrl(null, ADDR)).toBeNull()
  })
})

describe('shortRef', () => {
  it('truncates a long hash but leaves a short ref alone', () => {
    expect(shortRef('a2f686e487a70f4608230fa429e0b9d859dca937')).toBe('a2f686…a937')
    expect(shortRef('weth-usdc')).toBe('weth-usdc')
  })
})

describe('displayPositionId', () => {
  it('shortens only the ref segment, preserving chain and protocol', () => {
    expect(displayPositionId('base:Aerodrome V3:a2f686e487a70f4608230fa429e0b9d859dca937')).toBe(
      'base:Aerodrome V3:a2f686…a937',
    )
  })

  it('leaves an already-short canonical id unchanged (round-trips a friendly id)', () => {
    expect(displayPositionId('base:aerodrome:weth-usdc')).toBe('base:aerodrome:weth-usdc')
  })

  it('returns a sub-three-segment id unchanged', () => {
    expect(displayPositionId('base:aerodrome')).toBe('base:aerodrome')
  })
})
