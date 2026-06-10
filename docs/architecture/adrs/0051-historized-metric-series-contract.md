# ADR-0051 — Historized external metric series: one contract, one table

> **Status:** accepted (2026-06-10, at Plan 0055's close)
> **Date:** 2026-06-09
> **Related plan(s):** 0055-cycle-macro-series-spine (lands the contract), 0056-binance-derivatives-data, 0057-onchain-valuation-metrics (both historize through it), 0059-forecast-feature-set-v2 (consumes the as-of join)
> **Related ADRs:** [ADR-0006](0006-persistence-layout.md) (the SQLite cache this extends), [ADR-0027](0027-crypto-macro-regime-classification.md) (the snapshot-only macro read this historizes), [ADR-0031](0031-data-source-adapter-contract.md) (the per-capability source Protocol pattern this follows), [ADR-0030](0030-forecasting-subsystem.md) (the causality rule the as-of read enforces)

## Context

The app's crypto-meta reads are point-in-time only: `bitcoin_market_pulse` (BTC dominance, total market cap — ADR-0027) and `crypto_fear_greed` return the current value and discard it. Nothing outside OHLCV bars is historized. That blocks the two uses the user has now asked for: **backtesting against meta conditions** ("how did X perform when F&G was below 20") and **forecast features** (dominance trend, funding rate, MVRV as model inputs — Plan 0059). A feature the model can't read historically can't be trained on at all.

Three new source families are about to land (macro/cycle — Plan 0055; Binance derivatives — Plan 0056; on-chain valuation — Plan 0057), and all of them produce the same shape: **a scalar value per (named series, UTC timestamp)**. The decision point is whether each family gets its own table and migration, or whether one generic contract serves them all. The repo has a hard constraint that makes this non-academic: **Alembic migrations are one linear chain**, and plans that add migrations cannot run in parallel worktrees (plans/README § Parallel execution). Three plans each adding a table would serialize the whole program; one contract landing one migration up front keeps Plans 0056/0057 migration-free and parallel-able.

A second force: the forecast pipeline's causality invariant (ADR-0030 invariant 1). Exogenous series join onto bars by time, and the join itself is a leakage surface — reading a metric point timestamped after the bar being featurized is lookahead. The storage contract must make the *safe* read the easy read.

## Decision

We will add a single generic SQLite table for external metric time series, one `MetricSeriesSource` Protocol, and one repository:

- **Table `metric_points`** — `(series_id TEXT, ts INTEGER (UTC epoch seconds), value REAL, PRIMARY KEY (series_id, ts))`. One migration, landed by Plan 0055. No per-family tables.
- **Series ids are namespaced strings** registered in one module-level registry (`data/metric_series.py`): e.g. `fng.value`, `coingecko.btc_dominance`, `coingecko.total_mcap_usd`, `binance.funding_rate.BTCUSDT`, `binance.open_interest.BTCUSDT`, `blockchain.mvrv`. Unregistered ids are rejected at the repository boundary — the registry is the schema.
- **`MetricSeriesSource` Protocol** (in `data/sources.py`, per ADR-0031's selector-registry pattern): `fetch_series(series_id, start, end) -> list[MetricPoint]`. Sources that expose history implement a backfill; snapshot-only sources (dominance) are sampled by appending the current value at poll time.
- **Repository reads come in exactly two shapes:** `range(series_id, start, end)` and `as_of(series_id, ts)` — the latter returns the latest point with `point.ts <= ts`. **`as_of` is the only join primitive the forecast feature pipeline is allowed to call** — the no-lookahead rule enforced at the storage seam, mirroring ADR-0007's `as_of` argument.
- **Immutability:** points are upsert-once; a re-fetch that disagrees with a stored point overwrites it only via an explicit `refresh` path (revisions are a source-quality problem, surfaced — not silently absorbed).

## Consequences

### Positive
- **One migration for the whole program.** Plans 0056/0057 register series ids and adapters — dict entries and a Protocol implementation, no schema work — and stay parallel-able under the migration-chain rule.
- The `as_of` read makes the causally-safe join the default; Plan 0059's leakage tests can target one seam instead of N.
- Uniform tooling: one paged `get_metric_series` MCP tool (ADR-0046 paging) serves every series ever added.
- Backfillable sources (F&G, funding, on-chain charts) get full history on first fetch; the backtest use-case works retroactively for them.

### Negative
- **Snapshot-only series accrue from deployment day, not from history.** BTC dominance via the free CoinGecko `/global` endpoint has no historical form — the series starts when Plan 0055 ships and grows by polling. A dominance-trend forecast feature will have months of warm-up before it's trainable. We accept this honestly rather than buying a paid history source on day one.
- A generic `(series_id, ts, value REAL)` shape can't hold structured points (multi-field metrics, strings). Anything non-scalar either decomposes into several series ids or doesn't fit. Acceptable: every series in the current program is a scalar.
- The series-id registry is a discipline point — a typo'd or unregistered id must fail loudly, which the repository boundary check provides, but registry hygiene is ongoing.

### Neutral
- Cadence is per-series and irregular (daily F&G, 8-hour funding, poll-time dominance). The table doesn't model cadence; `as_of` semantics absorb gaps naturally.
- Retention is unbounded — these series are tiny (one float per period) compared to bars.

## Alternatives considered

### Alternative A — Per-family tables (`fng_history`, `funding_rates`, `onchain_metrics`)
Tighter per-table typing, but three-plus migrations on one linear chain serializes Plans 0056/0057 against everything else (and against the in-flight 0035/0044/0053 migration plans), and each new metric family is a schema change forever after. Rejected: the shape really is identical across families; paying the parallelism cost for cosmetic typing is a bad trade.

### Alternative B — Reuse the `bars` table with degenerate OHLCV
Stuff metrics into `bars` (`close=value`, rest null/duplicated). No new migration at all. Rejected: it poisons the bars cache's invariants (OHLC > 0 validation, timeframe semantics), confuses every existing bars consumer, and saves one small migration at the cost of permanent ambiguity.

### Alternative C — JSON snapshot blobs per fetch
Append whole API responses as JSON rows; parse at read time. Maximum fidelity, no registry needed. Rejected: no indexed range/as-of queries (the two reads the program exists to serve), and parse-at-read pushes source-format knowledge into every consumer.

## Notes
- The registry doubles as provenance: Plan 0059's forecast output lists the `series_id`s (and each one's latest point `ts`) that fed the feature row, so a forecast is auditable back to its inputs.
- Determinism: repository reads are ordered by `ts` (primary-key order), never by hash iteration; `as_of` is a deterministic max-`ts` lookup.
