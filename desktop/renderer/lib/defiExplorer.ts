/**
 * Block-explorer + position-id helpers for the DeFi Wallet-P&L view.
 *
 * The sidecar's `position_id` is the stable `chain:protocol:ref` string
 * (`src/market_analyser/data/adapters/zerion.py`), where `ref` is Zerion's
 * internal group id — NOT an on-chain address, so it is never itself linkable.
 * A real explorer link therefore needs a genuine `0x…` address (the wallet, or
 * a pool contract the sidecar exposes); `explorerAddressUrl` validates the
 * address shape and returns `null` for anything that is not a 40-hex address,
 * so a bad or missing value degrades to "no link" rather than a dead URL.
 */

/** The chains the DeFi layer tracks (`src/market_analyser/defi/models.py`). */
export type DefiChain = 'ethereum' | 'base' | 'arbitrum' | 'optimism'

interface Explorer {
  /** Human name shown on the link (e.g. "Basescan"). */
  name: string
  /** Origin, no trailing slash. */
  base: string
}

const EXPLORERS: Record<DefiChain, Explorer> = {
  ethereum: { name: 'Etherscan', base: 'https://etherscan.io' },
  base: { name: 'Basescan', base: 'https://basescan.org' },
  arbitrum: { name: 'Arbiscan', base: 'https://arbiscan.io' },
  optimism: { name: 'Optimistic Etherscan', base: 'https://optimistic.etherscan.io' },
}

const CHAINS = new Set<string>(Object.keys(EXPLORERS))
const ADDRESS_RE = /^0x[0-9a-fA-F]{40}$/

export interface ParsedPositionId {
  /** The recognized chain, or `null` when the first segment is unknown. */
  chain: DefiChain | null
  /** The protocol label (e.g. "Aerodrome V3"), or `null` when absent. */
  protocol: string | null
  /** Everything after `chain:protocol:` — the pool/nft group ref (may itself
   * contain `:`), or `null` when absent. */
  ref: string | null
  /** The original, unparsed id. */
  raw: string
}

/** Parse a `chain:protocol:ref` position id. Tolerant: only the first two
 * colons are structural, so a `ref` containing `:` stays intact; a string with
 * fewer segments fills the trailing fields with `null`. */
export function parsePositionId(id: string): ParsedPositionId {
  const first = id.indexOf(':')
  const chainRaw = first === -1 ? id : id.slice(0, first)
  const chain = CHAINS.has(chainRaw) ? (chainRaw as DefiChain) : null
  if (first === -1) return { chain, protocol: null, ref: null, raw: id }
  const rest = id.slice(first + 1)
  const second = rest.indexOf(':')
  if (second === -1) {
    return { chain, protocol: rest || null, ref: null, raw: id }
  }
  return {
    chain,
    protocol: rest.slice(0, second) || null,
    ref: rest.slice(second + 1) || null,
    raw: id,
  }
}

/** The explorer's display name for a chain, or `null` when unknown. */
export function explorerName(chain: DefiChain | null | undefined): string | null {
  return chain ? EXPLORERS[chain].name : null
}

/** A `.../address/0x…` explorer URL for a chain + address, or `null` when the
 * chain is unknown or the address is not a well-formed 40-hex `0x…` value. The
 * returned address is lower-cased (explorers are case-insensitive on the path). */
export function explorerAddressUrl(
  chain: DefiChain | null | undefined,
  address: string | null | undefined,
): string | null {
  if (!chain || !address || !ADDRESS_RE.test(address)) return null
  return `${EXPLORERS[chain].base}/address/${address.toLowerCase()}`
}

/** A compact `abc123…7890` label for a long ref (a full 64-hex group id), left
 * unchanged when already short. */
export function shortRef(ref: string): string {
  return ref.length > 14 ? `${ref.slice(0, 6)}…${ref.slice(-4)}` : ref
}

/** The canonical `chain:protocol:ref` id with only the (often 64-hex) ref
 * shortened, so the full row is scannable without the id overflowing the cell.
 * Ids with fewer than three segments are returned unchanged. */
export function displayPositionId(id: string): string {
  const parts = id.split(':')
  if (parts.length < 3) return id
  const [chain, protocol, ...rest] = parts
  return `${chain}:${protocol}:${shortRef(rest.join(':'))}`
}
