# ADR-0046 — MCP large-result delivery: bounded pages, not unbounded dumps

> **Status:** accepted (Plan 0050 close, 2026-06-09)
> **Date:** 2026-06-08
> **Related plan(s):** [0050-agent-surface-fixes](../plans/done/0050-agent-surface-fixes.md)
> **Related:** [ADR-0014](0014-mcp-as-second-sidecar-protocol.md) (MCP is the agent control surface), [ADR-0015](0015-claude-code-primary-control-surface.md) (the agent reads tool output into a finite context window), [ADR-0018](0018-backtest-result-schema.md) (the `BacktestResult` whose trades/equity this governs), [ADR-0013 §0013 cache-honest shape](../plans/done/0013-auto-backfill-on-cache-miss.md) (the `{bars, partial_reason, message}` precedent this extends)

## Context

The MCP tools return their result as text that lands directly in the agent's context window. That window is finite, and the harness enforces a hard per-tool-result token cap. Two tools can exceed it with ordinary inputs:

- **`get_ohlcv`** returns one JSON object per bar. A live session this week read `BTC-USD 1w` over 2015→2026 — **611 bars, ~108,992 characters** — which blew past the cap and was force-spilled to a file the agent then had to post-process out-of-band. Daily over the same span is ~4,100 bars; intraday is far worse. The tool is genuinely unusable inline for any multi-year window.
- **`get_backtest`** (the new MCP tool Plan 0050 adds, finding #1) would carry the full `BacktestResult` if it mirrored the renderer's `GET /backtests/{run_id}` route. The trade list is small (tens of rows), but the **equity curve is one point per bar** — the same unbounded series as `get_ohlcv`, and the 11-year daily run above produced a 185 KB `equity_curve.csv`. `run_backtest` already returns a deliberately-compact 5-metric summary "so the agent's conversation window stays compact" (its own docstring); a fetch tool that dumps the whole result would undo that discipline.

Both are the same problem — **an MCP tool handing an unbounded series to a context-bounded reader** — and both will recur for every future tool that returns a per-bar or per-row series (scanners, future analytics). A one-off fix in each tool would drift. The decision is a single shared contract for how MCP tools deliver large results.

A constraint worth stating: for `get_ohlcv` the *fetch* side must stay whole. The tool populates the cache by fetching the entire requested `[start, end]` window from upstream (ADR-0007 / Plan 0013); that behavior must not change. Only the *returned payload* is bounded — slicing the response must never shrink what gets cached.

## Decision

**MCP tools that return a per-element series cap the inline payload and page the remainder, surfacing the truncation as a typed, non-silent condition — they never return an unbounded series and never silently drop data.**

Concretely, reusing the established `{..., partial_reason, message}` cache-honest shape (ADR-0013):

1. **A per-tool maximum inline element count** (a module constant, e.g. `MAX_OHLCV_BARS`, `MAX_EQUITY_POINTS`) chosen to sit comfortably under the harness token cap. When a result would exceed it, the tool returns the first page and sets `partial_reason="too_large"` with a `message` that names the total count and tells the caller how to page or narrow.

2. **Deterministic offset/limit paging.** The series-returning tools gain `offset: int = 0` and a page-size limit (`max_bars` / `max_points`, defaulting to and capped at the constant). The response echoes `total_available`, `offset`, and `returned` so the caller can page forward without guessing. Paging is over the already-fetched/already-on-disk series — purely response-shaping, never re-fetching differently.

3. **Opt-in for the heavy, rarely-needed series.** `get_backtest` returns metrics + the full trade list **by default** (trades are bounded and are the common need — the trade-by-trade breakdown). The **equity curve is omitted unless `include_equity=true`**, and when included it obeys rule 2's paging. This keeps the default `get_backtest` reply small while still giving the agent a supported path to every field — closing the gap that today forces a filesystem read.

`too_large` is a partial *success* (bars/points were returned, just not all), consistent with the existing `partial_reason` semantics where a non-null reason means "you got some, here's why not all."

## Consequences

### Positive
- `get_ohlcv` over any window is usable inline again — the agent gets a bounded first page plus an honest "N more, page with offset=…" instead of a spilled file.
- `get_backtest` exists and returns trades inline (the actual use case that drove finding #1) without re-importing the equity-curve bloat the summary-only `run_backtest` was designed to avoid.
- One contract for every future series-returning tool; new tools copy the pattern instead of each inventing a cap.
- No silent truncation anywhere — a bounded result is always flagged, so the agent never mistakes "first 2000 bars" for "all the bars" (the data-integrity non-negotiable).

### Negative
- **The agent must page for large reads** — a multi-year daily history is several tool calls, not one. Accepted: the alternative is no usable result at all. The `message` makes the next call obvious, and most analysis wants either a recent window (fits in a page) or aggregates (better served by narrowing the window).
- **The caps are tuning knobs that can rot.** A harness token-cap change could leave them too high (overflow again) or needlessly low. Mitigation: the constants are named, centralized, and asserted by a test against a realistic worst-case row size, not scattered magic numbers.
- **A second `offset`-based read of a window that changed underneath (a backfill completed between pages) could see a shifted series.** Low risk for historical bars (immutable once cached) and for a persisted `BacktestResult` (immutable on disk); noted, not mitigated.

### Neutral
- `get_ohlcv` gains optional params but its default call (no `offset`/`max_bars`) is unchanged for windows under the cap — existing callers and the recent-window common case are unaffected.

## Alternatives considered

### Alternative A — Server-side downsampling (stride / LTTB to fit)
A `max_bars` that aggregates the series down to fit the cap. Rejected as the *primary* mechanism: it is lossy, so it silently corrupts exact analysis (trade reconstruction, indicator math, determinism checks) — the caller can't tell a downsampled bar from a real one. Acceptable only as an explicit, clearly-labeled overview mode; not the default and not in Plan 0050's scope.

### Alternative B — Compact columnar encoding (parallel arrays instead of per-bar objects)
Encode bars as `{ts:[...], open:[...], ...}` — ~3–4× denser. Rejected as a *solution*: density only postpones the cap (a long enough window still overflows) and it changes the wire shape every consumer parses, for a constant-factor win. It is a reasonable independent optimization but does not provide the hard bound the context window needs; paging does.

### Alternative C — Full result inline (mirror the REST route)
Have `get_backtest` return the entire `BacktestResult` like `GET /backtests/{run_id}` does. Rejected: it re-creates the exact `get_ohlcv` overflow for the equity curve and contradicts the deliberate summary-only design of `run_backtest`. The REST route can be unbounded because the renderer is not context-bounded; the MCP tenant is.

### Alternative D — Keep spilling to a file
Let large results spill to disk and have the agent read the file. Rejected: it only works because a filesystem tool happened to be available, leaks an out-of-band artifact path into the conversation, and defeats the point of a self-contained MCP surface (ADR-0014/0015). The session that hit this had to hand-write Python to parse the spill — not a contract we want to standardize.

## Notes
- The driving incident and the verified per-window sizes are recorded in Plan 0050's Context.
- This ADR is accepted at Plan 0050's close ceremony (proposed → accepted), per project convention for plan-gated ADRs.
