/**
 * Plan 0088 phase 5: the recent-wallet store + address validator.
 *
 * Pins: valid/invalid address gating (the client-side pre-fetch gate), the
 * most-recent-first ordering, case-insensitive dedupe, the 5-entry cap, tolerant
 * hydration of a malformed blob, and graceful degradation when localStorage is
 * blocked.
 */
import { isValidAddress, loadRecentWallets, rememberWallet } from './defiWallets'

const A = '0x' + 'a'.repeat(40)
const B = '0x' + 'b'.repeat(40)
const C = '0x' + 'c'.repeat(40)
const D = '0x' + 'd'.repeat(40)
const E = '0x' + 'e'.repeat(40)
const F = '0x' + 'f'.repeat(40)

beforeEach(() => {
  window.localStorage.clear()
})

describe('isValidAddress', () => {
  it('accepts a 0x + 40-hex address (any case), rejects malformed input', () => {
    expect(isValidAddress(A)).toBe(true)
    expect(isValidAddress('0x' + 'A'.repeat(40))).toBe(true)
    expect(isValidAddress(`  ${A}  `)).toBe(true) // trimmed
    expect(isValidAddress('vitalik.eth')).toBe(false)
    expect(isValidAddress('0x123')).toBe(false) // too short
    expect(isValidAddress('0x' + 'g'.repeat(40))).toBe(false) // non-hex
    expect(isValidAddress('')).toBe(false)
  })
})

describe('recent-wallet store', () => {
  it('is empty before anything is remembered', () => {
    expect(loadRecentWallets()).toEqual([])
  })

  it('records most-recent first and persists across loads', () => {
    rememberWallet(A)
    rememberWallet(B)
    expect(loadRecentWallets()).toEqual([B, A])
  })

  it('dedupes case-insensitively, moving a re-analyzed address to the front', () => {
    rememberWallet(A)
    rememberWallet(B)
    rememberWallet('0x' + 'A'.repeat(40)) // same address, upper-case hex (checksum-style)
    expect(loadRecentWallets()).toEqual([A, B]) // A moved to front, stored lowercased
  })

  it('caps the list at five, evicting the oldest', () => {
    for (const addr of [A, B, C, D, E]) rememberWallet(addr)
    rememberWallet(F)
    const recent = loadRecentWallets()
    expect(recent).toHaveLength(5)
    expect(recent[0]).toBe(F)
    expect(recent).not.toContain(A) // the oldest was evicted
  })

  it('ignores a malformed address without mutating the list', () => {
    rememberWallet(A)
    expect(rememberWallet('not-an-address')).toEqual([A])
    expect(loadRecentWallets()).toEqual([A])
  })

  it('tolerates a malformed stored blob by hydrating empty', () => {
    window.localStorage.setItem('ma.defiWallets', '{not json')
    expect(loadRecentWallets()).toEqual([])
    window.localStorage.setItem('ma.defiWallets', JSON.stringify(['garbage', 123, A]))
    expect(loadRecentWallets()).toEqual([A]) // only the valid entry survives
  })
})
