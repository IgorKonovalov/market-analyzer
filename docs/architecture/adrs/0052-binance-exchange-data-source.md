# ADR-0052 — Binance public API as the crypto exchange data source; exchange pairs are distinct symbols

> **Status:** accepted (2026-06-13, at Plan 0058's close; Plan 0056 exercised the derivatives half)
> **Date:** 2026-06-09
> **Related plan(s):** 0056-binance-derivatives-data (funding/OI), 0058-binance-klines-ohlcv (klines)
> **Related ADRs:** [ADR-0007](0007-market-data-provider.md) (the provider contract the klines adapter slots into), [ADR-0031](0031-data-source-adapter-contract.md) (per-capability Protocol + selector registry), [ADR-0019](0019-external-http-adapter-resilience.md) (ResilientHttpClient), [ADR-0051](0051-historized-metric-series-contract.md) (where funding/OI series land), [ADR-0028](0028-timeframe-resampling-and-expansion.md) (canonical timeframe registry)

## Context

Yahoo serves crypto OHLCV as `BTC-USD`-style synthetic USD pairs with hard intraday caps (1h ≤ 730 days, 15m ≤ 60 days) and no derivatives data at all. The crypto program needs (a) deep intraday history for forecast training, (b) altcoin pair breadth, and (c) funding rates + open interest — among the strongest crypto positioning signals. One exchange should serve all three so the program speaks one symbol vocabulary; the Pillar-5 execution work already targets Binance USDⓈ-M futures testnet, which biases the choice.

Verified facts (2026-06-09, official docs): spot `GET /api/v3/klines` is keyless, max 1000 bars/call, intervals `1m…1M` incl. native `4h`, 6,000 weight/min/IP, with full history since listing retrievable by pagination (confirmed in practice; retention not doc-guaranteed). Futures `GET /fapi/v1/fundingRate` is keyless with full history since contract launch (BTCUSDT: Sep 2019), 8h cadence for majors. **`/futures/data/openInterestHist` serves only the latest ~1 month** (docs verbatim) — long OI history does not exist to fetch; it must be recorded. **Geo-restriction is real:** api.binance.com / fapi.binance.com return HTTP 451 from restricted locations (US among them) even for public read-only endpoints; binance.us offers same-shape spot klines but **no futures API**.

The second decision inside this one: what symbol namespace exchange data uses. Yahoo's `BTC-USD` and Binance's `BTCUSDT` are *different series* (different venue, different quote asset, different prices); pretending they are one symbol would mix sources inside one cache key and break determinism/provenance.

## Decision

We will use **Binance public market-data endpoints** (spot `api.binance.com` for klines; futures `fapi.binance.com` for funding rate and open interest) as the crypto exchange data source, keyless, via a ResilientHttpClient subclass per ADR-0019.

**Exchange pairs are first-class, distinct symbols.** `BTCUSDT` is its own symbol — never an alias of `BTC-USD`, never merged with it in the bars cache. **Routing is by membership:** the provider routes OHLCV requests to the Binance adapter iff the symbol is present in Binance's cached `exchangeInfo` symbol set; everything else routes to Yahoo as today. No prefix namespace, no format heuristics. A symbol therefore resolves to exactly one source for its whole life, and cached bars stay single-provenance by construction.

**Open interest is recorded, not fetched:** current OI snapshots accrue into the ADR-0051 `metric_points` store from day one; the ~30-day `openInterestHist` window seeds the series once. Funding-rate history backfills in full.

**HTTP 451 is a typed, surfaced condition** (`GeoRestrictedError`), not a retry case — each Binance plan carries a `human` live-smoke phase early, and if the user's network is geo-blocked the fallback decision (binance.us spot-only, Bybit, or VPN posture) is made then, by a follow-up to this ADR, not improvised in an adapter.

## Consequences

### Positive
- Deep intraday history (1h/15m to listing date) unlocks intraday forecast training Yahoo's caps forbid; native `4h` removes one resample path for exchange symbols.
- Funding + OI land as ordinary ADR-0051 series — Plan 0059 features and future backtest conditions read them with the same `as_of` join as everything else.
- One venue vocabulary across data (this ADR) and execution (ADR-0043/Plan 0045): the testnet trader and the data layer agree on what `BTCUSDT` means.
- Keyless: no secret-store involvement for any of it (the trade keychain stays execution-only, ADR-0044).

### Negative
- **Geo fragility.** A 451 from the user's network kills the whole Binance leg; the live-smoke phases exist to learn this in hour one, but the risk is structural and outside our control. The fallback costs a follow-up ADR and possibly a second adapter.
- **OI history starts now.** Whatever the 30-day seed doesn't cover is gone forever; OI-derived forecast features inherit a long warm-up (ADR-0054's row-drop policy handles it honestly).
- Two symbol universes (`BTC-USD` and `BTCUSDT`) coexist on every symbol-taking surface. Tools/UI must not imply they're interchangeable; analyses on one don't transfer to the other. This is honest but mildly confusing forever.
- The `exchangeInfo` symbol-set cache is one more thing to refresh; a stale set misroutes a newly-listed pair to Yahoo (which then 404s — loud, at least).

### Neutral
- USDT-quoted prices ≠ USD prices (usually within bps, occasionally not). We treat that as a property of the symbol, not a problem to correct.
- Spot klines vs futures funding/OI reference different Binance markets for the "same" pair; series ids carry the market (`binance.funding_rate.BTCUSDT` is unambiguous — funding only exists on futures).

## Alternatives considered

### Alternative A — Coinbase
Cleaner geo story for Western users, decent spot API. Rejected: no funding/open-interest surface (spot-only exchange) — the derivatives half of the requirement would still need Binance or Bybit, splitting the program across two venues for no gain.

### Alternative B — Bybit
Full derivatives surface, fewer geo blocks than Binance. Rejected as the *default*: weaker alignment with the already-approved Pillar-5 Binance execution work and thinner spot liquidity. Explicitly the named fallback if the live smoke hits 451.

### Alternative C — Prefix namespace (`BINANCE:BTCUSDT`) instead of membership routing
TradingView-style explicit source prefixes. Rejected: the prefix leaks into every tool signature, chart title, cache key, and saved artifact forever, to solve a routing question the `exchangeInfo` membership check answers invisibly and deterministically.

## Notes
- Rate budget: klines weight 2 per call at 6,000/min is effectively unconstrained for a personal app; funding shares a 500/5min pool; the ADR-0019 client's pacing covers both.
- Full-history-by-pagination is confirmed in practice but not doc-guaranteed (the one soft spot in the verification); the backfill must treat an empty page as end-of-history, not an error.
