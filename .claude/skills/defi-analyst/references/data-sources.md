# Data sources

Four sources, used in this order of preference. The principle is **simplest source that answers the question accurately** — paying complexity (auth, schema-per-protocol, RPC plumbing) only when the simpler tier doesn't have what you need.

## 0. `defi_fundamentals` MCP tool — the packaged fundamentals read (Plan 0107)

Before hitting DefiLlama's raw endpoints by hand, reach for the **`defi_fundamentals(symbol_or_protocol)`** MCP tool (ADR-0102). It is the in-app, keyless path to a token/protocol's **fundamentals** — the layer price/structure and the LP scanners are blind to: `tvl` + `tvl_trend`, `dex_volume` (24h/7d/30d), `fee_apr` + `reward_apr` (TVL-weighted over the protocol's pools), `mcap`/`fdv`, and the `unlocks` (emissions/dilution) calendar. It rides the resilient HTTP path and packages the same DefiLlama endpoints section 1 documents, so use it for a wallet-holding's forward supply/yield picture and **surface TVL/APR/unlocks alongside a health report**.

- **Conditions only (ADR-0029).** The tool reports what IS — it carries no `action`/`signal`/`recommendation` field and never says buy/sell/rebalance. A rebalance call on a drifting position is the `advisor`'s job, not this read.
- **Honest-null, never fabricated (ADR-0019).** Any field DefiLlama doesn't cover comes back `null` with a `notes` entry naming the gap. Known thin spots: a token with no `gecko_id` has null `mcap` (AERO today); `fdv` has no keyless source at the DefiLlama tier (honest-null); the `unlocks` calendar is DefiLlama-Pro-gated for many small caps (AERO's `/emission` returns HTTP 402) → "unlocks not covered". Read the `notes` — a null is a real gap, not zero.
- **Aerodrome deep tier.** For AERO specifically, the tool folds an Aerodrome-native deep read (exact emission decay + veAERO/gauge weights over the Base RPC) onto the DefiLlama payload; other protocols stay at DefiLlama depth.
- **Wall-clock-sensitive — no `as_of`.** Current-state only; not reproducible after the fact (like the sentiment tools).

Reach for the raw sources below only when you need a field or a per-pool granularity the tool doesn't surface.

## 1. DefiLlama — default for cross-protocol queries

**Base URL:** `https://api.llama.fi` and `https://yields.llama.fi` (yields lives on its own subdomain). No auth.

**Use for:**
- Pool list across protocols + chains: `GET https://yields.llama.fi/pools` — returns every pool DefiLlama tracks, with TVL, fee APR (`apyBase`), reward APR (`apyReward`), pool id.
- A specific pool's chart: `GET https://yields.llama.fi/chart/{pool_id}` — daily TVL + APR history.
- Token prices: `GET https://coins.llama.fi/prices/current/{chain}:{address}` — multi-chain price oracle.
- Protocol TVL trend and audit info: `GET https://api.llama.fi/protocol/{slug}`.

**Schema gotchas:**
- `apyBase` is fee-only; `apyReward` is incentive-only. Their sum is the headline "APR" most UIs show. Don't conflate.
- `apyReward` is computed using the reward token's *current* price — usually correct, but spikes when a thinly-traded reward token has a stale price.
- `il7d` is sometimes null, sometimes a real number — null typically means "not enough history", not "no IL".
- Pool ids are stable, but the `project` slug occasionally renames after protocol rebrands; if a pool id 404s, the project may have been renamed.

**Rate limit:** courteous — keep it under ~10 req/s. No documented hard cap, but they reserve the right.

## 2. Subgraphs (The Graph) — protocol-specific structured queries

Use when DefiLlama is too coarse: tick-level Uniswap v3 data, exact fee accrual, historical block-level state.

**Auth:** the hosted service sunset in mid-2024. New endpoints live on the decentralized network and require an API key. Get it from `https://thegraph.com/studio/` and put it in `.env` as `GRAPH_API_KEY`. URL pattern:

```
https://gateway.thegraph.com/api/{GRAPH_API_KEY}/subgraphs/id/{SUBGRAPH_ID}
```

**Canonical subgraph IDs** (as of writing — verify if they 404):

| Protocol            | Chain    | Subgraph ID (example)                              |
| ------------------- | -------- | -------------------------------------------------- |
| Uniswap v3          | Ethereum | `5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV`     |
| Uniswap v3          | Base     | varies; check Uniswap docs                         |
| Aave v3             | Ethereum | `JCNWRypm7FYwV8fx5HhzZPSFaMxgkPuw4TnR3Gpi81zk`     |
| Aerodrome Slipstream| Base     | check Aerodrome docs / their github               |

Always confirm the subgraph ID by fetching it from the protocol's documentation rather than hardcoding — they get redeployed periodically. If a query 404s with a stale ID, search the protocol's docs site for "subgraph".

**Schema gotchas:**
- Subgraphs lag the chain by 1-30 blocks. For health-factor-on-the-edge queries, prefer direct RPC.
- Field names differ between v2 and v3 of the same subgraph. Pin the version in the ID; don't assume forward-compatibility.

## 3. Direct RPC — authoritative for a single contract call

**Library:** `web3.py` if the package is installed. Otherwise raw `requests.post` JSON-RPC.

**Endpoints (need user's key in `.env`):**

```
ETH_RPC=https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}
BASE_RPC=https://base-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}
ARB_RPC=https://arb-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}
OP_RPC=https://opt-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}
```

**Use for:**
- Aave health factor: `Pool.getUserAccountData(user)` on the Aave Pool contract — returns HF, current debt, available borrow, LTV. Authoritative; subgraph can lag.
- Uniswap v3 fee accrual: `NonfungiblePositionManager.positions(tokenId)` to get raw fee growth, then math to convert to actual fees owed.
- Spot price from a Chainlink feed: `latestAnswer()` on the feed contract — useful for stress-testing liquidation.
- Token balance: `ERC20.balanceOf(wallet)`.

**Gotchas:**
- Free Alchemy tier has compute-unit budgets. Single calls are fine; tight loops will burn through the budget.
- Always pin to a specific block (`block_identifier=N`) when the answer needs to be reproducible. `latest` drifts.

## 4. Protocol-native APIs — last resort

Some protocols expose curated APIs that wrap subgraphs + extra logic:

- **Aave**: `https://aave-api-v2.aave.com/` (rates, reserves) — limited; subgraph is usually fuller.
- **Uniswap**: there's a GraphQL API at `https://api.uniswap.org/v1/graphql` but it's undocumented and changes without notice. Avoid unless nothing else has the field.

Generally these duplicate what's in DefiLlama or the subgraph; reach for them only if you need a field the others don't expose.

## Caching policy

Save raw responses next to the run artifact under `raw/`:

```
runs/defi/health/2026-05-17T14-22-00/
├── report.md
├── report.json
└── raw/
    ├── defillama-pools-2026-05-17T14-22-00.json
    ├── aave-account-data-0x1234.json
    └── uniswap-position-12345.json
```

This is non-negotiable for two reasons: (1) the report becomes reproducible after the API changes, (2) the user can grep the raw data when they don't trust a number in the report.

## Sanity checks before reporting any number

- **APR > 100%** — almost always reward-token inflation. Decompose and price the reward at current market.
- **TVL < $1M** — pool stats are noisy; small swaps move things meaningfully. Flag the user.
- **Pool created < 14 days ago** — APR is unstable; "7d APR" is meaningless if the pool is 9 days old.
- **`apyReward` but `rewardTokens` empty** — DefiLlama bug or stale row. Don't trust the reward APR until you can identify the reward token.
- **Health factor < 1.1** — already in active liquidation risk on most chains; surface this above all other findings.
