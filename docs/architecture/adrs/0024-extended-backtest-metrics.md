# ADR-0024 — Extended backtest metrics: definitions and degenerate-value convention

> **Status:** accepted (2026-06-03, at [Plan 0020](../plans/done/0020-backtest-metrics-walk-forward.md) close — see the close note appended below)
> **Date:** 2026-05-24
> **Related plan(s):** [0020-backtest-metrics-walk-forward](../plans/0020-backtest-metrics-walk-forward.md)
> **Extends:** [ADR-0018](0018-backtest-result-schema.md) (backtest result schema)

## Context

`BacktestMetrics` ([ADR-0018](0018-backtest-result-schema.md), `backtest/result.py`) ships seven fields: `total_return`, `sharpe`, `max_drawdown`, `max_drawdown_duration_bars`, `win_rate`, `trade_count`, `buy_and_hold_return`. A richer institutional metric set — Calmar ratio, Sortino ratio, profit factor, expectancy, and best/worst trade — is what traders use to judge a strategy beyond raw Sharpe and drawdown, and we compute none of it today.

ADR-0018 already anticipates schema growth: "adding fields means appending to the end of their group," and the determinism contract pins `model_dump(exclude={"run_id", "started_at", "finished_at"})` equality across processes. So *adding* metrics is governed; this ADR is not a schema-policy reversal. What it pins is the part that is a genuine decision: **the exact formula for each new metric and how each behaves on degenerate input.** Without a written definition, two implementations (or a future re-derivation) can disagree on annualization basis, downside-deviation MAR, or zero-loss handling — and that silently breaks the cross-run determinism the engine guarantees.

A second decision: the existing metrics collapse degenerate cases to `0.0` (flat equity → Sharpe `0.0`; zero closed trades → win-rate `0.0`). For the new ratio metrics that convention is actively misleading — a profit factor of `0.0` means "all trades lost," which is not the same as "no losing trades to divide by." We need an explicit, different convention for the ratios.

## Decision

We will extend `BacktestMetrics` with six fields, appended to the model in this order: `calmar: float | None`, `sortino: float`, `profit_factor: float | None`, `expectancy: float | None`, `best_trade_return: float | None`, `worst_trade_return: float | None`. Definitions:

- **Sortino** — `mean(per_bar_returns) / downside_deviation * sqrt(bars_per_year[timeframe])`, where `downside_deviation = stdev({min(r, 0) for r in returns})` (target/MAR = 0, sample stdev ddof=1). Same annualization basis as the existing Sharpe (`_TIMEFRAME_BARS_PER_YEAR`). When there is no downside (no negative bar return) or fewer than two returns, `sortino = 0.0` — consistent with Sharpe's flat-curve collapse, because Sortino is the same family of metric.
- **Calmar** — `annualized_total_return / abs(max_drawdown)`. `annualized_total_return = (1 + total_return) ** (bars_per_year / n_bars) - 1`. When `max_drawdown == 0.0` (curve never dipped), Calmar is **`None`** (undefined — division by zero), not `0.0`.
- **Profit factor** — `gross_profit / gross_loss` over closed trades, where gross profit/loss are summed per-trade returns (positive vs negative). When `gross_loss == 0` (no losing trade) **or** `trade_count == 0`, profit factor is **`None`** (undefined), never `inf` (not JSON-representable) and never `0.0` (misleading).
- **Expectancy** — `mean(per_closed_trade_return)` (the average fractional return per closed trade). When `trade_count == 0`, expectancy is **`None`**.
- **Best / worst trade return** — `max` / `min` of per-closed-trade fractional returns. When `trade_count == 0`, both are **`None`**.

The degenerate-value rule: **ratio and per-trade metrics that are genuinely undefined are `None`, not `0.0`.** This deliberately differs from the original Sharpe/win-rate `0.0`-collapse, because for these metrics `0.0` carries a distinct, wrong meaning. The two Sharpe-family fields (`sharpe`, `sortino`) keep the `0.0` collapse; everything else uses `None`.

Landing this bumps `ENGINE_VERSION` and regenerates the Plan 0008 golden fixture. The determinism contract is preserved: same inputs still produce the same dump (modulo the documented run-provenance exceptions).

## Consequences

### Positive
- Strategy comparison (Plan 0020's leaderboard) can rank on Calmar/Sortino/profit factor, not just total return or Sharpe.
- Every metric's formula and edge-case behavior is pinned in one place, so a re-derivation cannot silently diverge and break determinism.
- `None` for undefined ratios is honest — the renderer and agent can render "—" instead of a fake `0.0`.

### Negative
- **`float | None` fields complicate every consumer.** The renderer's metrics panel, the `BacktestRunSummary` projection, and the agent's reply formatting must all handle `None`. We accept this as the price of not lying with `0.0`.
- **`ENGINE_VERSION` bump invalidates cached/persisted results' version stamp.** Old `result.json` artifacts predate the new fields; readers must tolerate their absence. This is the normal cost of schema growth under ADR-0018.

### Neutral
- The annualization basis is inherited from the existing Sharpe path; if a future ADR revisits annualization (e.g. calendar-day vs trading-day), it revisits Sharpe, Sortino, and Calmar together.

## Alternatives considered

### Alternative A — Collapse undefined ratios to `0.0` like the existing metrics
Rejected. `0.0` is a meaningful value for a profit factor ("all losses") and for Calmar; collapsing undefined-due-to-no-data to `0.0` makes a strategy with zero losing trades indistinguishable from one that only loses. `None` is the honest representation.

### Alternative B — A separate `ExtendedMetrics` model instead of extending `BacktestMetrics`
Rejected. It would split the metrics across two objects for no benefit, complicate the `BacktestResult` envelope, and the additive-append provision of ADR-0018 already covers in-place growth cleanly.

### Alternative C — No ADR; just add the fields under ADR-0018's append provision
Rejected. The append *mechanism* is covered by ADR-0018, but the *formulas and degenerate-value convention* are decisions with real alternatives (MAR choice, zero-loss handling, `None` vs `0.0`) that determinism depends on. Those belong in a written record.

## Notes
- Walk-forward (Plan 0020) reports these same metrics per fold; pinning the definitions here means per-fold and full-run numbers are computed identically.

## Close note — confirmed at Plan 0020 close (2026-06-03)

Implemented exactly as decided; no formula or degenerate-value divergence. One implementation refinement this ADR did not specify, recorded for auditability:

- **Field defaults.** The Decision lists the six fields by type (`calmar: float | None`, …) without defaults. As built, each field carries a **default equal to its own degenerate value** (`calmar/profit_factor/expectancy/best_trade_return/worst_trade_return = None`, `sortino = 0.0`) so a hand-built `BacktestMetrics` (test fixtures, the persistence/route schemas) stays constructible without enumerating all thirteen fields. This does **not** weaken the convention: the engine's `_calc_metrics` always sets every field explicitly, and the defaults equal the honest degenerate value, so no partial construction can misreport. Confirmed acceptable at close.
- **Annualization basis.** `_TIMEFRAME_BARS_PER_YEAR` covers `1d`/`1h`/`1m`; Sortino and Calmar inherit it from Sharpe as the Decision intended. Timeframes added since (Plan 0025's `15m`/`4h`/`1w`) are not yet in this table, so the Plan 0020 tools constrain `timeframe` to the supported three at the MCP boundary — a known, honest limitation, not a determinism gap.
