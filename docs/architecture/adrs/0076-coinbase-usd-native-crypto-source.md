# ADR-0076 — Coinbase as the USD-native crypto OHLCV source; N-source membership routing

> **Status:** accepted (2026-07-11, at Plan 0081's close)
> **Date:** 2026-07-11
> **Related plan(s):** 0081-coinbase-usd-native-source
> **Related ADRs:** [ADR-0052](0052-binance-exchange-data-source.md) (refines its routing section — membership routing generalises to N sources; Coinbase takes precedence over Yahoo for its listed pairs), [ADR-0069](0069-crypto-first-asset-class-positioning.md) (this is a crypto-first data-breadth investment), [ADR-0007](0007-market-data-provider.md) (the provider contract the adapter slots into), [ADR-0031](0031-data-source-adapter-contract.md) (per-capability Protocol + selector registry), [ADR-0019](0019-external-http-adapter-resilience.md) (ResilientHttpClient), [ADR-0028](0028-timeframe-resampling-and-expansion.md) (derive-on-read for non-native timeframes), [ADR-0047](0047-variable-duration-monthly-timeframe.md) (the monthly-resample concern this ADR revisits for a 24/7 series), [ADR-0026](0026-symbol-search-bound-to-ohlcv-provider.md) (chartable invariant: every searchable symbol is fetchable)

## Context

Yahoo serves crypto as `BTC-USD`-style synthetic USD composites with hard intraday caps: 15m ≤ 60 days, 1h ≤ 730 days ([ADR-0052](0052-binance-exchange-data-source.md) context; `data/timeframes.py` registry). A user charting `BTC-USD` at 15m hits the 60-day wall — the immediate trigger for this ADR (a renderer-side graceful clamp shipped first, commit `b020fa8`, so the chart degrades instead of erroring; this ADR addresses the underlying data-depth gap).

Binance already solves depth ([ADR-0052](0052-binance-exchange-data-source.md)) — full history to listing, 15m uncapped — **but only under the `BTCUSDT` symbol**. ADR-0052 deliberately keeps `BTC-USD` (Yahoo) and `BTCUSDT` (Binance) as distinct, single-provenance series and rejected both prefix-namespaces and Coinbase *as the derivatives venue* (Coinbase is spot-only, so it couldn't serve the funding/OI half of that requirement). Neither rejection speaks to the question here: **what should the familiar USD-quoted `BTC-USD` form serve, and can we make it deep without the USDT basis or the two-namespace confusion?**

Verified facts (2026-07-11, official Coinbase Exchange docs):
- Public market data is **keyless**. `GET /products/{id}/candles?granularity={sec}&start={iso}&end={iso}` returns `[time, low, high, open, close, volume]` rows, **max 300 candles per request** — deep history is retrievable by backward pagination (same shape as the Yahoo absolute-range work, [ADR-0031](0031-data-source-adapter-contract.md)).
- Granularities are `{60, 300, 900, 3600, 21600, 86400}` seconds — i.e. native **1m, 5m, 15m, 1h, 6h, 1d**. There is **no native 4h, 1w, or 1mo**.
- `GET /products` lists the full product universe (`BTC-USD`, `ETH-USD`, `SOL-USD`, … plus `-USDC`/`-USDT`/crypto-quoted pairs) — the membership set that drives routing, exactly like Binance's `exchangeInfo`.
- `GET /products/{id}/ticker` gives a keyless spot quote.
- **Geo:** Coinbase Exchange public market data is US-accessible and does not 451 the way `api.binance.com` can — so Coinbase independently hedges ADR-0052's stated "geo fragility" negative for the crypto data layer.

Crypto pairs quote in **USD** on Coinbase — so `BTC-USD` maps 1:1 to the string Yahoo already uses, with no USD-vs-USDT semantic fudge. The user has asked that this apply to crypto generally, not just BTC.

## Decision

We will add **Coinbase Exchange public market-data endpoints** (`api.exchange.coinbase.com`) as a third keyless OHLCV source via a ResilientHttpClient subclass ([ADR-0019](0019-external-http-adapter-resilience.md)), and **generalise ADR-0052's membership routing from two sources to N**.

**Routing precedence is Binance → Coinbase → Yahoo, by membership:**
- If the symbol is in Binance's cached `exchangeInfo` set → Binance (unchanged; `BTCUSDT` etc.).
- Else if the symbol is in Coinbase's cached product set → Coinbase (`BTC-USD`, `ETH-USD`, all its listed crypto pairs).
- Else → Yahoo (unchanged; equities, indices, FX, and any crypto Coinbase does not list).

Binance's `BTCUSDT` and Coinbase's `BTC-USD` are different strings, so the two exchange sets never collide; precedence only disambiguates a hypothetical overlap and keeps Binance the primary exchange. **Coinbase adopts its whole product universe**, so every crypto `-USD` pair it lists — not just BTC — routes to Coinbase and gets deep, USD-native history. This directly serves [ADR-0069](0069-crypto-first-asset-class-positioning.md).

**A symbol still resolves to exactly one source for its whole life and across every timeframe** (ADR-0052's single-provenance invariant, preserved). Because Coinbase serves only 15m/1h/1d natively, the other canonical timeframes are **derived on read** ([ADR-0028](0028-timeframe-resampling-and-expansion.md)) from a Coinbase base, never from another venue: `4h ← 1h`, `1w ← 1d`, `1mo ← 1d`. Crypto trades 24/7, so daily bars are gap-free and calendar-week / calendar-month bucketing is deterministic and trailing — which materially reduces the variable-duration concern [ADR-0047](0047-variable-duration-monthly-timeframe.md) raised for Yahoo's market-closure-laden series. The per-source `source_resampled_from` seam already exists (`data/timeframes.py:112`); Coinbase adds its own branch.

**Provenance is enforced at the cache read, not merely by routing.** Adopting Coinbase changes the source of an *existing* string (`BTC-USD` was Yahoo, becomes Coinbase) — unlike Binance, which introduced a brand-new string. So the bars cache read becomes **source-scoped**: a Coinbase-routed symbol never returns or merges bars recorded under a different source. Existing Yahoo-sourced crypto `-USD` rows are purged by a migration (they are a different series now). This makes the switch safe by construction rather than by hoping the cache happened to be empty.

**HTTP geo/availability failures are a typed, surfaced condition** (reuse ADR-0052's `GeoRestrictedError` posture), and Plan 0081 carries an early `human` live-smoke phase — Coinbase is expected to be US-clean, but we confirm rather than assume.

## Consequences

### Positive
- **The familiar `BTC-USD` (and every Coinbase-listed crypto `-USD`) becomes deep and USD-native.** No 60-day 15m wall, no USDT basis, no symbol the user has to re-learn.
- **Geo-resilience hedge.** A US network that 451s `api.binance.com` still reaches Coinbase; the crypto-first data layer no longer rests entirely on Binance's geo availability.
- **Membership routing generalises cleanly.** Binance → Coinbase → Yahoo is a small, deterministic extension of the ADR-0052 mechanism — no prefixes leak into tool signatures, cache keys, or chart titles (ADR-0052 Alternative C stays rejected).
- **Provenance gets *stronger*, not weaker.** Source-scoped cache reads make single-provenance an enforced read-time invariant instead of a routing coincidence.

### Negative
- **Existing crypto `-USD` analyses/backtests shift to Coinbase data.** A saved backtest on `BTC-USD` re-run after this lands pulls Coinbase bars, not Yahoo — slightly different prices, so a different (but equally valid and deterministic) result. This is an honest source change, not a determinism bug ([ADR-0018](0018-backtest-result-schema.md) determinism is per-input; the input source changed). The purge migration is one-way.
- **Two-of-three coarse timeframes are derived, not native.** `4h/1w/1mo` for Coinbase symbols are in-house resamples; a bug in the weekly/monthly bucketing would surface only on those charts. Mitigated by 24/7 gap-free daily bars and a determinism test, but it is new resample surface.
- **Another membership cache to keep fresh.** The Coinbase product set, like Binance's `exchangeInfo`, needs an explicit refresh; a stale set misroutes a newly-listed pair to Yahoo (loud 404 or shallow caps — degraded, not silent-wrong).
- **A third external dependency in the crypto hot path.** More endpoints to be down. Reuses the ADR-0019 client's resilience, and Yahoo remains the fallback for anything Coinbase doesn't list.

### Neutral
- **No new PyPI dependency.** Coinbase's endpoints are plain keyless HTTP consumed through the existing ResilientHttpClient, so [ADR-0012](0012-dependency-cooldown.md)/[ADR-0013](0013-pin-direct-dependencies.md) (cooldown + pinning) don't engage.
- Coinbase USD prices ≠ Binance USDT prices ≠ Yahoo composite — three venues, three series. We treat venue as a property of the symbol (ADR-0052's stance), now across three sources instead of two.
- Coinbase lists non-USD-quoted pairs too (`ETH-BTC`, `*-USDC`). They're in the membership set and route to Coinbase by the same rule; they were never well-served by Yahoo anyway.

## Alternatives considered

### Alternative A — Alias `BTC-USD` → Binance `BTCUSDT`
Reuse the already-wired Binance adapter by mapping USD↔USDT. Rejected: this is exactly what [ADR-0052](0052-binance-exchange-data-source.md) decided against — merging two venues into one series, USD-vs-USDT semantics, and mixed-provenance cache keys. It would need to *supersede* ADR-0052's core decision, and the argument doesn't hold: Coinbase gives USD-native depth without the merge.

### Alternative B — UI steering only (no new source)
Make the picker prefer/label the Binance `BTCUSDT` form for crypto and lean on the shipped graceful clamp for `BTC-USD`. Rejected as the *whole* answer (kept as a complementary phase in Plan 0081): it never makes the USD form itself deep, leaves the two-namespace confusion, and does nothing for the geo hedge. Good as a cheap addition, insufficient as the decision.

### Alternative C — Coinbase for intraday+daily only; leave 1w/1mo on Yahoo
Adopt Coinbase for the timeframes it serves natively and let the two coarsest buckets fall back to Yahoo. Rejected: it breaks single-provenance-per-symbol — one `BTC-USD` chart would be Coinbase at 15m and Yahoo at 1mo, with a visible price seam at the boundary. Deriving `1w/1mo` from Coinbase's own daily bars keeps the symbol single-source, and 24/7 crypto makes that derivation clean.

### Alternative D — TradingView
Ruled out as a bar-history source: the existing `TradingViewScreenerAdapter` is screening-only, and TradingView exposes no clean/authorized public OHLCV history API. Named here so it is not re-proposed.

## Notes
- Rate budget: Coinbase public market data is ~10 req/s per IP; the ADR-0019 client's pacing covers the paged backfill (300 candles/call) comfortably for a personal app.
- Full-history-by-pagination is confirmed in practice but, as with Binance ([ADR-0052](0052-binance-exchange-data-source.md) note), not retention-guaranteed by docs — the backfill treats an empty page as end-of-history, not an error.
- This ADR **refines** ADR-0052's routing section (two sources → N) and its "everything non-Binance → Yahoo" clause; it does not supersede it — the Binance decision, the distinct-symbols principle, and the geo-fragility framing all still stand.
