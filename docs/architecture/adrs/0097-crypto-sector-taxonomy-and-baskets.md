# ADR-0097 — Crypto sector taxonomy and representative baskets

> **Status:** proposed
> **Date:** 2026-07-13
> **Related plan(s):** [0102](../plans/0102-crypto-sector-rotation.md)

## Context

Sector rotation — ranking sectors by relative momentum to see where capital is rotating — is a classic market read. For this app the user chose **crypto** sectors over US sector ETFs.

Equities make rotation easy: the SPDR sector ETFs (XLK, XLF, XLE, …) are canonical, liquid, externally defined, and each has a single fetchable price. Crypto has **none of that** — there is no canonical sector taxonomy and no liquid sector index we can fetch one price for. To rank crypto sectors we must *define* the sectors and their constituent baskets ourselves, then synthesize a basket momentum from constituent OHLCV we already fetch.

That makes the taxonomy a durable, opinionated, maintenance-bearing decision: it will drift as the market changes (today's "AI" sector barely existed two cycles ago), and a bad basket — an illiquid or unrepresentative constituent — skews the read. This is a decision, not a no-brainer: how the sectors are defined, weighted, and maintained all could reasonably go several ways.

## Decision

We will define a small, explicit crypto sector taxonomy as **versioned config data** (not hardcoded in analysis logic) — an initial set of roughly 6–8 sectors (final set pinned in Plan 0102; candidates: Layer-1, Layer-2/scaling, DeFi, memecoins, AI, RWA, DePIN), each mapped to a handful of liquid, representative constituent symbols priced through our existing USD-native sources (Coinbase / Binance / Yahoo per [ADR-0076](0076-coinbase-usd-native-crypto-source.md) / [ADR-0052](0052-binance-exchange-data-source.md) / [ADR-0069](0069-crypto-first-asset-class-positioning.md)). Sector momentum is the **equal-weighted mean of constituent trailing returns** over the requested window, computed over cached bars via the [ADR-0095](0095-watchlist-scan-fanout-harness.md) harness. The taxonomy file is the single source of truth, is expected to be revised over time, and each revision is a config edit — not a code change. A sector with fewer than a configured minimum of priced constituents is reported as incomplete rather than silently ranked.

## Consequences

### Positive
- A genuine crypto rotation read with **no new data source** — it reuses OHLCV we already fetch.
- The taxonomy is transparent and user-editable; anyone can see and change which tokens define "DeFi".
- Equal-weighting is simple, determinism-friendly, and needs no market-cap feed.

### Negative
- The taxonomy is subjective and **will age** — someone must maintain it. This is an operational handle, documented as such; a stale taxonomy quietly degrades the signal.
- Equal-weight over a few constituents is a **coarse proxy** for a true cap-weighted sector index — it treats a mega-cap and a mid-cap as equal voters.
- A delisted or illiquid constituent distorts its sector until the config is fixed. Mitigation: skip-and-flag missing constituents and require ≥N priced to report a sector.

### Neutral
- Sectors overlap — a token can be both "L1" and "AI". We allow membership in multiple baskets and document it.

## Alternatives considered

### Alternative A — Pull an external crypto-sector taxonomy/index (a provider API)
Rejected: it adds a keyed/rate-limited dependency and cedes the taxonomy to an external definition we can't audit or tune. ADR-0069's crypto-first, in-house-data posture favours defining it ourselves from OHLCV we already hold.

### Alternative B — Market-cap-weight the baskets
Rejected: cap-weighting needs a live market-cap source per constituent (an extra dependency and a non-price-path input that complicates determinism), and one mega-cap would dominate the sector signal. Equal-weight is the honest coarse proxy; cap-weight is a future refinement if the read proves valuable.

### Alternative C — US sector ETFs instead of crypto
Rejected: the user explicitly chose crypto sectors. The US-ETF version is the trivial one (canonical liquid ETFs, one price each) and can be a later sibling plan if wanted.
