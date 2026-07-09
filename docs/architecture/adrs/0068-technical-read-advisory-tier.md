# ADR-0068 — Technical-read tier: a second, lesser sanctioned conditions→decision crossing

> **Status:** proposed
> **Date:** 2026-07-09
> **Related plan(s):** 0074-technical-read-advisory-tier
> **Extends:** [ADR-0029](0029-advisory-recommendation-boundary.md) (advisory recommendation boundary)

## Context

[ADR-0029](0029-advisory-recommendation-boundary.md) drew the one sanctioned crossing of "conditions are facts, decisions are the user's": the advisor's `recommend` tool may emit a directional call, but only as a **fully-corroborated** artifact — the forecast, a strategy's live signal, and a positive walk-forward edge must **all agree**, conviction is *derived* (`P(direction) × edge_factor`) never invented, and any disagreement collapses to an honest flat. That "every leg must agree" invariant (`advisor/fusion.py`) is the honesty guarantee: it is what stops the app from emitting confident-looking calls on thin evidence.

The user wants a **lighter** capability: a directional read driven by a *single indicator's regime* — "Supertrend is long", "price is above the Ichimoku cloud with a bullish TK" — without the ML forecast and walk-forward corroboration. This is genuinely useful (a fast, transparent technical read) but it sits on the wrong side of ADR-0029 if delivered as-is: a bare direction from one indicator is exactly the confident-looking-but-thinly-supported call ADR-0029 exists to prevent from masquerading as advice.

The tension is real and worth a decision: either we refuse it (keep the single fused tier), we weaken `fuse()`'s all-legs-agree rule (guts the guarantee), or we sanction a **second, explicitly-lesser** crossing whose honesty comes from *structural separation and omission* rather than from corroboration. The interview chose the third path with the strongest-separation options: a distinct model, no conviction, no levels, its own tool.

## Decision

We will sanction a second advisory tier — the **technical read** — as an extension of ADR-0029, not a change to it. A technical read maps **one curated regime indicator** to a direction by its textbook mechanical rule and returns a distinct `TechnicalRead` artifact that carries **only** `{symbol, timeframe, as_of_bar_ts, indicator_id, direction (long/short/flat), regime_state, rationale}`. It deliberately **omits conviction and entry/stop/target levels** — the two things that make the fused `Recommendation` look like a trade ticket — so a technical read can never be mistaken for a corroborated call. It is delivered by a **separate MCP tool** (`technical_read`) and a distinct SSE event; the fused `recommend` tool and its all-legs-agree invariant are untouched.

The honesty boundary is preserved by three structural facts, all test-enforced:

1. **Distinct type.** `TechnicalRead` is a different model from `Recommendation`; it has no `conviction`, `entry_zone`, `stop`, or `targets` fields, so the "thin basis" cannot be dressed as a ticket even by mistake.
2. **Named single basis.** Every technical read states its one `indicator_id` and the mechanical regime rule that produced the direction — the basis is the indicator, said out loud, and nothing more.
3. **Curated, direction-yielding indicators only.** The eligible set is fixed to indicators with an unambiguous regime→direction reading: **Ichimoku** (price-vs-cloud + TK), **Supertrend** (direction), **EMA-stack** (fast-vs-slow + close), **MACD** (histogram sign). Strength/level-only indicators (ADX, ATR, bare RSI level) are excluded — they do not imply a direction, and forcing one would recreate the fabricated-call problem.

Like `recommend`, the tool is **advisory-only, structurally**: it holds no trade key, opens no network-write path, places no order (ADR-0025 untaken), enforced by a source scan. The `market-analyst` boundary is unchanged — analysts still never emit a direction; the technical read is an **advisor-tier** artifact, the lightest rung of the ADR-0029 crossing.

## Consequences

### Positive
- A fast, transparent, deterministic directional read is available without the ML/backtest machinery — cheap to compute, easy to reason about, and consistent with how traders actually read a single indicator.
- The honesty guarantee is preserved without weakening `fuse()`: the fused tier keeps its exact meaning, and the lesser tier is *unmistakably* lesser by construction (no conviction, no levels, distinct type).
- The eligible-indicator registry is a clean extension point: new regime indicators (e.g. a future one) join by adding a documented regime→direction rule, nothing more.

### Negative
- A second directional-output surface is a second place the ADR-0029 boundary must be defended. It is defended by the same structural scans (`no key/order/network-write`) plus the type/field pins, but it is more surface than a single tier.
- Two tiers can confuse a consumer who does not read the label ("the app told me long"). Mitigation is entirely in presentation: the distinct type, the "single-indicator, not corroborated" framing in the rationale, and a UI banner. The risk is real if a future consumer collapses the two.
- A bare direction from one indicator will sometimes disagree with the fused `recommend` (which may be flat). That is correct and intended — the two tiers answer different questions — but it must be presented so the divergence reads as "thin vs. corroborated", not "the app contradicts itself".

### Neutral
- Ichimoku eligibility depends on the `ichimoku()` function from Plan 0073 phase 1; the other three indicators are available today. The registry ships with whatever is available and gains Ichimoku when 0073 phase 1 lands.

## Alternatives considered

### Alternative A — Refuse it (keep only the fused tier)
Leave `recommend` as the only directional surface. Rejected because the capability is legitimately wanted and the fused tier's forecast gate makes it return flat in exactly the cases (no ML edge) where a trader still wants the mechanical read — the honest answer is a *labeled lesser tier*, not silence.

### Alternative B — Relax `fuse()` to make the forecast/backtest legs optional
Add a mode to `recommend` that drops the corroboration gates. Rejected as the most damaging option: it guts the all-legs-agree invariant that is the entire point of ADR-0029, and it blurs the two call types inside one code path and one tool description.

### Alternative C — Reuse `Recommendation` with a new `label`/`tier` value
Emit the technical read as a `Recommendation` tagged `label="technical"`. Rejected in favour of a distinct model: reusing the ticket-shaped model (with its `conviction`/`entry`/`stop`/`targets` fields) invites those fields to be populated on a thin basis, and the honesty then rests on a single enum value rather than on the type not having the fields at all. Structural omission is a stronger guarantee than a label.

## Notes

The `advisor` skill's charter extends to consuming this tool (it is a call, not a condition report), but the technical read never feeds `fuse()` and never carries conviction or levels — it is a sibling output, not an input to the fused tier. The four regime→direction rules are mechanical and documented in Plan 0074; they are textbook readings, not tuned signals, and carry no claim of edge — the absence of a conviction number is the point.
