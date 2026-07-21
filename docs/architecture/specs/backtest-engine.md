# Spec — Backtest engine

> **Subsystem:** The pure backtest orchestrator — turns `(strategy, bars, params, costs, capital)` into a frozen `BacktestResult` with no I/O, no DB, no event bus.
> **Source:** src/market_analyser/backtest/ (`engine.py`, `adapter.py`, `metrics.py`, `result.py`, `_bars_hash.py`, `_version.py`)
> **Reconciled-through:** Plan 0112
> **Governing ADRs:** 0018-backtest-result-schema, 0004-strategy-interface, 0007-market-data-provider, 0050-short-selling-strategy-backtest

This is the repo's most safety-critical behavioral contract. The three claims
below — re-run determinism, no lookahead, and no hidden non-determinism in the
financially-meaningful path — are the cross-cutting non-negotiables from
[`CLAUDE.md`](../../../CLAUDE.md) as they land in the backtest engine specifically.
A reader should be able to restate the determinism contract from this file alone.

## Invariants

- **Re-run byte-identity modulo run provenance.** The engine MUST produce, for
  identical inputs `(strategy_module, bars, params, timeframe, commission_bps,
  slippage_bps, initial_capital)`, a `BacktestResult` whose
  `model_dump(mode="json")` is equal element-by-element **after excluding exactly
  three provenance fields — `run_id`, `started_at`, `finished_at`**. Those three
  are the *only* documented sources of variation: `run_id` is a fresh `uuid4().hex`
  per call and the two timestamps are wall-clock reads. Everything else — trades,
  equity curve, metrics, `bars_hash`, `engine_version` — is a deterministic
  function of the inputs.  (ADR-0018; `backtest/engine.py:run`, `backtest/_version.py`)

- **Cross-process / cross-machine identity, not just in-process.** The engine MUST
  reproduce the committed golden fixture
  (`tests/fixtures/backtest/rsi_default_expected.json`) byte-for-byte from the
  same inputs, on any machine, in a fresh process. Any change that alters engine
  output requires a deliberate `ENGINE_VERSION` bump *and* a golden-fixture regen —
  the fixture is the second line of defence behind the honor-system version
  constant.  (ADR-0018; `tests/backtest/test_engine_golden.py`, `backtest/_version.py:ENGINE_VERSION`)

- **No lookahead: a decision at bar `i` executes at bar `i + 1`.** A strategy
  signal emitted at `bar_index = i` MUST fill at `bars[i + 1].open` — the decision
  is taken at the close of bar `i`, executed at the open of the *next* bar. The
  engine MUST NOT let a signal reference or fill against any price at index `≤ i`
  for its own execution, and MUST silently drop any signal whose executable bar
  `i + 1` is past the end of the series (`bar_index ≥ len(bars) - 1`) — there is no
  future open to fill against.  (ADR-0004 execution-timing convention; `backtest/adapter.py:signals_to_trades`)

- **Deterministic same-bar ordering.** WHEN multiple signals reference the same
  bar, the engine MUST process **exits before entries**, via a stable sort on
  `(bar_index, is_entry)`, regardless of the order the strategy emitted them. A
  deterministic strategy output therefore yields a deterministic trade list — the
  adapter introduces no order-dependence of its own.  (ADR-0050 same-bar rule; `backtest/adapter.py:_ordering_key`)

- **No hidden non-determinism in the financially-meaningful path.** Between reading
  the inputs and returning the result, the engine MUST NOT: iterate over a `set` or
  any unordered collection in a way that affects output; read the wall clock for
  anything other than the two provenance timestamps; or use unseeded randomness for
  anything other than the `uuid4` `run_id`. `run()` is a pure function of its
  arguments modulo those two named escapes.  (CLAUDE.md determinism rule; ADR-0018; `backtest/engine.py`)

- **Data identity is recorded, not assumed.** Every `BacktestResult` MUST carry
  `bars_hash` — the SHA256 of the canonical serialization of the exact bar list
  that fed `run()` (each bar as
  `f"{event_ts.isoformat()}|{open!r}|{high!r}|{low!r}|{close!r}|{volume!r}"`, joined
  by `\n`, UTF-8). Two runs of the same spec against different cached bars produce
  different `bars_hash` values, making silent data drift visible on inspection.
  (ADR-0018; `backtest/_bars_hash.py:bars_hash`)

- **Purity: no I/O in the core.** `run()` MUST NOT touch the disk, the database,
  the SSE bus, or the network. It returns one frozen `BacktestResult` object; the
  persistence layer (`backtest/persistence.py`) is a separate, thin step that
  splits that object into `spec.json` / `result.json` / `equity_curve.csv`. This is
  what makes the engine unit-testable without a filesystem.  (ADR-0018 rejected Alternative B; `backtest/engine.py`)

- **Input validation at the engine boundary.** The engine MUST reject a
  non-contract-shaped strategy module (missing `META` / `Params` /
  `generate_signals`) with `StrategyContractError`, and MUST reject an empty bar
  list with `ValueError`, rather than producing a degenerate result.  (`backtest/engine.py:_validate_strategy_module`, `backtest/engine.py:run`)

## Scenarios

- WHEN `run()` is called twice in the same process with identical inputs THEN the
  two results' `model_dump(mode="json")` dicts are equal after popping `run_id`,
  `started_at`, `finished_at`.  (`test_engine_golden.py::test_in_process_determinism`)

- WHEN `run()` is called on the AAPL/1d/200-bar fixture with the default RSI params
  in a fresh process THEN the provenance-stripped dump equals the committed
  `rsi_default_expected.json` field-for-field.  (`test_engine_golden.py::test_matches_committed_golden_fixture`)

- WHEN a strategy emits `ENTER_LONG` at bar index `i` THEN the resulting trade's
  entry price is `bars[i + 1].open`, never `bars[i].close` or any earlier price.
  (`backtest/adapter.py`; `tests/backtest/test_adapter_*.py`)

- WHEN a strategy emits a signal at `bar_index = len(bars) - 1` (the last bar) THEN
  that signal is dropped — there is no bar `i + 1` to fill against — and it
  contributes no trade.  (`backtest/adapter.py:signals_to_trades`)

- WHEN a long-exit and a short-entry reference the same bar THEN the exit fills
  first and the entry second, both at that bar's next open, with the book flat in
  between — independent of the order the strategy listed them.  (ADR-0050; `backtest/adapter.py`)

- WHEN two runs use the same spec but the underlying cached bars changed between
  them THEN their `bars_hash` values differ, so the divergence is detectable
  without re-reading the bars.  (ADR-0018; `backtest/_bars_hash.py`)

- WHEN the four engine helpers (`_apply_costs`, `_build_equity_curve`,
  `_calc_metrics`, `_buy_and_hold_return`) or `run()`'s composition order change in
  a way that alters output THEN `ENGINE_VERSION` must be bumped and the golden
  fixture regenerated, or the golden test fails.  (`backtest/_version.py`; `test_engine_golden.py`)

- WHEN `run()` is handed a module missing `generate_signals` (or an empty bar list)
  THEN it raises `StrategyContractError` (respectively `ValueError`) instead of
  returning a result.  (`backtest/engine.py`)

## Known gaps / honest nulls

- **`ENGINE_VERSION` is honor-system.** Nothing forces a contributor to bump the
  constant when they change engine math; the golden fixture is the backstop (an
  un-bumped output change reddens the golden test), but a contributor who regens the
  fixture *without* bumping the version defeats both. Accepted per ADR-0018; a CI
  diff of golden outputs across PRs is a future plan, not a current guarantee.

- **`params: dict[str, Any]` is loosely typed in the result.** Strategy-specific
  param types are validated at the strategy boundary (`strategy.Params(**params)`
  raises on a bad shape), not by `BacktestResult` itself. The result schema cannot
  enforce per-strategy param types.  (ADR-0018)

- **Per-bar equity curves are memory-hungry at long horizons.** `equity_curve` is
  one `EquityPoint` per bar (`len(equity_curve) == len(bars)`), so a multi-year
  minute-bar run is millions of entries. Acceptable for v1 (runs are low-thousands
  of bars); flagged, not fixed.  (ADR-0018)

- **Determinism is guaranteed only for the financially-meaningful path.** `run_id`,
  `started_at`, and `finished_at` vary by construction and are excluded from every
  equality assertion; downstream consumers must not treat them as reproducible.
