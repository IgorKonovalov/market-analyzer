# ADR-0095 — Shared watchlist-scan fan-out harness

> **Status:** proposed
> **Date:** 2026-07-13
> **Related plan(s):** [0100](../plans/0100-watchlist-condition-scanners.md), consumed by [0101](../plans/0101-composite-quality-rank.md) and [0102](../plans/0102-crypto-sector-rotation.md)

## Context

We already ship two multi-symbol watchlist scanners — `volume_breakout` and `smart_volume` — and each one independently re-implements the same fan-out: validate a capped symbol list, read cached bars per symbol through the `MarketDataProvider` Protocol ([ADR-0007](0007-market-data-provider.md)), skip empty/errored symbols into a `skipped` list, honour `as_of` so the window truncates to `event_ts <= as_of` (the anti-lookahead guarantee), offload each read with `asyncio.to_thread`, sort the matches deterministically, and return `{matches, skipped, scanned_at}`. `volume_breakout.py` states the current posture explicitly: *"Kept self-contained per scanner tool rather than shared."*

That was reasonable at two scanners. Plans 0100–0102 add three-to-five more (squeeze, gainers/losers, momentum-band, quality-rank, sector-basket momentum). Copying a safety-critical loop five more times is exactly where the anti-lookahead contract, the symbol cap, the skip discipline, and the deterministic sort silently drift apart — a lookahead bug or a missing cap in one copy would be invisible against the five that got it right.

## Decision

We will extract a single shared async fan-out helper (`_scan_symbols(...)`, in `analysis/scanners.py` or `api/mcp_tools/_scan.py`) that owns the cross-cutting scan contract — symbol-cap enforcement, the per-symbol cached read via the provider, `as_of` truncation, the `asyncio.to_thread` offload, skip-on-empty/error into `skipped`, and the `scanned_at` provenance stamp — parameterised by a pure per-symbol scoring callable `(bars) -> ResultOrNone` and the scanner's result model. Each scanner tool supplies only its scoring function and its typed match model; the harness guarantees the shared invariants in one place. The two shipped scanners (`volume_breakout`, `smart_volume`) are refactored onto it behaviour-preservingly, and the "self-contained" note is retired.

## Consequences

### Positive
- One place enforces anti-lookahead, determinism, and the symbol cap — the invariants can no longer diverge between scanners.
- A new scanner becomes a pure scoring function plus a result model, not a re-implemented async loop with its own cap and skip handling.
- `MAX_SCAN_SYMBOLS` becomes a single shared constant instead of a per-file copy.

### Negative
- The helper couples the scanners: a change to the harness touches all of them, so it must stay small and stable. **This is the price** — a shared safety primitive is a shared blast radius.
- The parameterised-callable indirection is marginally less obvious to a first-time reader than a self-contained loop.
- Refactoring the two shipped scanners risks a behaviour regression; mitigated by their existing `_..._scan_response` unit tests, which must stay green unchanged through the refactor.

### Neutral
- The harness lives on the analyst/data side and is consumed by MCP tools; it is not itself a tool.

## Alternatives considered

### Alternative A — Keep each scanner self-contained
The status quo. Rejected: at six scanners the duplication crosses the line where DRY wins decisively — five more hand-copied anti-lookahead loops is precisely where a future lookahead or missing-cap bug hides, and the cost of an undetected lookahead bug in this domain is high.

### Alternative B — A base class / mixin for scanners
Rejected: Python class inheritance for a stateless fan-out is heavier than a function, and the per-symbol scoring step is naturally a callable, not an overridden method. A higher-order function models it more honestly.
