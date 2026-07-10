# ADR-0069 — Crypto-first asset-class positioning (TradFi supported, secondary)

> **Status:** accepted
> **Date:** 2026-07-10
> **Related plan(s):** none (positioning decision; constrains future data-breadth and forecasting plans)

## Context

The project began asset-class-neutral in framing — "analyze markets and author trading strategies" — and the TradFi surface (Yahoo OHLCV, candlestick + classical patterns, indicators, the TradingView screener) shipped first. But the depth of investment since has been overwhelmingly on the crypto side, and the imbalance is now structural rather than incidental:

- **Data breadth is crypto-heavy.** The crypto intelligence program shipped Binance klines + derivatives (funding, open interest — [ADR-0052](0052-binance-exchange-data-source.md)), on-chain valuation (MVRV — [ADR-0053](0053-onchain-valuation-source.md)), cycle metrics (halving clock, Mayer, 200W-MA), F&G and BTC-dominance history, and a whole DeFi domain (Zerion/DefiLlama discovery, tx-replay P&L, LP/lending risk — [ADR-0034](0034-defi-portfolio-aggregator.md)–[ADR-0037](0037-defi-position-risk-forecast.md)). The TradFi side, by contrast, has no fundamentals (earnings, balance sheet, ratios), no macro series (yield curves, CPI, M2), and no equities-specific breadth beyond price + screener + generic sentiment.
- **The predictive surface is crypto-only where it matters.** The forecasting subsystem's v1 OHLCV-only feature set has no measurable edge at any horizon on 11.4 years of BTC-USD; the only edge that appears (h=21, v2-deep — [ADR-0057](0057-forecast-feature-set-tiers.md)) comes from the **exogenous features that only exist for crypto**: MVRV, funding, open interest, dominance, halving-cycle phase. There is no equivalent feature richness available for an equity, so the forecaster cannot honestly promise comparable output on a TradFi symbol. The one exception — the neutral macro-regime classifier — is itself explicitly crypto-scoped ([ADR-0027](0027-crypto-macro-regime-classification.md)).
- **The committed roadmap leans the same way.** Polymarket odds ([ADR-0041](0041-polymarket-odds-read-source.md)), cross-venue portfolio aggregation whose first live venue is Binance ([ADR-0042](0042-cross-venue-portfolio-aggregation.md)), and the execution arc's first (and testnet) venue Binance USDⓈ-M Futures ([ADR-0043](0043-execution-venue-protocol.md)–[ADR-0044](0044-trade-secret-store.md)) are all crypto. Tier 4's TradFi "fundamentals data" bullet in the roadmap has never been drafted into a plan.

This asymmetry currently reads as an oversight — a TradFi user could reasonably expect fundamentals or a working equity forecast and find neither. Naming the positioning turns the asymmetry into a stated decision, so future maintainers know *why* we invest in on-chain/derivatives depth and not in an earnings adapter, and so the product never over-promises on TradFi symbols.

## Decision

We position `market-analyser` as a **crypto-first** research and decision-support tool. Crypto (spot, perps/derivatives, on-chain, DeFi) is the primary asset class: it gets first-class data breadth, the richest forecasting feature sets, and the execution/portfolio arc. **TradFi (equities, indices, futures) remains supported but secondary** — the price/OHLCV, candlestick + classical pattern, indicator, screener, news and generic-sentiment surfaces continue to work on TradFi symbols and are maintained, but TradFi does **not** get a dedicated fundamentals or macro-fundamentals data layer, and the forecasting/advisory surfaces make **no** claim of a comparable predictive edge on TradFi symbols. Any future TradFi-fundamentals or macro work is an explicit, separately-justified plan that amends this positioning, not an assumed roadmap commitment.

This decision changes no code today. It is a scoping constraint on what plans get drafted, what the product promises, and how the forecaster's honest-uncertainty contract reads for non-crypto symbols.

## Consequences

### Positive
- **Focus.** Finite single-maintainer effort concentrates where the differentiated data (on-chain, derivatives, cycle, DeFi) and the only demonstrated forecast edge live, instead of spreading thin across an unbounded "all asset classes" surface — directly serving the roadmap's stated anti-scope-creep discipline.
- **Honest promises.** The forecasting/advisory surfaces stop implicitly over-claiming on TradFi symbols. "No edge on this equity" becomes a stated positioning, not a surprise.
- **Clear cut-criterion for backlog.** TradFi fundamentals / macro adapters move from "pending Tier 4 work" to "explicitly deferred, needs a plan that argues for reversing the positioning" — a concrete gate that resists silent scope growth.

### Negative
- **We under-serve the long-horizon equities investor.** A user who wants earnings-driven or macro-driven equity analysis will find only technicals + screener + generic sentiment. This is a real capability we are choosing not to build.
- **Perceived narrowing.** "Analyze markets" framing was broader; some users will read crypto-first as a downgrade. The mitigation is that TradFi is *supported*, not dropped — the technical surface is asset-class-neutral and stays that way.
- **Concentration risk.** Betting the product on one asset class ties its usefulness to crypto's relevance and to the continued availability of keyless crypto data sources (Binance public, alternative.me, CoinMetrics community). If those degrade, the primary surface degrades.

### Neutral
- The technical-analysis surface (`analysis/`), the strategy contract, and the backtest engine remain **asset-class-neutral** — they operate on OHLCV bars regardless of source. Crypto-first constrains *data breadth and forecasting scope*, not the deterministic primitives. A TradFi symbol still backtests, still scans for patterns, still gets a condition snapshot.

## Alternatives considered

### Alternative A — Asset-class-neutral (the implicit status quo)
Keep the "analyze markets" framing and treat crypto and TradFi as co-equal, with a fundamentals/macro adapter for TradFi planned in Tier 4. Rejected because it is a promise the single-maintainer effort has not kept and the forecasting subsystem structurally cannot keep: the differentiated features are crypto-only, and drafting an earnings adapter would dilute effort away from the surfaces that actually work. Neutral framing has been quietly producing the asymmetry this ADR exists to name.

### Alternative B — TradFi-first
Invest in fundamentals, macro, and equity breadth as the primary surface, treating crypto as secondary. Rejected because it discards the project's actual differentiated assets (on-chain valuation, derivatives, cycle metrics, DeFi P&L, the only demonstrated forecast edge) in favour of a crowded space already served well by mature incumbents. It would be a rewrite of the roadmap's center of gravity against the grain of everything shipped since 2026-06.

### Alternative C — Crypto-only (drop TradFi)
Remove the TradFi surface entirely and commit fully to crypto. Rejected as needlessly destructive: the technical surface, strategies, and backtest engine are asset-class-neutral and cost nothing extra to keep working on equities/indices, and cross-asset TradFi context (e.g. SPX regime as a crypto-correlation input) has genuine analytical value. Supported-but-secondary keeps that option open at near-zero cost; crypto-only would throw it away.

## Notes

- This ADR is standalone (no supersede/refine edge) but constrains the interpretation of the roadmap's Tier 4 ("Investor surface") and the forecasting tiers ([ADR-0030](0030-forecasting-subsystem.md)/[ADR-0057](0057-forecast-feature-set-tiers.md)): the exogenous feature richness that gives the forecaster its edge is crypto-specific by nature, which is independent confirmation of the positioning.
- Reversing or narrowing this positioning (e.g. deciding to build a TradFi fundamentals layer after all) is a normal future decision — write the plan that argues for it and supersede this ADR if the argument holds.
