# ADR-0018 — BacktestResult schema

> **Status:** proposed
> **Date:** 2026-05-20
> **Related plan(s):** [0008-backtest-engine-v1](../plans/0008-backtest-engine-v1.md), [0002-strategy-interface](../plans/0002-strategy-interface.md) (deferred the schema to this ADR's pairing plan)
> **Related ADRs:** [ADR-0004](0004-strategy-interface.md) (strategy contract — names the missing engine half), [ADR-0006](0006-persistence-layout.md) (SQLite + on-disk artifact discipline), [ADR-0007](0007-market-data-provider.md) (`as_of` seam — bars input identity), [ADR-0017](0017-live-ui-updates-via-sse.md) (`run.completed v1` envelope — wire-level consumer of `run_id` + `artifact_path`)

## Context

[ADR-0004](0004-strategy-interface.md) locked the strategy contract — `META`, `Params`, `generate_signals(bars, params)` — and explicitly left the engine and its result shape for a follow-up. [Plan 0002](../plans/0002-strategy-interface.md) shipped the contract module, the six reference strategies, and the `signals_to_trades` adapter (with the `Trade` type), but it punted the `BacktestResult` schema and the four metric helpers to this ADR's paired plan. The architect's project-context backlog has carried "Backtest result schema" as the next required decision since 2026-05-19, with the note that it must land before the first engine phase ships.

Three forces shape the decision:

1. **Determinism is non-negotiable.** Same inputs → byte-identical outputs ([`CLAUDE.md`](../../../CLAUDE.md) cross-cutting rule). A re-run from the same `spec.json` must produce the same `result.json`, modulo a small set of non-deterministic fields (run identity, wall-clock timestamps, engine version). The schema must make this property cheap to assert.
2. **The result has three consumers with different shapes.** (a) The MCP `run_backtest` tool returns a summary to the agent (small, JSON-serializable, latency-sensitive). (b) The UI's backtest results view consumes the full curve + trade log + metrics (bigger, fetched on demand). (c) The SQLite index supports "list recent backtests for AAPL" queries (just a row's worth of searchable fields). One schema must serve all three without forcing every consumer to load the largest representation.
3. **The `run.completed v1` envelope is already wire-frozen.** [ADR-0017](0017-live-ui-updates-via-sse.md) fixed the envelope payload as `{kind: "backtest" | "analysis" | "defi", run_id, artifact_path}`. This ADR must keep its choices consistent with that wire — specifically, `run_id` and `artifact_path` (relative to `runs/`) are load-bearing, and the on-disk layout under `runs/<run_id>/` must hold whatever the UI fetches when it routes on the envelope.

Two schema-shape questions were genuinely in contention:

- **Flat vs nested metrics.** Either every metric is a top-level field on `BacktestResult`, or the four metric helpers' outputs cluster under a `metrics: BacktestMetrics` sub-model. Flat is simpler to serialize; nested mirrors the engine's module organization (one helper, one cluster of fields).
- **Inline equity curve vs reference-only.** The full per-bar equity curve can be ~10k entries for multi-year minute-bar backtests. Inline keeps `run()` self-contained (returns a single object with everything); reference-only forces a disk round-trip for the UI but keeps the in-memory result small.

Two identity questions were also in contention:

- **`run_id` derivation.** UUID4 (random, fresh each call) vs content-addressed hash of the `spec.json` (re-runs of identical specs share an id and implicitly cache).
- **Data identity.** Does the result embed any fingerprint of the bars it ran on, or rely on the spec fields (`symbol + timeframe + range`) as a proxy? The data layer's `as_of` seam ([ADR-0007](0007-market-data-provider.md)) means two backtests with the same spec can hit different cached bars if the cache was refreshed in between — silently producing different results.

## Decision

We will define `BacktestResult` as a frozen Pydantic model that holds **everything `run()` produces** — spec, output, identity, timing — in one object. The persistence layer splits it into multiple files for ergonomics, but the in-memory shape is one object. The schema lives in `src/market_analyser/backtest/result.py` and is wire-stable: the per-type SSE payload model `RunCompletedPayloadV1` (from [ADR-0017](0017-live-ui-updates-via-sse.md)) and the SQLite `backtest_runs` row are both projections of this object.

```python
# src/market_analyser/backtest/result.py — illustrative; final shape locked in Plan 0008 phase 1.

class EquityPoint(BaseModel):
    ts: datetime  # UTC, bar close time
    equity: float  # cash-equivalent value at this bar's close
    model_config = {"frozen": True, "extra": "forbid"}


class BacktestMetrics(BaseModel):
    total_return: float                # (final_equity / initial_capital) - 1
    sharpe: float                      # annualized; NaN-safe (zero-std → 0.0)
    max_drawdown: float                # peak-to-trough as a negative fraction, e.g. -0.25
    max_drawdown_duration_bars: int    # bars from peak to recovery (or end-of-series if never recovered)
    win_rate: float                    # closed trades only; NaN-safe (zero trades → 0.0)
    trade_count: int                   # closed trades count
    buy_and_hold_return: float         # comparator: (last_close / first_close) - 1
    model_config = {"frozen": True, "extra": "forbid"}


class BacktestResult(BaseModel):
    # --- Identity ---
    run_id: str                        # UUID4, hex; assigned at run start
    engine_version: str                # bumps on any change to engine math; invalidates cached results

    # --- Spec (what was run) ---
    strategy_id: str                   # from strategy module's META.id
    strategy_version: str              # from strategy module's META.version
    symbol: str
    timeframe: str
    range_start: datetime
    range_end: datetime
    bars_hash: str                     # sha256 of canonical bar serialization (see "Data identity" below)
    params: dict[str, Any]             # strategy params, JSON-serializable; validated against strategy.Params at run()
    costs: dict[str, float]            # {"commission_bps": ..., "slippage_bps": ...}; flat-bps model per Plan 0008 scope
    initial_capital: float             # default 10_000.0
    sizing: Literal["fixed_fraction"]  # v1 enum has one value; reserved for future plans

    # --- Timing ---
    started_at: datetime               # UTC, ms precision
    finished_at: datetime              # UTC, ms precision

    # --- Output ---
    trades: list[Trade]                # from Plan 0002; closed + dangling
    equity_curve: list[EquityPoint]    # per-bar mark-to-market; len == len(bars)
    metrics: BacktestMetrics

    model_config = {"frozen": True, "extra": "forbid"}
```

**Persistence layout.** Plan 0008's `persist(result, runs_dir, session)` splits the result into three files under `runs/<run_id>/`:

| File              | Contents                                                                                 |
|-------------------|------------------------------------------------------------------------------------------|
| `spec.json`       | Strategy / data / costs / capital / sizing — everything needed to re-run. **No outputs.** |
| `result.json`     | `BacktestResult` minus `equity_curve` (which lives in its own file for ergonomics).      |
| `equity_curve.csv` | Two columns (`ts`, `equity`), one row per bar. Plain CSV so it diffs cleanly.            |

And one row in the SQLite `backtest_runs` table (Plan 0008 phase 3) keyed by `run_id`, holding the searchable projection: `(run_id, strategy_id, strategy_version, symbol, timeframe, range_start, range_end, total_return, sharpe, max_drawdown, win_rate, trade_count, finished_at, artifact_path)`. The artifact_path is relative to `runs/` — i.e., the directory name — matching the `run.completed v1` envelope's payload exactly.

**Equity curve cadence — per-bar mark-to-market.** Each bar gets one `EquityPoint`. While flat, equity equals cash. While in a long position, equity equals `cash + position_units * bar.close`. This makes max-drawdown calculation meaningful (peak-to-trough during open trades is visible) and keeps the curve density predictable (`len(equity_curve) == len(bars)` always). The cost is a 10k+ entry list for multi-year minute-bar backtests; acceptable for v1 where typical bar counts are in the low thousands.

**`run_id` is UUID4.** Generated at run start. Two re-runs of an identical spec produce two different `run_id`s. We do not collapse identical re-runs into one record; the user owns cache hygiene via the `runs/` directory (which is gitignored).

**Data identity is a hash field, not a proxy.** Every `BacktestResult` carries `bars_hash`, a SHA256 of the canonical serialization of the bar list that fed `run()` (each bar serialized as `{ts (ISO-8601 UTC), open, high, low, close, volume}`, joined newline-delimited, encoded UTF-8). Two backtests with the same spec but different cached bars will have different `bars_hash` values, and the divergence is detectable on inspection. The `as_of` seam from [ADR-0007](0007-market-data-provider.md) is orthogonal — `as_of` controls *what bars get cached*; `bars_hash` records *what bars the backtest actually saw*.

**Engine versioning.** `engine_version` is a string. Bumps on any change to `_apply_costs`, `_calc_metrics`, `_build_equity_curve`, or `_buy_and_hold_return` that changes outputs for identical inputs. Documented in `src/market_analyser/backtest/__init__.py` as a module-level constant. Future cache layers (not in Plan 0008) key on `(spec_hash, engine_version)` — old `engine_version` results are still readable but flagged as stale.

**Metrics shape — nested, not flat.** `BacktestMetrics` is a sub-model. This mirrors the four-helper engine organization from Plan 0002's followup and makes "metrics-only" projections (the agent summary, the SQLite row) explicit. The cost is one extra level of dotted access at consumer sites; the benefit is that adding a metric (e.g. Sortino, Calmar) does not bloat `BacktestResult`'s top level.

**Inline equity curve, split at persistence time.** `BacktestResult` carries `equity_curve` as `list[EquityPoint]` in memory. The persistence layer writes it as a separate CSV file. `GET /backtests/{run_id}` re-merges `result.json` + `equity_curve.csv` into a single `BacktestResult` JSON payload for the UI. This keeps `run()` self-contained (one object returned, callers can use it without a disk round-trip) while letting the on-disk artifacts diff cleanly and stay debuggable.

## Consequences

### Positive

- **Determinism is mechanically checkable.** Re-running `run()` with the same `(strategy, bars, params, costs, initial_capital)` produces a `BacktestResult` byte-identical to the prior run after stripping `run_id`, `started_at`, `finished_at`. Plan 0008's phase 2 golden test asserts exactly this.
- **One in-memory object, three disk artifacts, one SQLite row.** Each consumer takes the projection it needs. The UI gets the merged JSON via one HTTP call. The agent gets the metrics summary directly. SQLite queries hit only the row.
- **`bars_hash` makes silent data drift loud.** A user who refreshes their Yahoo cache mid-experiment and re-runs the same spec sees a different `bars_hash` and knows the inputs changed. Without this field the drift is invisible.
- **`engine_version` makes math changes loud.** Bumping the constant is part of any PR that changes engine output, surfaced in the result itself. Future maintainers see exactly which engine version produced any given result row.
- **Wire-compatible with [ADR-0017](0017-live-ui-updates-via-sse.md).** `run.completed v1` carries `run_id` + `artifact_path` (relative to `runs/`); both are present on `BacktestResult` and on the SQLite row, with no translation layer needed.
- **Schema evolution is local.** Adding a metric is one field on `BacktestMetrics`. Adding a sizing model is extending the `sizing` Literal. Both are additive and don't break existing `result.json` files (Pydantic permits extra reads in non-strict modes; the model's `extra="forbid"` applies at *write*, not at *read* of older snapshots — Plan 0008 phase 1 captures the read-side compatibility test).

### Negative

- **Per-bar equity curves are memory-hungry at long horizons.** A 10-year minute-bar backtest is ~3.6M entries × 16 bytes each ≈ 60 MB in memory per result. Acceptable for v1 (we backtest at most a few thousand bars per run); flagged here so the limit is on the record.
- **`engine_version` is honor-system.** Nothing forces a contributor to bump it when they change `_calc_metrics`. The mitigation is a Plan 0008 done-when (the engine_version constant lives next to a docstring saying "bump on any output-affecting change") and Mode 4 review attention; a more rigorous mechanism (e.g. a CI check that diffs golden outputs across PRs) is a future plan.
- **`bars_hash` requires canonical serialization.** Two bar lists with the same content but different float formatting would hash differently. Plan 0008 phase 1 pins the serialization (ISO-8601 UTC `ts`, no fractional seconds, floats serialized at full Python `repr` precision) and asserts the hash is stable across two calls on the same bar list.
- **The `params: dict[str, Any]` typing is loose.** Validation happens at the strategy boundary (`strategy.Params(**params)` raises on a bad shape); the result schema itself can't enforce strategy-specific param types. We accept this — the alternative is a generic-typed `BacktestResult[ParamsT]` which adds typing ceremony for no runtime benefit on the persistence path.
- **`sizing: Literal["fixed_fraction"]` is a one-valued enum at v1.** The field is reserved for the moment fixed-notional or risk-targeted sizing lands (likely a sweeps-or-walkforward plan). Until then, the field is dead weight in every row — accepted because adding the field retroactively to historical results is more painful than carrying one literal forward.

### Neutral

- **The schema is mode-agnostic.** A single-run `run()` and a future `run_sweep()` both produce `BacktestResult` objects — sweeps just produce many. No sweep-specific shape is reserved.
- **The schema crosses the agent/UI/SQLite boundary.** Each consumer gets a different subset; the canonical name (`BacktestResult`) is the in-memory one. The SSE envelope's payload (`RunCompletedPayloadV1`) and the SQLite row are projections, not separate schemas — they reference the same underlying types.

## Alternatives considered

### Alternative A — Flat metrics on `BacktestResult`

Inline every metric (`total_return`, `sharpe`, …) directly on the top-level model with no nested `BacktestMetrics`.

Rejected because the four-helper engine organization in Plan 0002's followup is the explicit module shape, and a flat schema makes "metrics-only" projections (the agent's summary return, the SQLite row's metric columns) implicit instead of explicit. Adding a future metric to a flat shape bloats the top level; adding it to `BacktestMetrics` keeps the top level stable. One level of dotted access is a cheap price.

### Alternative B — Reference-only equity curve

`BacktestResult` carries `equity_curve_path: str` (the file path under `runs/`) instead of an in-memory list. `run()` writes the CSV during execution and embeds the path.

Rejected because it forces `run()` to do I/O — exactly the layering violation the Mode 1 architecture choice ("Pure core + thin persistence layer") rejected. A pure `run()` can be unit-tested without touching disk; a `run()` that writes intermediate files cannot. The 60 MB ceiling on per-bar curves is high enough that v1 workloads never approach it.

### Alternative C — Content-addressed `run_id` (hash of `spec.json`)

Re-runs of identical specs share a `run_id`; the persistence layer can short-circuit when the artifact already exists.

Rejected because content-addressing tangles caching with identity. Distinguishing "the user re-ran on purpose to validate determinism" from "the user re-ran by accident" requires inspecting timestamps anyway; an explicit cache-key field (`spec_hash`) on the result would serve cache lookups without conflating them with identity. We may add `spec_hash` in a future caching plan; for now `run_id = UUID4` is unambiguous, fresh, and cheap.

### Alternative D — No `bars_hash`; trust `(symbol, timeframe, range)` as identity

The spec triple uniquely identifies a backtest's data input — adding a hash duplicates that.

Rejected because the `as_of` seam ([ADR-0007](0007-market-data-provider.md)) explicitly contemplates the cache changing under the same triple. A backtest run against bars-as-of-yesterday and the same spec run against bars-as-of-today produce different results that the spec triple cannot distinguish. `bars_hash` is the smallest field that closes the gap.

### Alternative E — Single monolithic `result.json` (no equity_curve.csv split)

Everything (spec, output, equity curve) goes in one JSON file under `runs/<run_id>/`.

Rejected because the equity curve is the one part of the result that diffs cleanly as CSV and badly as JSON. Splitting it out costs one file per run; the benefit is that `git diff runs/<run_id>/equity_curve.csv` (in the rare case a user commits a `runs/` artifact to a local branch) is readable. `spec.json` and `result.json` are smaller and JSON-shaped naturally.

### Alternative F — Persist via SQLite only; no on-disk artifacts

Skip `runs/<run_id>/`; store everything (spec, result, equity curve) as BLOBs in the `backtest_runs` table.

Rejected because the human-debuggability cost is large (no `cat result.json | jq .metrics`), and the `run.completed v1` envelope's `artifact_path` is wire-frozen by [ADR-0017](0017-live-ui-updates-via-sse.md). Removing the path would force the envelope's revision, which is out of scope here.

## Notes

- Plan 0008 phase 1 pins the canonical bar serialization for `bars_hash` and asserts hash stability across two calls. The exact format is "for each bar in input order, write `f'{ts.isoformat()}|{open!r}|{high!r}|{low!r}|{close!r}|{volume!r}'` joined by `\n`, encode UTF-8, SHA256". `ts.isoformat()` is deterministic for `datetime` instances with UTC tzinfo; `repr(float)` is deterministic across Python 3.10+ per PEP 3101. The test asserts that two `bars_hash` calls on the same bar list return the same string, and that re-serializing the same content via a different DataFrame source produces the same hash.
- Sharpe annualization uses a per-timeframe bars-per-year mapping (`1d → 252`, `1h → 252*24 ≈ 6048`, others as added). The mapping lives in `src/market_analyser/backtest/metrics.py` as a module constant and is enumerated as part of `_calc_metrics`'s docstring. Unknown timeframes raise; the engine does not silently guess.
- Future plans likely to touch this schema: sweeps (extends with a `sweep_id` correlation field), walk-forward (adds per-fold sub-results), shorts (changes `sizing` enum and adds margin fields to `BacktestMetrics`). Each is a separate ADR + plan; this one stays scoped to v1.
