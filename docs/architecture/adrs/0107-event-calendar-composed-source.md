# ADR-0107 — Event / economic calendar as a composed, keyless-first data source

> **Status:** accepted (2026-07-21, Plan 0113 close)
> **Date:** 2026-07-21
> **Related plan(s):** 0113-event-calendar-source

## Context

The analyst/advisor surface is blind to **scheduled forward events** that move price — FOMC and CPI/PCE releases, equity earnings dates, crypto listings/delistings, and token unlocks. This is the roadmap's Tier 5 ("news & market investigation"), and a concrete gap surfaced in use: `news_for` returns nothing for on-chain-native tokens, and there is no way to ask "what's coming this week". Unlike OHLCV or sentiment, these are *dated future facts* (a timestamp, sometimes a magnitude), not historical bars or published headlines.

A 2026-07-21 source investigation ground-truthed availability across the four categories and found **no single vendor covers them keyless**, with availability varying sharply by category:
- **Macro** splits: FOMC meeting dates are an official but API-less HTML schedule that changes ~yearly (effectively curate-once); CPI/PCE *release dates* are cleanly served by **FRED** (free self-serve key, JSON). A *live* economic feed with consensus/actual numbers has no first-party keyless source.
- **Equity earnings** are well-served by **free-key** APIs (Finnhub — 60 req/min, JSON — is the best free tier; FMP second). The only keyless route is unofficial Yahoo scraping (ToS-violating, fragile).
- **Crypto listings/delistings** have **no official announcement API from any exchange**; the only robust keyless signal is self-diffing Binance `exchangeInfo` + Coinbase product lists (catches *tradeable* add/remove events, misses forward announcements and forks/upgrades).
- **Token unlocks** are **paid-only**: DefiLlama's emissions/unlocks API now returns `HTTP 402` (only its TVL/price routes are free — a correction to the Plan 0107 assumption), DropsTab is approval-gated, Messari/CryptoRank are paid. No fully-keyless unlocks JSON exists; the only keyless path is scraping the DefiLlama unlocks web page (ToS-gray).

Two standing decisions frame the response: **keyless-first** (ADR-0069) and the established **free-key-inert** pattern — a keyed source ships fully wired but honest-empty when the key is absent (ADR-0103 LunarCrush, ADR-0105 Reddit OAuth), keys resolved through `SecretsStore` (ADR-0038). The user chose "free keys OK, inert without" for this work and "defer token unlocks" rather than pay ~$300/mo or ship a scrape.

## Decision

We will add an **events domain** exposing a **composed `EventCalendarSource`**: one Protocol, one `MarketEvent` model, and per-category provider adapters that each **honest-degrade independently** (ADR-0019 resilient path; a dead or unconfigured provider yields empty, never an exception, never a fabricated event). The initial providers are:
- **Macro** — FOMC meeting dates from a **curated static seed** (refreshed as the Fed publishes, ~yearly) plus a **FRED** release-dates adapter (`fred_api_key` via `SecretsStore`, `file_type=json`, **inert without the key**).
- **Earnings** — a **Finnhub** earnings-calendar adapter (`finnhub_api_key`, inert without the key).
- **Crypto listings/delistings** — a **keyless self-diff** of Binance `exchangeInfo` and Coinbase product lists against a persisted prior snapshot, emitting one event per tradeable add/remove.

Events are surfaced as a **single discriminated `event_calendar(category=…)` tool** (one verb, modes as a parameter — ADR-0104), **conditions-only** (an event is a fact, never a buy/sell call — ADR-0029), and **wall-clock-sensitive** (forward-looking scheduled facts; no `as_of`, and repeated calls legitimately differ as the calendar updates — the same posture as the sentiment sources).

**Token unlocks are explicitly deferred.** With no keyless JSON source and the user's spend paused, we ship no unlocks provider now; revisiting requires a future spend-or-scrape decision (its own follow-on plan), mirroring the ADR-0103 "spend paused" posture.

## Consequences

### Positive
- Closes the "what's coming this week" blind spot with a design that fits the existing seams (ADR-0031 source registry, ADR-0019 resilient HTTP, ADR-0038 secrets, ADR-0104 tool granularity, ADR-0029 conditions boundary) — no new architectural concept.
- Per-category honest-degrade means the tool is useful the moment *any* provider is configured; a missing FRED or Finnhub key silently narrows coverage instead of breaking the call.
- The composed shape makes adding a later provider (or a funded unlocks source) a local change behind the same Protocol and the same tool.
- Feeds naturally into the existing dwell scheduler + OS-notification path (ADR-0055/0093/0094) as a follow-on — "FOMC in 2 days" is a small addition, not new plumbing.

### Negative
- **Coverage is uneven and stays that way** — and the tool must say so in every payload: the listings-diff misses forward announcements and forks/upgrades; macro carries release *dates* but no consensus/actual numbers; earnings and FRED release dates are absent without their free keys; **token unlocks — the user's top-priority category — are simply not covered.** This is honest incompleteness, but it is incompleteness.
- A curated-static FOMC seed can go stale if the Fed publishes new dates and no one refreshes it; the refresh is a manual chore (bounded — ~yearly).
- Free-tier field gating (some Finnhub estimate fields, non-US earnings coverage) means the adapter must validate against a real key and degrade fields it can't read.

### Neutral
- Events live in their own domain, not folded into the ADR-0010 news adapter — a scheduled dated fact is a different shape from a published headline, and the corroboration/timeline/digest Tier-5 pieces will consume events and news side by side, not through one adapter.

## Alternatives considered

### Alternative A — A single paid calendar vendor (one clean multi-category source)
Rejected: it reverses keyless-first (ADR-0069) for a recurring bill, and no single vendor actually covers crypto listings + macro + equities well — we'd pay and *still* compose. The composed keyless/free-key design gives most of the coverage at zero cost.

### Alternative B — A live economic-calendar feed (consensus/actual/forecast numbers)
Rejected for v1: there is no first-party keyless source; the available feeds are ForexFactory scrapes behind middlemen (ToS-fragile). Release *dates* via FRED answer "when", which is the Tier-5 need; the numbers themselves are a later, separately-justified addition.

### Alternative C — Pay for a clean unlocks source now (DefiLlama Pro ~$300/mo)
Rejected: ~$300/mo for one category while spend is paused (ADR-0103 precedent). Deferred behind an explicit future decision rather than quietly absorbed.

### Alternative D — Keyless earnings via unofficial Yahoo scraping
Rejected: ToS-violating and breakage-prone when a clean free-key source (Finnhub) exists. We take the free key and ship inert without it, consistent with ADR-0103/0105.

### Alternative E — Fold events into the existing news adapter (ADR-0010)
Rejected: a dated forward event (timestamp + magnitude + symbol) is structurally unlike a published news item (headline + body + sentiment). One shape per domain keeps both honest; the Tier-5 timeline will join them at the view, not the source.

## Notes

- Source investigation: 2026-07-21 (DefiLlama emissions 402 paid-only; FRED free-key JSON release dates; Finnhub free-key earnings; Binance/Coinbase diff-only listings; no keyless unlocks).
- Implemented by Plan 0113; accepted at that plan's close. Token-unlocks follow-on and the alert-wiring / timeline-view Tier-5 pieces are tracked as separate future plans.
