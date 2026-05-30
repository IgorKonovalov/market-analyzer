# ADR-0028 — Canonical timeframe registry, in-house 4h resampling, and per-timeframe history caps

> **Status:** accepted (2026-05-30, at [Plan 0025](../plans/done/0025-timeframe-expansion.md) close)
> **Date:** 2026-05-29
> **Related:** [ADR-0007](0007-market-data-provider.md) (bars flow through the Provider), [ADR-0009](0009-rewrite-data-layer-in-house.md) (we own and evolve the data layer, including resampling), [ADR-0019](0019-external-http-adapter-resilience.md) (the Yahoo fetch stays on the resilience client), [Plan 0025](../plans/0025-timeframe-expansion.md) (the implementing plan), [Plan 0021](../plans/0021-multi-timeframe-and-volume-scanners.md) (the consumer that forced the question)

## Context

The data layer supports exactly `1d` and `1h`. [Plan 0021](../plans/0021-multi-timeframe-and-volume-scanners.md) needs `weekly`, `4h`, and `15m` for multi-timeframe alignment. Expanding the set raises three decisions that outlive the plan that triggers them, so they belong in an ADR:

1. **Where does timeframe knowledge live?** Today it is scattered: the supported *set* in `annotations/types.py`, the fetch *interval* threaded as a defaulted parameter through `adapters/yahoo.py` → `_yahoo_fetch.py` (whose parser branches on a binary `interval == "1h"` intraday/daily test), and **no** central per-timeframe bar duration at all (so the coverage/gap math has nothing to read for a new cadence). Adding three timeframes by editing each site invites drift.
2. **How do we get `4h`?** Yahoo serves `1h`, `90m`, `15m`, `1d`, `1wk`, `1mo` natively but **not `4h`**. Either we drop 4h, or we synthesise it.
3. **How do we handle Yahoo's intraday history limits?** `15m` history is ≈60 days, `1h` ≈730 days, `1d`/`1wk` effectively unbounded. A request beyond a timeframe's reach must not look like a successful empty result.

## Decision

**1. A canonical timeframe registry is the single source of truth.** A new `src/market_analyser/data/timeframes.py` maps each supported timeframe string to: its **bar duration** (`timedelta`), its **Yahoo fetch interval** (`None` when the timeframe is derived), its **resampled-from base** (`None` when native), and its **max-history cap** (`timedelta | None`). The Yahoo adapter's valid-set and interval selection, the coverage/gap math, the resampler, and the boundary tool descriptions all read the registry. `SUPPORTED_TIMEFRAMES` remains the canonical *set* in `annotations/types.py` (so the MCP and annotations validators keep their existing import with no new cross-layer dependency), and a test asserts the registry's keys equal `SUPPORTED_TIMEFRAMES` — two views, one enforced invariant.

The initial registry: `15m` (native), `1h` (native), `4h` (resampled from `1h`), `1d` (native), `1w` (native, Yahoo `1wk`).

**2. `4h` is resampled in-house from native `1h` bars, derived on read.** A pure function aggregates `1h` bars into `4h` bars on a **fixed UTC-aligned grid** — bucket boundaries at `00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC`. Each `4h` bar aggregates the `1h` bars whose timestamps fall in `[bucket_start, bucket_start + 4h)`:

- `open` = first 1h bar's open, `high` = max high, `low` = min low, `close` = last 1h bar's close, `volume` = sum.

The aggregation is **trailing by construction** — an output bar reads only the `1h` bars inside its own closed window, never future bars — so it carries no lookahead. A **partial final bucket** (fewer than four `1h` bars at the series end) is emitted from the bars available so far rather than dropped or forward-padded; this is still anti-lookahead-safe (it uses only `bars[0..=i]`). `4h` is **derived on read**: the cache stores the `1h` base, and a `4h` request fetches/serves `1h` and resamples — `4h` is never separately cached.

**3. Per-timeframe history caps surface honestly.** A request whose window exceeds a timeframe's `max_history` returns the Plan 0013 cache-honest shape (`{bars, partial_reason, message}`) with a typed reason, not a silent empty success or a raw 5xx. Resampled `4h` inherits the `1h` base's ≈730-day cap.

## Consequences

**Positive:**
- One registry means adding or tuning a timeframe is a single, reviewable edit; the supported set, fetch interval, coverage cadence, and resampling base can no longer disagree (a test enforces set/registry parity).
- In-house `4h` gives Plan 0021 its full ladder without depending on Yahoo to offer a 4h interval, and the aggregation stays inside the deterministic `src/` path where the no-lookahead / determinism non-negotiables are testable.
- Derive-on-read for `4h` avoids double-storage and the base/derived drift that a separately-cached 4h series would risk.
- History caps make Yahoo's intraday limits a visible, typed condition instead of a confusing empty response.

**Negative (the price we pay):**
- **UTC-aligned 4h buckets do not match any exchange session.** A 4h candle spans `08:00–12:00 UTC` regardless of where the instrument trades. This is a deliberate trade of session-exactness for determinism and venue-independence — we have no exchange-calendar data, and the condition-reporting consumers care about cross-timeframe trend agreement, not session-perfect candles. Documented so it isn't mistaken for a bug.
- **Derived-on-read 4h costs CPU per request.** Trivial for the bar counts involved, but it means a 4h read is never a pure cache hit. Accepted; the alternative (caching 4h) is worse (storage + drift).
- **Two views of the supported set** (the `SUPPORTED_TIMEFRAMES` frozenset + the registry) need a sync test to stay honest. Accepted as cheaper than introducing an `annotations → data` import to collapse them into one.
- **The resampler is `1h → 4h` only**, not general. A future need for `2h`/`8h` means extending it. Accepted — building a general aggregator now would be speculative.

## Alternatives considered

- **Native-only — drop `4h`.** Add just `15m` + `1w`; Plan 0021's ladder loses the 4h rung. Rejected at interview: 1h→4h aggregation is cheap and correctness-bounded, and 4h is a commonly-watched rung; dropping it weakens the headline capability for little saving.
- **Resample everything from `1m`.** A single fine base resampled up to all intervals. Rejected: Yahoo's `1m` history is ≈7 days (useless for alignment over weeks/months) and it multiplies fetch volume and storage.
- **Resample in the renderer.** Let the chart aggregate 1h → 4h client-side. Rejected: it puts financially-meaningful aggregation outside the deterministic, tested `src/` path and duplicates the rule across every consumer (chart, scanners, analysis).
- **Cache `4h` as its own series.** Store resampled 4h bars in the cache. Rejected: doubles storage and creates a base/derived consistency burden (a corrected 1h bar would silently diverge from the cached 4h); derive-on-read is simpler and always consistent.
- **Leave the supported set scattered, just add strings in each place.** Rejected: three edit sites with no shared source is exactly the drift this registry prevents; the coverage math has no per-timeframe duration to read otherwise.
- **No ADR — fold into the plan body.** Rejected: the UTC-bucket rule, derive-on-read, and the history-cap contract are durable decisions a future maintainer will want the reasoning for; a closed plan in `done/` is not a discoverable contract.
