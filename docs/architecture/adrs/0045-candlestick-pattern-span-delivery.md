# ADR-0045 — Candlestick pattern delivery: span-bearing markers, derived not persisted

> **Status:** accepted (2026-06-06, at Plan 0049 close)
> **Date:** 2026-06-06
> **Related plan(s):** [0049](../plans/0049-pattern-sweep-and-span-rendering.md); builds on [0047](../plans/0047-bar-upsert-mtf-degrade-and-chart-legend.md)

## Context

`detect_patterns` (`src/market_analyser/analysis/patterns.py`) finds all 14 candlestick
patterns over a bar series — 4 single-bar (doji, hammer, hanging_man, marubozu), 6 two-bar
(engulfing ×2, dark_cloud_cover, piercing_line, harami ×2), 4 three-bar (morning/evening
star, three white soldiers / black crows). Each hit is a `PatternHit(bar_index, pattern,
direction, strength)` where `bar_index` is the *completing* bar and `direction ∈
{bullish, bearish, neutral}`.

The only path from a detected pattern to the chart today is the agent-driven MCP tool
`highlight_pattern`, which emits **exactly one** marker per call on the `chart.highlight v1`
event (`src/market_analyser/events/__init__.py`). That marker is point-in-time and binary:

```python
class Marker(BaseModel):
    event_ts: datetime
    kind: Literal["bullish_marker", "bearish_marker"]
    label: str | None = None
```

Three forces make this a decision rather than a no-brainer, surfaced by the 2026-06-05 smoke
("patterns show only once on the chart"):

1. **No bulk path.** Nothing converts a whole range's worth of `PatternHit`s into markers; the
   agent would have to call `highlight_pattern` once per pattern. "See all patterns in range"
   has no mechanism.
2. **The marker model is lossy.** `kind` is binary, so **neutral** patterns (doji, neutral
   marubozu) cannot be represented at all. Pattern *identity* lives only in free-text `label`.
   The renderer dedup key is `(event_ts, kind)` everywhere (`chartHandlers.ts` `dedupHighlights`,
   `OhlcvView.tsx` `mergePolledAndLive`), so two **different** patterns on the **same bar with
   the same direction** (e.g. a doji and a hammer) silently collapse into one marker.
3. **Multi-bar patterns lose their span.** `PatternHit` carries only the completing
   `bar_index`; a 3-bar morning star renders as a single arrow on its last bar. The span the
   pattern occupies is gone end-to-end.

Plan 0047 (in-flight) deliberately scopes these *out*: its "does NOT do" pins markers at
`bullish_marker`/`bearish_marker` and colors "per-pattern" by parsing the label. So the
first-class representation is genuinely undecided, and it is a schema change to a versioned
event payload — ADR territory.

A second force is **persistence**. `highlight_pattern` persists each marker as an `Annotation`
row so a reopened Electron sees agent-authored highlights. A pattern *sweep* is different in
kind: it is **derived data** — a deterministic, trailing (no-lookahead) function of the cached
bars. The same bars always yield the same patterns. So a sweep can always be recomputed; it
does not need to be persisted, and persisting it would invite staleness (stored spans diverging
from recomputed ones when bars are revised) plus an Alembic migration on a single linear chain.

## Decision

We will deliver candlestick-pattern sweeps as **span-bearing markers on the existing
`chart.highlight` channel**, and treat them as **derived, non-persisted** data.

**Marker model — extend, don't fork.** The `chart.highlight` marker gains first-class pattern
identity and an optional span, additively:

- `pattern: str | None` — the detector name (`"morning_star"`); identity, not presentation.
- a **neutral** direction — add `neutral_marker` to the `kind` vocabulary so doji et al. can be
  emitted faithfully (`kind` stays the rendering discriminator; `bullish_marker`/`bearish_marker`
  unchanged for backward compatibility).
- `span_start_ts: datetime | None`, `span_end_ts: datetime | None` — present for multi-bar
  patterns, absent (≡ point marker on `event_ts`) for single-bar ones.
- `strength: float | None` — the detector score, so the renderer styles by conviction without
  re-deriving it.

All additions are optional, so the existing `highlight_pattern` tool keeps working unchanged.
The renderer dedup key moves from `(event_ts, kind)` to **`(event_ts, pattern, kind)`** (falling
back to `(event_ts, kind)` when `pattern` is absent), which fixes the same-bar/same-direction
collision.

**Emission.** A new MCP tool `scan_patterns(symbol, timeframe, range_start, range_end, …)` runs
`detect_patterns` over the range and publishes **one** `chart.highlight` event carrying **all**
detected markers (with spans resolved from bar timestamps). It does **not** write `Annotation`
rows — sweep results are derived and recomputed on demand, never persisted.

## Consequences

### Positive
- "Show all patterns in range" becomes a single agent tool call, not N manual `highlight_pattern`
  calls; the renderer already accumulates markers correctly once they arrive.
- Neutral patterns (doji) become representable; same-bar distinct patterns stop collapsing.
- Multi-bar patterns can render as the span they actually occupy (Plan 0049 phase 5).
- No migration. Sweeps stay reproducible from bars; nothing in the persisted store can go stale
  against the detectors.
- One marker plumbing path: the extended channel reuses Plan 0047's marker styling, hover
  tooltips, and layers-legend work rather than standing up a parallel delivery channel.

### Negative
- The `chart.highlight` marker is now a single model doing two visual jobs — a point arrow and a
  span bracket — distinguished by the presence of `span_*`. The renderer must branch on it.
- Sweep markers are ephemeral: reopening Electron loses them until the sweep is re-run (acceptable
  for derived data; agent-authored `highlight_pattern` markers still persist).
- The hand-maintained TS mirror (`desktop/renderer/types/events.ts`) and its parity test must
  track the new fields in lockstep (`extra="forbid"` means the renderer can't ignore them).
- Adding `neutral_marker` widens a `Literal`; every exhaustive switch over `kind` must add the arm.

### Neutral
- `PatternHit` grows a span notion (a `span_bars` count or `start_bar_index`); the value is
  static per pattern, so it is bookkeeping, not new analysis.

## Alternatives considered

### Alternative A — A dedicated `chart.patterns` event
A new event type carrying a list of `PatternSpan` objects, separate from `chart.highlight`.
Cleaner type separation (a span is not a point marker), but it duplicates the marker plumbing the
renderer already has — accumulation, dedup, the legend/tooltip work landing in Plan 0047 — into a
second channel that renders the same glyphs. Rejected: the duplication cost outweighs the modeling
purity, and the user chose to extend the marker model.

### Alternative B — Persist pattern spans to the `Annotation` table
Treat sweep results like agent-authored highlights and store them, so reopening shows them.
Rejected: pattern spans are derived and deterministic from bars; persisting them adds an Alembic
migration to a single linear chain (a parallel-execution hazard, per plans/README) and creates a
staleness surface — stored spans can disagree with what the detectors now produce when bars are
revised. Recompute-on-demand is both cheaper and can't drift.

### Alternative C — Keep `kind` binary, dedup by `label`
Minimal: leave the enum, but add `label` to the dedup key so distinct patterns on one bar survive.
Rejected: it still cannot represent neutral patterns (doji has no bullish/bearish kind), and it
makes the presentational `label` load-bearing for identity — the wrong field to key on.

## Notes

- No-lookahead is preserved by construction: `detect_patterns` is trailing, and a sweep reports a
  pattern at bar `i` only from `bars[0..=i]` ([cross-cutting non-negotiables](../../../CLAUDE.md)).
- The span-rendering primitive feasibility (lightweight-charts `ISeriesPrimitive` vs a canvas
  overlay) is a Plan 0049 phase-5 implementation risk, not a decision this ADR makes.
