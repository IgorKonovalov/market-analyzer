# Reference — Zerion REST API capability survey

> **Type:** reference (capability catalog, not a decision or a plan).
> **Date:** 2026-06-05.
> **Provenance:** every endpoint below was probed **live** against the Zerion v1 REST API (`https://api.zerion.io/v1`) using the project's free Developer-tier key, for the public test wallet `0xae5b…9790` (a test wallet). Status codes, response shapes, and the sample values are observed, not snippet-sourced — they resolve the "vendor pages not directly loadable" confidence gap flagged in [ADR-0034](../adrs/0034-defi-portfolio-aggregator.md). Live values (totals, prices, PnL) move; treat the *numbers* as a 2026-06-05 snapshot and the *shapes* as durable.
> **Related:** [ADR-0034](../adrs/0034-defi-portfolio-aggregator.md) (picked Zerion for discovery + tx history), [ADR-0035](../adrs/0035-defi-domain-placement.md) (the per-capability source Protocols this maps onto), [ADR-0036](../adrs/0036-defi-pnl-reconstruction.md) (consumes tx history), [ADR-0037](../adrs/0037-defi-position-risk-forecast.md) (risk/forecast), [ADR-0038](../adrs/0038-third-party-api-key-storage.md) (the key store). Current consumer: `src/market_analyser/data/adapters/zerion.py` (positions only).

## TL;DR

Zerion is far more than the single positions endpoint we use today. The live probe confirmed **13 working endpoints** across four capability groups — wallet portfolio, wallet activity (decoded tx history), wallet NFTs, and global reference/token data. We currently consume **one** of them. The untapped surface most relevant to the DeFi program: a one-call **portfolio summary** (totals, by-chain, by-type, 24h change), a **PnL** endpoint (realized/unrealized/fees/cost-basis), **portfolio value charts**, full **NFT tracking** (positions + collections + floor-price portfolio), and **token market-data + price charts**. The probe also re-confirmed the ADR-0034 **F1 classification gap** as a live fact (see [§5](#5-what-we-use-today--the-classification-gap)).

---

## 1. Access model (applies to every endpoint)

| Aspect | Observed behavior |
|--------|-------------------|
| **Base URL** | `https://api.zerion.io/v1` |
| **Auth** | HTTP Basic — API key as username, empty password (`curl -u "zk_…:"`). Matches `zerion.py::_basic_auth_header`. |
| **Envelope** | JSON:API. Top level is `{ "links": {...}, "data": ... }`. `data` is a list (collections) or an object (singletons). Each item carries `type`, `id`, `attributes`, and often `relationships` (linked `chain` / `fungible` / `dapp` / `nft`). |
| **Currency** | `?currency=usd` on every wallet/token endpoint converts `value`/`price` fields. Other fiats supported. |
| **Pagination** | Cursor-based. Collections return `links.next` (an opaque full URL) when more pages exist; `page[size]=N` caps page size (seen on transactions, nft-positions). Positions/portfolio are single-shot (no cursor). |
| **Rate limit (free Developer tier)** | ~2,000 req/day, 10 RPS ([ADR-0034](../adrs/0034-defi-portfolio-aggregator.md) Notes). **Confirmed in practice:** firing ~11 endpoints back-to-back drew several `HTTP 429`s; a ~1.1s inter-request spacing cleared them. This validates ADR-0034's "deliberate, request-triggered scan cadence — never reactive/auto-refresh" constraint as a hard requirement, not advice. |
| **Trailing-slash quirk** | Collection paths want a trailing slash (`/positions/`, `/transactions/`). **`/pnl` must NOT have one** — `/pnl/` returns `301` redirecting to `/pnl`. A naive `+ "/"` convention breaks PnL. |

All calls are read-only `GET`. The only write-ish surface is `swap`/`gas` (trade construction), which is out of scope for a read-only analyzer.

---

## 2. Endpoint catalog (all probed 2026-06-05)

Status = HTTP code observed for the test wallet. ✅ = returns useful data for this wallet.

| # | Endpoint | Returns | Status | For test wallet |
|---|----------|---------|--------|-----------------|
| **Wallet — portfolio** ||||
| 1 | `GET /wallets/{addr}/portfolio` | Net worth, distribution by chain & by position-type, 24h change | 200 | ✅ $1,140 total |
| 2 | `GET /wallets/{addr}/positions/` | Interpreted fungible positions (DeFi + balances) across all chains | 200 | ✅ 114 complex / 86 simple |
| 3 | `GET /wallets/{addr}/pnl` | Realized/unrealized gain, fees, cost basis, invested | 200 | ✅ +$29,193 total gain |
| 4 | `GET /wallets/{addr}/charts/{period}` | Portfolio total-value time series | 200 | ✅ 289 points (month) |
| **Wallet — activity** ||||
| 5 | `GET /wallets/{addr}/transactions/` | Decoded tx history (transfers, approvals, acts, fee) | 200 | ✅ paginated |
| **Wallet — NFTs** ||||
| 6 | `GET /wallets/{addr}/nft-positions/` | Per-NFT holdings (token id, price, value, media) | 200 | ✅ many (paged) |
| 7 | `GET /wallets/{addr}/nft-collections/` | Holdings grouped by collection + floor price | 200 | ✅ 78 collections |
| 8 | `GET /wallets/{addr}/nft-portfolio` | NFT net worth distribution by chain | 200 | ✅ ~$1,974 |
| **Reference / token data** ||||
| 9 | `GET /chains/` | All supported chains + native fungible, explorer, flags | 200 | ✅ 64 chains |
| 10 | `GET /fungibles/{id}` | Token metadata + market_data (price, mcap, supply, 1d/30d/90d/365d changes) | 200 | ✅ |
| 11 | `GET /fungibles/{id}/charts/{period}` | Token price time series | 200 | ✅ 289 points (day) |
| 12 | `GET /fungibles/?filter[search_query]=…` | Token search/list | 200 | ✅ |
| 13 | `GET /gas-prices/` | Per-chain gas (eip1559 base/slow/standard/fast) | 200 | ✅ 31 chains |

> Not surveyed (out of scope for a read-only analyzer): `GET /swap/*` (offer/quote construction — `swap/fungibles` returned 200 but empty for our query), and the NFT singleton lookups `GET /nfts/{id}`. Both are documented Zerion surfaces; neither serves discovery/tracking.

---

## 3. Per-endpoint detail

### Group A — Wallet portfolio

**1. `/wallets/{addr}/portfolio`** — the cheapest "headline" call. One request → entire net-worth summary:
```
attributes:
  total.positions                       1140.41         # USD net worth (positions)
  positions_distribution_by_type        {wallet, deposited, borrowed, locked, staked}
  positions_distribution_by_chain       {base:1009.97, ethereum:82.60, optimism:45.27, … 9 chains}
  changes.absolute_1d / percent_1d      -26.03 / -2.23%
```
This is the single best call to drive a portfolio header/overview without paging the full positions list. Note `by_type` exposes lending exposure (`borrowed`, `deposited`) and `staked` at a glance.

**2. `/wallets/{addr}/positions/`** — the one we consume today (see [§5](#5-what-we-use-today--the-classification-gap)). Key params: `filter[positions]` (`only_simple` *default* → plain balances only; `no_filter` → include complex DeFi; `only_complex`), `filter[chain_ids]=base` (verified: narrows 114 → 43), `filter[trash]`. Rich per-position attributes beyond what we map: `price`, `changes.{absolute,percent}_1d`, `pool_address`, `application_metadata` (dapp name/icon/url), `flags.{displayable,is_trash}`, `protocol_module` (`farming` / `staked` / `lending` / `nft_staked`), `quantity.{int,decimals,float,numeric}`.

**3. `/wallets/{addr}/pnl`** (no trailing slash) — Zerion's FIFO profit-and-loss:
```
total_gain 29192.98 | realized_gain 30882.12 | unrealized_gain -1689.13
relative_total_gain_percentage 0.892 | total_fee 5156.56
total_invested 3.27M | realized_cost_basis 3.27M | net_invested 2831.26
received_external / sent_external / sent_for_nfts / received_for_nfts
```
Per [ADR-0036](../adrs/0036-defi-pnl-reconstruction.md) this is **not** our source of truth (we reconstruct from events for auditability) — but it is exactly the documented **cross-check signal** to flag gross reconstruction errors.

**4. `/wallets/{addr}/charts/{period}`** — portfolio total-value series. `period` ∈ `{hour, day, week, month, year, max}` (verified `month` → 289 points). Each point is `[unix_ts, value]`. Drives an account equity curve in the UI.

### Group B — Wallet activity

**5. `/wallets/{addr}/transactions/`** — fully decoded, human-readable history. This is the [ADR-0036](../adrs/0036-defi-pnl-reconstruction.md) input. Per transaction:
```
operation_type   receive | send | trade | deposit | withdraw | mint | execute | approve | borrow | repay …
hash, mined_at, mined_at_block, nonce, status, sent_from, sent_to
fee              { fungible_info, quantity, price, value }
transfers[]      { fungible_info{symbol,implementations[{chain_id,address,decimals}]},
                   direction(in/out), quantity{int,decimals,float,numeric},
                   value, price, sender, recipient, act_id }
approvals[]      token approvals granted
acts[]           { id, type, application_metadata{contract_address, method{id,name}} }   # the semantic "what happened"
```
Filters (verified `operation_types`): `filter[operation_types]=trade`, `filter[asset_types]`, `filter[chain_ids]`, `filter[trash]`, time-range `filter[min_mined_at]`/`filter[max_mined_at]`, `page[size]`. The `transfers[]` + `price` + `mined_at` triplet is precisely what per-event valuation needs — though ADR-0036 deliberately re-prices from an independent `HistoricalPriceSource` rather than trusting the inline `price` (which is point-in-time, not block-precise).

### Group C — Wallet NFTs (entirely untapped today)

**6. `/wallets/{addr}/nft-positions/`** — per-NFT holdings: `nft_info{contract_address, token_id, name, interface(ERC721/ERC1155), content{preview,detail}, flags}`, `collection_info{name,description}`, `amount`, `price`, `value`, `changed_at`, linked `chain`. Cursor-paged.

**7. `/wallets/{addr}/nft-collections/`** — holdings rolled up by collection: `nfts_count`, `total_floor_price`, `collection_info`, per-collection `chains`. 78 collections for the test wallet.

**8. `/wallets/{addr}/nft-portfolio`** — NFT net worth by chain (`base`, `ethereum`, `polygon` for this wallet ≈ $1,974 total). The NFT analogue of endpoint #1.

### Group D — Reference / token data

**9. `/chains/`** — 64 chains. Confirms all four target chains present (`ethereum`, `base`, `arbitrum`, `optimism`) plus `polygon`, `binance-smart-chain`, `avalanche`, and many L2s (`zora`, `degen`, `lens`, `ink`, …). Each chain: `external_id`, `name`, `icon`, `explorer{tx_url_format,token_url_format}`, `rpc.public_servers_url`, `flags{supports_trading,sending,bridge}`, `relationships.native_fungible`. Useful for building explorer deep-links and validating the target-chain allowlist against Zerion's canonical ids.

**10. `/fungibles/{id}`** — token metadata + `market_data`: `price`, `market_cap`, `fully_diluted_valuation`, `total_supply`, `circulating_supply`, `trading_volumes`, and `changes{percent_1d, percent_30d, percent_90d, percent_365d}`. Plus `implementations[]` (per-chain address+decimals), `external_links`, `description`. A usable **current-price/quote** source.

**11. `/fungibles/{id}/charts/{period}`** — token price series, `[unix_ts, price]` points (289 for `day`) + `stats`. **Important caveat (consistent with ADR-0034/0036):** this is an *interpolated chart series*, **not** a clean price-at-block-timestamp lookup — so it is **not** a substitute for ADR-0036's `HistoricalPriceSource` (DefiLlama/Alchemy). Good for sparklines, not for per-event cost basis.

**12. `/fungibles/?filter[search_query]=…`** — token search (verified `aerodrome` → 3 results). Resolve a symbol/name → `fungible_id` for #10/#11.

**13. `/gas-prices/`** — per-chain gas, 31 chains: `gas_type` (eip1559), `base_fee`, `slow`/`standard`/`fast` `{priority_fee, max_fee, estimation_seconds}`, `updated_at`. (A `filter[chain_ids]=…` attempt returned 0 items — the exact filter param for this endpoint is unverified; the unfiltered call returns all chains.) Marginal value for a read-only analyzer; useful only if we ever annotate tx cost.

---

## 4. Supported chains (target-relevance)

All four ADR-0034 target chains are first-class. Our adapter's `_CHAIN_IDS` map (`ethereum`/`base`/`arbitrum`/`optimism`) correctly matches Zerion's `chain.data.id` literals. The test wallet additionally holds value on `polygon`, `optimism`, `arbitrum`, `fantom`, `lens`, `monad` — **dropped today by design** (off-target). If the program ever widens scope, widening is a one-line map edit, but note Zerion ids are kebab-case (`binance-smart-chain`, not `bsc`).

---

## 5. What we use today — the classification gap

`zerion.py` consumes **only** endpoint #2 (`/positions/?filter[positions]=no_filter`) and maps it to `DefiPosition`. The live probe re-confirmed [ADR-0034](../adrs/0034-defi-portfolio-aggregator.md) followup **F1 as a present-tense fact**, worth surfacing here because the survey makes it concrete:

- This wallet's Aerodrome LPs arrive as **`protocol_module: "farming"`** with **`position_type: "staked"`** (16 such positions). Our `_classify_kind` only branches on `protocol_module ∈ {liquidity_pool, lending}`; `farming` is not matched, so these fall through to the `position_type == "staked"` branch and are labelled **`kind="staking"` with `pool=None`** — not `kind="lp"` with the pool name. The observed `protocol_module` vocabulary is wider than the adapter assumes: `{farming, staked, lending, nft_staked, None}`. **F1's fix (fold gauge-staked LPs into `lp`, carry pool, de-dupe tokens) is live-validated as still-needed**, and feeds the deep-adapter plan, not this survey.

Everything else in §2 is **unconsumed**.

---

## 6. Untapped capabilities mapped to project needs

| Capability (endpoint) | What it unlocks | Maps onto |
|-----------------------|-----------------|-----------|
| Portfolio summary (#1) | One-call net worth + by-chain/by-type + 24h Δ for a dashboard header | new `WalletPortfolioSource`? or fold into discovery |
| PnL (#3) | Reconstruction cross-check signal | [ADR-0036](../adrs/0036-defi-pnl-reconstruction.md) (already designated cross-check) |
| Portfolio chart (#4) | Account equity-curve view over time | UI surface ([ADR-0035](../adrs/0035-defi-domain-placement.md) placement) |
| Transactions (#5) | Decoded event history → per-event P&L | [ADR-0036](../adrs/0036-defi-pnl-reconstruction.md) `TxHistorySource` (designed, not yet built) |
| NFT positions/collections/portfolio (#6–8) | Whole NFT tracking dimension — absent from the data model today | **no ADR yet** — net-new domain decision |
| Token market_data (#10) | Current price/quote, 1d/30d/90d/365d momentum per token | possible quote source; **not** historical-price (#11 is interpolated) |
| Chains (#9) | Explorer deep-links, canonical chain allowlist | UI / validation helper |

---

## 7. Recommended next adoptions (with owner-skill routing)

Ordered by leverage-to-effort. Each is a *pointer for a future plan*, not a commitment — adoption goes through the normal architect → plan → owner-skill flow.

1. **Wire `/transactions/` as the `TxHistorySource` (#5).** Highest value: it is the already-designed input to the [ADR-0036](../adrs/0036-defi-pnl-reconstruction.md) P&L engine, the next major DeFi capability. The Protocol seam ([ADR-0035](../adrs/0035-defi-domain-placement.md)) already names it. → **architect** writes the plan (pagination, operation-type filtering, the `transfers[]`→event mapping, trash filtering); → **dev** implements the adapter + parser alongside `zerion.py`.

2. **Add the PnL cross-check (#3).** Cheap (one call, flat shape) and directly serves ADR-0036's stated "sanity cross-check" role — flag when our reconstructed total diverges grossly from Zerion's. Mind the no-trailing-slash quirk. → **architect** confirms the divergence-threshold contract; → **dev** implements.

3. **Portfolio summary + chart (#1, #4) for an overview surface.** One call each; powers a net-worth header and an account equity curve. Needs a placement decision (new `WalletPortfolioSource` vs. folding into discovery). → **architect** (new ADR/plan, since it adds a Protocol); → **dev** (adapter) + **ui-builder** (the view).

4. **NFT tracking (#6–8) — net-new domain.** The biggest untapped surface and the one with **no existing ADR**. Touches the data model (no NFT type today), discovery, and UI. Don't bolt onto the fungible-positions adapter. → **architect** owns the up-front decision (is NFT tracking in scope for this personal tool? what's the model — positions vs. collection-rollup vs. floor-price-only?); only then → **dev** + **ui-builder**.

5. **Reconsider token market_data (#10) as a quote source — with care.** It gives a *current* price and multi-window momentum, but the **chart endpoint (#11) is interpolated, not block-precise**, so it must not be mistaken for ADR-0036's `HistoricalPriceSource` (that stays DefiLlama/Alchemy). Likely a *minor* convenience, not a priority. → **architect** to rule in/out before any wiring.

**Not recommended:** `/gas-prices/` and `/swap/*` — no role in a read-only analyzer.

---

## 8. Bearing on Plan 0034 (deep LP detail) — does the payload carry a keyable position identifier?

[Plan 0034](../plans/0034-defi-deep-lp-detail.md) parks its phases 3–4 (the concrete deep-state adapter) **pending exactly one input from a full-Zerion-API investigation: "whether Zerion's payload carries a position identifier (NFT tokenId / pool address) we can key an RPC/Graph read on."** This survey answers it. Probed across the test wallet's **28 complex (DeFi) positions**:

| Identifier on each complex position | Coverage | Example (Aerodrome WETH/GHST farm, Base) |
|-------------------------------------|----------|------------------------------------------|
| `attributes.pool_address` | **28 / 28** | `0xe3800a58b5535935850a10e082952ec3577d8dcc` |
| `attributes.group_id` | 28 / 28 | `37023fc883407f46…a706efb7` |
| `relationships.dapp.data.id` | 28 / 28 | `aerodrome` |
| `fungible_info.implementations[].{address,chain_id}` | per token | LP/token contract per chain |

**Answer for 0034's source decision:**

- **Velodrome/Aerodrome-class LPs (ERC-20 LP token): `pool_address` is present and sufficient.** The LP is fungible; `pool_address` + `chain` keys a direct RPC or The-Graph read of pool reserves/state → the position's on-chain detail. This covers the test wallet's actual holdings. **0034's "our RPC + The Graph" assumption holds for this class** — no Zerion-native deep call needed.
- **Uniswap-v3 LPs (each position is an NFT with `tickLower`/`tickUpper`): `pool_address` is necessary but NOT sufficient.** Two positions can share one pool with different ranges, so the deep read must key on the **position NFT `tokenId`**, not the pool. The fungible-`positions/` payload exposes `pool_address` but **no Uni-v3 `tokenId`** — *unverified live* (this wallet is Aerodrome-heavy, holds no live Uni-v3, the ADR-0034 F3 gap). The `tokenId` would have to come from the **NFT-positions endpoint (#6)** or an RPC enumeration of the NonfungiblePositionManager. **This is a real refinement to ADR-0034 §deep-state**: discovery→deep keying is one hop for Velodrome-class, two hops (resolve tokenId first) for Uni-v3.

Net: 0034's phases 3–4 are **unblockable for the Aerodrome path now**; the Uni-v3 path needs the tokenId-resolution decision settled first (architect, possibly an ADR refining ADR-0034). This finding belongs in 0034's open-decision log when it's finalized.

## 9. Caveats & determinism notes

- **Values are Zerion-interpreted, not reproducible from our code.** Positions, PnL, portfolio totals, and NFT floor prices are the aggregator's numbers — the [ADR-0034](../adrs/0034-defi-portfolio-aggregator.md) ADR-0009 tension. The reproducibility-critical path (per-event P&L) stays ours by design; everything in §2 is *display/discovery* data, acceptable to source externally.
- **Live values drift between calls.** Anything time-stamped (`updated_at`, `changes.*_1d`, `total`) reflects call time. Snapshots in this doc are 2026-06-05.
- **Rate limit is a real design constraint, observed.** 429s appeared under burst; the adapter's no-cache + request-triggered-only posture is correct. Any future multi-endpoint scan (portfolio + positions + tx + nft in one wallet view) must serialize/space calls or it will trip the free-tier ceiling.
- **Secrets discipline unchanged.** The key is read lazily from `SecretsStore` and injected server-side as Basic auth ([ADR-0038](../adrs/0038-third-party-api-key-storage.md)); none of the new endpoints change that — they reuse the same key and client.
- **ToS still unverified.** Per ADR-0034 Notes, Zerion's redistribution/desktop clauses live in the API License Agreement (not the pricing page) and were not loaded. Nothing is expected to bite a personal read-only tool, but this survey does not clear that flag.
