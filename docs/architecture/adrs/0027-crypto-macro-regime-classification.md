# ADR-0027 — Crypto macro regime as an in-house neutral structural classification

> **Status:** accepted (2026-06-03, at [Plan 0022](../plans/done/0022-macro-context.md) close — see the close note appended below)
> **Date:** 2026-05-29
> **Related:** [ADR-0007](0007-market-data-provider.md) (the `get_macro_context` Provider method this signal rides on), [ADR-0009](0009-rewrite-data-layer-in-house.md) (we own derived-signal logic in-house), [Plan 0022](../plans/0022-macro-context.md) (the implementing plan)

## Context

[Plan 0022](../plans/0022-macro-context.md) adds `bitcoin_market_pulse`, a single-call crypto macro read from CoinGecko's keyless `/global` endpoint: BTC price + 24h change, BTC dominance, total market cap + 24h change. Alongside the raw measurements the plan emits a `regime` field — a one-word label summarising the *structure* of the crypto market (where capital is concentrating).

This is a new kind of output for the data layer. Every existing derived value either passes through a vendor's own number (crypto Fear & Greed reports Alternative.me's index verbatim, per [Plan 0011](../plans/done/0011-fear-and-greed-indices.md)) or is a well-established trailing indicator (RSI, MACD, etc.). `regime` is the first signal where **we** define a classification taxonomy and the rule that maps measurements onto it. Two forces make that a decision rather than a one-liner:

1. **The analyst non-negotiable.** "Conditions are facts, decisions are the user's." A macro label is one step from advice — `ALT_FAVORABLE` or `HIGH_RISK` would cross the line. The vocabulary itself has to be constrained so the signal cannot drift into a recommendation as the code evolves.
2. **It becomes a consumed signal.** Once `bitcoin_market_pulse` ships, agents and (later) UI surfaces read `regime`. A taxonomy and mapping that live only as narrative prose in a plan body are unauditable — a future maintainer can't tell whether a label change is a bug or an intended re-tuning. The classification needs a durable record of *what the labels mean* and *why they're descriptive, not prescriptive*.

## Decision

We will classify crypto macro structure into a **fixed, closed, neutral four-value vocabulary**, computed in-house from the two measurements CoinGecko's `/global` already returns (BTC dominance and total-market-cap 24h trend). The vocabulary is a Pydantic `Literal`:

```python
regime: Literal["btc_led", "alt_structure", "risk_off_structure", "neutral"]
```

Each label names a **structural condition** — where capital is sitting — and contains no action, sentiment-grade, or risk-grade token. The proposed mapping rule (qualitative; the implementer pins the exact numeric thresholds at Plan 0022 phase 1 and the close ceremony confirms them against this ADR):

| Label                  | Structural condition (descriptive) |
|------------------------|-------------------------------------|
| `btc_led`              | BTC dominance rising — capital concentrating in BTC relative to alts. |
| `alt_structure`        | BTC dominance falling while total market cap is flat-to-rising — capital rotating toward alts. |
| `risk_off_structure`   | Total market cap contracting materially — broad outflow across the asset class. |
| `neutral`              | No condition above is clearly met (mixed or within the deadband). |

Two invariants are pinned by tests in Plan 0022 phase 1, so the guarantee is enforced, not just documented:

- **No-action-token test.** The vocabulary is asserted to contain none of `buy` / `sell` / `favorable` / `opportunity` (or similar advice tokens). This guards the non-negotiable at the type level — a future label that smuggles in advice fails the test.
- **Determinism test.** The same measurement inputs deterministically yield the same label across repeated computation (no wall-clock read, no ordering dependence). This locks the mapping above and satisfies the determinism non-negotiable for the one computed field.

`get_macro_context` rejects a non-null `as_of` with `ValueError` — the read is wall-clock-sensitive (consistent with `screener_query`, the quote, and the sentiment methods); there is no historical regime replay surface.

## Consequences

**Positive:**
- The classification is auditable: the label set, the mapping intent, and the "condition not advice" guarantee live in one durable record, with two tests enforcing the invariants.
- The closed `Literal` makes the signal cheap to consume — agents and any future UI can switch on four known values, and the type system rejects an unknown label.
- The deadband-backed `neutral` value means the signal degrades honestly: when the structure is genuinely mixed, it says so rather than forcing a misleading label.

**Negative (the price we pay):**
- **A four-bucket label is coarse.** Real market structure is continuous; collapsing dominance + mcap-trend into one of four words discards nuance. We accept this for a single-glance macro read; a consumer wanting precision reads the raw `btc_dominance_pct` / `total_market_cap_change_24h` fields, which are always present alongside `regime`.
- **The thresholds are a judgement call.** The boundary between, say, `neutral` and `risk_off_structure` is a tuned number, not a law of nature. Re-tuning it is a deliberate change that must update this ADR (or supersede it) and re-pin the determinism fixtures — it is not a free parameter to nudge silently.
- **Vocabulary growth is constrained.** Adding a fifth label (e.g. a stablecoin-rotation condition) means a new `Literal` value, an ADR amendment/supersession, and consumers that branch on the set. That friction is intentional — it is the cost of keeping the signal auditable and advice-free.

## Alternatives considered

- **Risk-grade / recommendation labels** (`HIGH_RISK`, `ALT_FAVORABLE`, "opportunity with caution"). Rejected outright — directly contradicts "conditions are facts, decisions are the user's." This is the failure mode the closed neutral vocabulary exists to prevent.
- **No classification — raw numbers only.** Emit `btc_dominance_pct` and `total_market_cap_change_24h` and let the consumer interpret. Rejected: the whole value of `bitcoin_market_pulse` is the one-glance read; forcing every consumer to re-derive "is this BTC-led or alt-rotation?" both duplicates the logic and invites each consumer to invent its own (inconsistent, unaudited) thresholds. Centralising the rule once, with tests, is the safer shape — the raw fields remain available for anyone who wants them.
- **A continuous structural score (e.g. −1 … +1).** Rejected for v1: a score implies a precision the two coarse inputs don't support, and "what does −0.3 mean?" is harder to consume than four named conditions. A score could supersede this ADR later if the inputs grow richer.
- **Capture it as a plan detail, no ADR.** Rejected: per `CLAUDE.md` ("new ADR-shaped question → architect, even if it feels like a small edit"), a new in-house classification taxonomy that becomes a consumed signal is exactly the durable decision an ADR exists to record. A narrative paragraph in a plan body that closes and moves to `done/` is not a discoverable contract.

## Pinned thresholds & sourcing — confirmed at Plan 0022 close (2026-06-03)

The Decision deferred the exact numeric thresholds to the implementer, to be confirmed against the taxonomy at close. They are pinned in `src/market_analyser/data/adapters/coingecko.py` (`classify_crypto_regime`), confirmed here, and reproduced so the mapping is auditable without reading code:

- **`risk_off_structure`**: `total_market_cap_change_24h <= -5.0` (% over 24h). Evaluated **first** — a material broad contraction takes priority over the dominance trend, per the table's intent.
- **Dominance-trend proxy.** BTC dominance *change* (which a single `/global` snapshot cannot give on its own) is recovered as `relative = btc_change_24h - total_market_cap_change_24h`. Because total market cap includes BTC, `sign(relative) == sign(Δdominance)`, so the proxy is exact for direction.
- **`btc_led`**: not materially contracting and `relative >= +1.0` pp (BTC outperforming the whole market → dominance rising).
- **`alt_structure`**: not materially contracting and `relative <= -1.0` pp (BTC underperforming → dominance falling).
- **`neutral`**: not materially contracting and `|relative| < 1.0` pp (within the dominance-trend deadband).

Two clarifications recorded (append-only) where the proposed body above was looser than the as-built rule:

1. **Sourcing is two keyless calls, not one.** The Context's "single-call `/global`" framing was a planning-time simplification. `/global` carries dominance, total market cap, and total-cap 24h change but **neither BTC's spot price nor BTC's own 24h move**; the adapter adds a second keyless call (`/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true`) for both. Both share one `ResilientHttpClient` (60s TTL), so the resilience and caching properties this ADR assumed are unchanged. The second call is **load-bearing for `regime`** — it supplies `btc_change_24h`, the dominance-trend input — not merely for display.
2. **`alt_structure` is gated only by the −5% risk-off floor**, not by a separate "total market cap flat-to-rising" test. The table row reads "while total market cap is flat-to-rising"; the pinned rule instead labels `alt_structure` for *any* non-materially-contracting market (`total > -5%`) where BTC underperforms by ≥1pp — which includes **mildly** contracting markets (e.g. total −3%, BTC −4.5%). This is a deliberate two-axis simplification (risk-off floor, then relative performance) and is broader than the original prose; the coarseness is the price already acknowledged in Consequences, and the raw `btc_dominance_pct` / `total_market_cap_change_24h` fields remain available for finer reads.

Re-tuning any of these constants remains a deliberate change that must amend or supersede this ADR and re-pin the determinism fixtures (per Consequences) — not a free parameter to nudge silently.
