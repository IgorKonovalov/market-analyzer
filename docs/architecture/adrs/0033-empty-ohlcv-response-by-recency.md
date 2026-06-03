# ADR-0033 — Empty Yahoo OHLCV response classified by window recency

> **Status:** proposed — accepts at Plan 0031 close
> **Date:** 2026-06-03
> **Related plan(s):** [0031](../plans/0031-yahoo-absolute-range-fetch.md) (phase 2 — the change this records), [0030](../plans/0030-lazy-historical-loading.md) (the backward-paging feature this unblocks)
> **Related ADRs:** [ADR-0007](0007-market-data-provider.md) (the Provider/adapter contract — unchanged); refines the Plan 0013 `UnknownSymbolError` heuristic in [`data/errors.py`](../../../src/market_analyser/data/errors.py)

## Context

Plan 0013 gave the data layer a typed upstream-error taxonomy. Yahoo's chart endpoint has no explicit "no such symbol" signal, so the `YahooAdapter` *infers* one: an empty upstream response raises `UnknownSymbolError`, on the reasoning that "zero bars on a known-good interval is implausible for a live, listed name." The original discriminator was **period size** — `errors.py`'s `UnknownSymbolError` docstring distinguishes the unknown-symbol case from a legitimate empty (a weekend gap) "by the period size" (a multi-day window should contain bars).

Plan 0031 switches the fetcher to absolute `period1`/`period2`, so a window ending in the **past** is fetched verbatim for the first time. Plan 0030's scroll-left backward paging is the first feature to request strictly-historical windows. When a user scrolls a chart back past the symbol's first listing date (or past Yahoo's coverage), Yahoo returns an **empty window for a perfectly valid symbol** — and the period-size heuristic misreads it as `UnknownSymbolError`. The adapter cannot tell these two cases apart from the empty response alone:

- **(a)** a genuinely unknown / unlisted symbol, and
- **(b)** a valid symbol whose requested *older* window predates its listing.

This conflation breaks Plan 0030's done-when directly: its Phase 2 manual smoke requires backward paging to "stop cleanly at the start of available history **with no error chip**." The renderer latches `reachedStart` on an empty **200 `[]`**, not on an error. With case (b) raising `UnknownSymbolError`, `/ohlcv` returns an error status, `loadOlder` sets `olderError`, the chip shows, and `reachedStart` never latches — the feature reads as broken at the one boundary that matters.

Two forces constrain the fix:

- **Determinism / anti-lookahead.** Any "is this window recent?" judgment must go through the provider's `_now`/`as_of` seam, not a raw wall-clock read in the adapter. `default_provider` is already "the single owner of that determinism seam" (its own comment), and historical replay freezes `_now`; a clock read in the adapter would reintroduce the leak the seam exists to prevent.
- **ADR-0007 stays intact.** The `MarketDataProvider.get_ohlcv` Protocol signature, return types, and `as_of` seam must not change. Only adapter-internal code is in play.

## Decision

We will classify an empty Yahoo OHLCV response by the requested window's **recency**, not by its emptiness alone or its span. An empty upstream response raises `UnknownSymbolError` **only when the requested window reaches the leading edge** — its `end` lies within one bar of "now" (the provider's `_now`/`as_of` reference), where a live, listed symbol must have data. For a strictly-historical window — one whose `end` predates the leading edge — an empty response is a legitimate "no data in this range" and returns `[]`, which the provider passes through unchanged (its existing `if not fetched: continue` gap path) and the renderer latches as `reachedStart`.

The recency reference is threaded from the provider's existing `_now`/`as_of` seam into the adapter as an adapter-internal parameter on `YahooAdapter.fetch_ohlcv`; **the `MarketDataProvider` Protocol (ADR-0007) is untouched**, and the adapter never reads the wall clock itself. This narrows the Plan 0013 heuristic: the discriminator changes from *period size* to *window recency*. The two real call patterns then separate cleanly — initial loads and backfills always end at "now" (so a truly-unknown symbol still raises `UnknownSymbolError`), backward paging always ends in the past (so end-of-history surfaces as `[]`).

## Consequences

### Positive
- **Plan 0030's backward paging stops cleanly with no error chip.** The empty older window is a `200 []` → `reachedStart`, not an error — exactly its done-when.
- **The route's `UnknownSymbolError` → 404 mapping (Plan 0031 phase 2) becomes unambiguous.** 404 now means only "symbol not found," never "ran out of history." End-of-history is expressed as *data* (`[]`); errors stay errors. This is the property that makes option (ii) cleaner than the renderer-side 404-means-reachedStart shim.
- **Detection stays where the empty response is observed** (the adapter) but is anchored on the provider's determinism seam — no new wall-clock read, no lookahead risk, testable by freezing `default_provider._now`.
- **No ADR-0007 Protocol change, no wire/persisted shape change.** `GET /ohlcv` still returns `Bar[]`; only the adapter-internal fetch signature gains the now-reference.

### Negative
- **The heuristic is still a heuristic.** A genuinely-unknown symbol queried with a *past-ending* window would now return `[]` instead of raising. We accept this: the app never requests a past-ending window for a symbol it has not already resolved with a now-ending initial load, so this path is not generated in practice.
- **One more parameter threads through the adapter-internal fetch path** — the adapter is no longer purely `(symbol, window)` but `(symbol, window, now-reference)`. Mild signature churn, fully contained below the Protocol.
- **The `errors.py` `UnknownSymbolError` docstring must be updated** (it documents the period-size discriminator), and any test asserting "empty past window raises `UnknownSymbolError`" flips to "returns `[]`."

### Neutral
- The threshold is "within one bar of now." It is tunable (one bar vs a small multiple, to absorb holiday closures on a short now-ending window) **without revisiting this decision** — the principle is "the window should contain what would be the latest bar."

## Alternatives considered

### Alternative A — Keep the empty→`UnknownSymbolError` heuristic; resolve it in the renderer (Plan 0031 phase 2 option (i))
Map `UnknownSymbolError` → 404 at the route and have `useOhlcvHistory.loadOlder` treat a 404 on an *older-chunk* fetch as `reachedStart`. Rejected: it overloads 404 to mean both "no such symbol" and "reached start," pushes a data-layer distinction into the renderer (reopening a `ui-builder` touch on the otherwise-finished Plan 0030), and leaves the data layer reporting a normal end-of-history as an error. The boundary that actually knows the difference is the data layer, not the renderer.

### Alternative B — Resolve the symbol via the search endpoint on every empty response
On an empty fetch, call Yahoo `/v1/finance/search`; if the symbol resolves, return `[]`, else raise `UnknownSymbolError`. Rejected: an extra network round-trip per empty fetch on the hot paging path, and it couples the OHLCV fetch to the search adapter for a signal that recency already gives for free and offline.

### Alternative C — Keep the period-size discriminator (status quo, refined)
Rejected as insufficient: a backward page requests a *wide* (e.g. one-year) window, so "multi-day span ⇒ bars must exist ⇒ empty means unknown" misfires precisely on the legitimate historical-empty case. Span size does not separate (a) from (b); recency does.

## Notes

- Implementation lands in Plan 0031 phase 2: `data/adapters/yahoo.py` (recency-gate the empty-response branch), `data/default_provider.py` (thread `_now`/`as_of` into the fetch call), `data/errors.py` (docstring), and the route + adapter tests. The route-status half of phase 2 (`UpstreamDataError` → typed HTTP, finding M1) is orthogonal to this ADR and stands on its own.
- The renderer needs no change: `useOhlcvHistory` already latches `reachedStart` on an empty older chunk.
