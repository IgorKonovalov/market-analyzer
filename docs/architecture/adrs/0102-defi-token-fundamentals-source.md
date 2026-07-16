# ADR-0102 — DeFi token & protocol fundamentals as a data source

> **Status:** accepted (Plan 0107 close, 2026-07-16)
> **Date:** 2026-07-15
> **Related plan(s):** [0107](../plans/0107-defi-fundamentals.md)

> **Acceptance note (2026-07-16).** Accepted as-built. The keyless DefiLlama tier plus the best-effort Aerodrome-native deep tier shipped as `defi_fundamentals`, conditions-only, honest-degrade. Coverage confirmed against upstream reality: **AERO's `mcap` (no `gecko_id`), `fdv` (no keyless total-supply source at this tier), and the `unlocks` calendar (DefiLlama `/emission` is Pro-gated → HTTP 402) are genuine keyless gaps handled by honest-null-with-note**, exactly the negative consequence recorded below — not a defect. The deep tier's composition point is the tool (the reader is injected and folded for Aerodrome off the resolved `protocol_slug`), not a composite `DefiFundamentalsSource`, keeping the registry a single `{"defillama": …}` entry.

## Context

Our condition surface for an asset is price/structure (`analyze_symbol`, `market_structure`) plus four sentiment surfaces. For a small-cap DeFi-native token this is **structurally blind to the fundamentals that actually move it**. Analyzing AERO (Aerodrome) for a user holding AERO-heavy Aerodrome LPs, we found:

- `news_for` (curated RSS: CoinDesk/CoinTelegraph/Yahoo/MarketWatch/CNBC) returns **zero** items — the token is too small for those feeds.
- Nothing ingests **protocol/pool TVL + volume + fee/reward APR trend**, the token's **emissions schedule** (which directly sets LP reward APR), **veAERO vote/bribe dynamics**, or the **unlock/dilution calendar**.

These are on-chain-native facts. Two of our existing seams are relevant: the keyless [`DefiLlamaAdapter`](../../../src/market_analyser/data/adapters/defillama.py) (currently only a historical-price reader) and the Base RPC path (`RpcLpDetailAdapter`, [ADR-0038](0038-third-party-api-key-storage.md) secrets). The decision — which source(s), how deep, keyless-first vs protocol-native, and which skill consumes it — could reasonably go several ways.

## Decision

We will add **DeFi token/protocol fundamentals as a first-class read**, layered by cost:

1. **Keyless DefiLlama primary — chain-agnostic.** A `DefiFundamentalsSource` over DefiLlama's protocol / fees / yields endpoints and its emissions-unlocks dataset — protocol TVL + history, DEX volume, fee/reward APR, token mcap/FDV, and the unlock/dilution schedule where DefiLlama covers it. Keyless, on the [ADR-0019](0019-external-http-adapter-resilience.md) resilient path, honest-degrade on miss (never fabricate a number). **DefiLlama is not chain-scoped** — the tool keys on token/protocol, so any major chain (Ethereum, Base, Arbitrum, Optimism, …) resolves through the same call with no per-chain work. This is the v1 slice and covers most of the gap with **no new key and no new dependency**.
2. **Protocol-native deep reads (best-effort, later) — Base + Aerodrome only.** For the facts DefiLlama does not expose cleanly — **exact Aerodrome emissions decay + veAERO/Voter vote & bribe weights** — a protocol-native reader over the existing Base RPC (Minter/Voter/veAERO contracts) or the Aerodrome subgraph, keyed on the RPC URL already in `secrets.json`. Best-effort and additive: it deepens the DefiLlama read for Aerodrome-on-Base and degrades to the DefiLlama (chain-agnostic) depth everywhere else. This tier is **per-protocol-per-chain by construction** — extending it to another protocol (Uniswap, Aave) or another chain (an Ethereum-mainnet deep read, reachable via the `eth_rpc_url` we hold) is a separate increment, not part of this decision.

Both ride the [ADR-0031](0031-data-source-adapter-contract.md) source-registry pattern (one Protocol, one registry entry). The read is surfaced as a **`defi_fundamentals` MCP tool** consumed by **`defi-analyst`** (on-chain conditions) and referenceable by `market-analyst` — **conditions only, never advice** ([ADR-0029](0029-advisory-recommendation-boundary.md)). It is **wall-clock-sensitive like the sentiment tools — no `as_of` historical replay** (current-state protocol data has no reconstructable point-in-time series here); any figure carries its own upstream `as_of`.

## Consequences

### Positive
- Closes the fundamentals blind spot for on-chain-native assets keyless, reusing `DefiLlamaAdapter`'s HTTP path and the source-registry seam — the v1 slice adds no key and no dependency.
- Layered by cost: the high-value keyless slice ships first; the paid-attention protocol-native reads are a clean later increment, not a blocker.
- Directly serves the user's AERO-LP earning thesis — emissions/APR trend and unlocks are the levers on reward yield.

### Negative
- DefiLlama coverage of the **unlocks/emissions dataset is uneven** for small caps; AERO specifically must be verified, and a gap surfaces as an honest "not covered", not a fabricated calendar.
- The protocol-native tier is **Aerodrome-specific** — every additional protocol (Uniswap, Aave, …) is its own reader. We are buying depth for one protocol first, breadth never guaranteed.
- Another wall-clock-sensitive tool with no deterministic replay — consistent with the sentiment tools, but it means these reads are not reproducible after the fact.

### Neutral
- The set of DefiLlama endpoints (and, later, the Aerodrome contract addresses) becomes a maintained config, exactly like the RSS feed list and the sentiment subreddit groups.

## Alternatives considered

### Alternative A — Protocol subgraph / RPC only (skip DefiLlama)
Rejected as the *primary*: it is accurate and on-chain-truthful but Aerodrome-specific and high-effort per protocol, and it does not carry cross-protocol TVL/volume comparables. It is the right tool for the *deep* tier (emissions decay, veAERO), not the broad one — which is exactly how the decision places it.

### Alternative B — A paid fundamentals aggregator (Token Terminal, Messari, DefiLlama Pro)
Rejected for v1: introduces a paid key and a dependency for data the keyless DefiLlama endpoints largely cover. Revisit only if the keyless coverage proves too thin for the assets the user actually holds.

### Alternative C — Do nothing; rely on price/structure + the LP scanners
Rejected: the whole finding is that price structure is blind to emissions/unlocks/APR-trend, and the user explicitly asked how to get these. The LP scanners (`scan_wallet`, `enrich_lp_positions`) read *the user's positions*, not the token's forward supply/yield picture.
