# ADR-0047 — Monthly is a native, variable-duration timeframe

> **Status:** accepted (Plan 0050 close, 2026-06-09)
> **Date:** 2026-06-08
> **Related plan(s):** [0050-agent-surface-fixes](../plans/done/0050-agent-surface-fixes.md)
> **Related:** [ADR-0028](0028-timeframe-resampling-and-expansion.md) (the canonical timeframe registry this extends), [ADR-0007](0007-market-data-provider.md) (bars flow through the Provider), [ADR-0009](0009-rewrite-data-layer-in-house.md) (we own the data/resampling layer)

## Context

The canonical timeframe registry (ADR-0028, `src/market_analyser/data/timeframes.py`) tops out at `1w`. A user asked for a **monthly** BTC chart this session; the agent had no `1mo` to request and silently substituted weekly. Adding monthly looks trivial but raises one decision that outlives the request, because monthly is the **first timeframe whose bars are not a fixed `timedelta` apart**.

Two facts shape the decision:

1. **Yahoo serves `1mo` natively.** ADR-0028 already notes Yahoo offers `1h, 90m, 15m, 1d, 1wk, 1mo`. So monthly needs **no in-house resampling** — unlike `4h` (derived from `1h`), it is fetched directly like `1d`/`1w`. The calendar-aware aggregation that monthly-from-daily would require (variable month lengths, DST, month boundaries) is avoided entirely.

2. **`TimeframeSpec.bar_duration` is a `timedelta`, and the coverage/gap math reads it.** Every existing timeframe has a constant spacing: `1w` is exactly `timedelta(days=7)`. A month is 28–31 days. The gap detector that decides "are these two cached bars adjacent or is one missing?" consults `bar_duration`; a single fixed value cannot equal every real month-step, so a naive `timedelta(days=30)` would either flag February-to-March as a false gap or miss a genuinely-absent 31-day month, depending on the comparison's direction. This is the actual decision monthly forces.

## Decision

**Add `1mo` as a native (Yahoo `1mo`), unbounded-history timeframe, and define its `bar_duration` as the *maximum* month length so the coverage invariant holds across all real months without false gaps while still detecting truly-missing months.**

Registry row: `yahoo_interval="1mo"`, `resampled_from=None`, `max_history=None` (effectively unbounded, like `1d`/`1w`), `bar_duration=timedelta(days=31)`.

The coverage/gap math treats `bar_duration` as the **upper bound on the spacing between two adjacent bars**, not as an exact step: two consecutive monthly bars are 28–31 days apart, all ≤ 31, so they read as adjacent; a real hole (two bars ~59+ days apart) exceeds 31 and is correctly flagged as missing. Implementing this requires confirming the existing gap detector uses a bounded-spacing comparison (or adapting it to), rather than assuming exact `bar_duration` multiples — that confirmation is an explicit done-when in Plan 0050, not an assumption of this ADR.

## Consequences

### Positive
- Monthly charts and monthly analysis become first-class; the agent requests `1mo` instead of silently downgrading to weekly.
- No calendar-resampling code — monthly rides the same native-fetch path as `1d`/`1w`, keeping the variable-length complexity out of `src/`.
- The "`bar_duration` is the max adjacent spacing" reading is a small, documented generalization of the coverage invariant that any future variable-duration timeframe (e.g. quarterly) can reuse.

### Negative
- **`bar_duration=31d` is an approximation, not the true step.** Any consumer that multiplies `bar_duration` to project calendar time (e.g. "N bars ≈ X days") will be slightly long for short months. Today's consumers use it only for adjacency/gap detection, where the max-spacing reading is correct; this is documented so a future consumer doesn't assume exactness. If one ever needs exact month math, it must use calendar arithmetic, not `bar_duration`.
- **Yahoo's `1mo` bar-stamping convention (first-of-month, and whether the final in-progress month is partial) is inherited, not controlled by us.** The partial-current-month bar is anti-lookahead-safe (it aggregates only elapsed days) but its value moves until the month closes — the same property the `4h` partial-final-bucket already has (ADR-0028). Documented so it isn't mistaken for a data bug.

### Neutral
- The registry-keys-equal-`SUPPORTED_TIMEFRAMES` parity test (ADR-0028) automatically extends to `1mo` once both sides list it; no new invariant.

## Alternatives considered

### Alternative A — Resample monthly from daily/weekly in-house (calendar-aware)
Aggregate native `1d`/`1w` bars into months ourselves, the way `4h` derives from `1h`. Rejected: Yahoo already serves `1mo` natively, so this would add calendar-boundary aggregation code (variable month lengths, year rollovers) for zero capability gain. ADR-0028 chose in-house resampling for `4h` *only because* Yahoo lacks a 4h interval; that justification is absent here.

### Alternative B — Make the coverage/gap math fully calendar-aware for monthly
Teach the gap detector real month arithmetic (a "next month" function) instead of a bounded `timedelta`. Rejected as over-engineering for one timeframe: the max-spacing reading of `bar_duration` gets the adjacency/hole decisions right with no new calendar logic, and it generalizes to other variable cadences. Revisit only if a consumer needs exact monthly boundaries.

### Alternative C — Reject monthly; document the weekly ceiling instead
Don't add `1mo`; just make the unsupported-timeframe error explicit so the agent stops silently substituting. Rejected: the sidecar *already* rejects unknown timeframes (`_require_supported_timeframe`) — the silent substitution was an agent-side choice, not a sidecar gap — and monthly is a genuinely useful, cheaply-available rung. Declining a native, no-resampling timeframe to avoid one approximation constant is a poor trade.

### Alternative D — No ADR, fold into the plan body
Rejected for the same reason ADR-0028 itself was written: the variable-duration `bar_duration` convention is a durable contract a future maintainer (adding quarterly, or debugging a "phantom gap") will need the reasoning for. A closed plan in `done/` is not a discoverable decision record.

## Notes
- Accepted at Plan 0050's close ceremony (proposed → accepted).
- The renderer must also learn `1mo` (timeframe selector + month-axis formatting) for the chart to render it — that is a `ui-builder` phase in Plan 0050, not a data-layer concern of this ADR.
