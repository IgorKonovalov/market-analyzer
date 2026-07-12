/**
 * Recent-wallet store for the DeFi P&L view (Plan 0088 phase 5).
 *
 * A small, bounded list of recently-analyzed `0x…` addresses, persisted in
 * `localStorage['ma.defiWallets']` (ADR-0039's `ma.*` presentation-pref
 * convention — renderer-owned, never on the wire). Shaped like the other
 * `ma.*` stores (`userOverlays.ts`, `theme.ts`): every `localStorage` access is
 * wrapped so a blocked/privacy context degrades to session-only rather than
 * throwing, and the parsed value is sanitized to valid, deduped, capped entries.
 *
 * The address validator is exported so the view's client-side gate and this
 * store share one regex — the same shape the sidecar enforces
 * (`EVM_ADDRESS_PATTERN`), so an address that passes here is fetchable.
 */

const STORAGE_KEY = 'ma.defiWallets'
const MAX_RECENT = 5

/** A raw EVM address: `0x` + exactly 40 hex chars (the route's `EVM_ADDRESS_PATTERN`). */
const ADDRESS_RE = /^0x[0-9a-fA-F]{40}$/

/** True when `address` is a well-formed `0x…` EVM address (trimmed). */
export function isValidAddress(address: string): boolean {
  return ADDRESS_RE.test(address.trim())
}

/** Normalize for storage + case-insensitive dedupe: trimmed, lowercased. */
function normalize(address: string): string {
  return address.trim().toLowerCase()
}

/** Coerce an unknown parsed value into a clean, deduped, capped address list. */
function sanitize(raw: unknown): string[] {
  if (!Array.isArray(raw)) return []
  const seen = new Set<string>()
  const result: string[] = []
  for (const entry of raw) {
    if (typeof entry !== 'string' || !isValidAddress(entry)) continue
    const address = normalize(entry)
    if (seen.has(address)) continue
    seen.add(address)
    result.push(address)
    if (result.length >= MAX_RECENT) break
  }
  return result
}

/** The recently-analyzed addresses, most-recent first. Empty when unset,
 * malformed, or storage is blocked. */
export function loadRecentWallets(): string[] {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored === null) return []
    return sanitize(JSON.parse(stored) as unknown)
  } catch {
    /* localStorage blocked or JSON malformed → empty, session-only */
    return []
  }
}

/**
 * Record `address` as the most-recently-analyzed, persist, and return the new
 * list (most-recent first, deduped case-insensitively, capped at 5). A
 * malformed address is ignored and the current list returned unchanged.
 */
export function rememberWallet(address: string): string[] {
  if (!isValidAddress(address)) return loadRecentWallets()
  const normalized = normalize(address)
  const next = [normalized, ...loadRecentWallets().filter((a) => a !== normalized)].slice(
    0,
    MAX_RECENT,
  )
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
  } catch {
    /* localStorage blocked → the returned list is the session-only source of truth */
  }
  return next
}
