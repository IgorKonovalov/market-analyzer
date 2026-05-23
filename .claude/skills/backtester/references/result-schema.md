# BacktestResult — working schema

This is the **ad-hoc** shape we're using until an architect-approved ADR codifies it. When that ADR lands, this file is retired and the ADR becomes the source of truth.

## Why this exists separately from the SKILL.md

The `BacktestResult` shape is consumed by `ui-builder` and by anyone reading `result.json` from a `runs/` directory. Changing it after the fact breaks downstream readers. Pinning it down — even informally — beats letting each run invent its own keys.

If you change the shape during implementation, update this file in the same commit and flag to the user that the change happened so they can decide whether to escalate to architect.

## The shape

```python
# src/market_analyser/backtest/result.py
from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, Field


class TradeRecord(BaseModel):
    entry_bar_index: int
    exit_bar_index:  int | None              # None if the trade is still open at end of bars
    entry_ts:        datetime                # UTC-aware
    exit_ts:         datetime | None
    entry_price:     float                   # fill price (incl. slippage)
    exit_price:      float | None
    quantity:        float                   # contracts/shares — full-balance sizing in v1
    pnl_gross:       float                   # before costs
    pnl_costs:       float                   # commission + slippage in $
    pnl_net:         float                   # pnl_gross - pnl_costs
    pnl_pct:         float                   # pnl_net / cost_basis
    entry_reason:    str | None              # passed through from Signal.reason
    exit_reason:     str | None
    model_config = {"frozen": True}


class EquityPoint(BaseModel):
    bar_index: int
    ts:        datetime
    equity:    float                         # cash + open position MTM
    drawdown:  float                         # equity / running_max_equity - 1; <= 0
    model_config = {"frozen": True}


class BacktestMetrics(BaseModel):
    total_return_pct:   float                # (final_equity / initial_cash) - 1
    cagr:               float                # annualized; if span < 30 days, NaN
    sharpe:             float                # annualized; NaN if stddev == 0
    sortino:            float                # annualized; NaN if downside_stddev == 0
    max_drawdown_pct:   float                # <= 0
    max_drawdown_duration_bars: int
    n_trades:           int
    n_wins:             int
    n_losses:           int
    win_rate:           float                # n_wins / max(1, n_trades)
    avg_win_pct:        float
    avg_loss_pct:       float
    profit_factor:      float                # sum(wins) / abs(sum(losses)); NaN if no losses
    buy_and_hold_return_pct: float           # benchmark
    model_config = {"frozen": True}


class BacktestMeta(BaseModel):
    strategy_id:       str
    strategy_version:  str
    bars_source:       str                   # e.g. "tests/fixtures/btc-1h-2024.csv" or "yfinance:AAPL:1d:2020-01-01:2024-12-31"
    bars_count:        int
    bars_first_ts:     datetime
    bars_last_ts:      datetime
    ran_at:            datetime
    run_hash:          str                   # first 7 chars; see SKILL.md
    dropped_terminal_signals: int = 0
    notes:             list[str] = []        # warnings/anomalies surfaced during the run
    model_config = {"frozen": True}


class BacktestResult(BaseModel):
    params:  dict                            # the strategy's Params.model_dump()
    costs:   dict                            # BacktestCosts.model_dump()
    metrics: BacktestMetrics
    trades:  list[TradeRecord]
    equity:  list[EquityPoint]
    meta:    BacktestMeta
    model_config = {"frozen": True}
```

## JSON serialization

`BacktestResult.model_dump_json(indent=2)` is the canonical serialization. Keys are emitted in declaration order so diffs between two `result.json` files are clean. Datetimes are ISO 8601 with `Z` suffix.

## Reconciliation invariant

`equity[-1].equity` must equal `costs.initial_cash + sum(t.pnl_net for t in trades if t.exit_bar_index is not None) + (mtm of any open trade at last bar)`. Engine should assert this internally and surface a `notes` entry if it ever fails to within floating-point tolerance.

## What's deliberately not here

- **No fees-by-asset-class table.** Just flat bps. Add if the ADR ever lands a per-asset model.
- **No multi-asset position tracking.** v1 is one strategy ⇒ one asset; multi-leg or portfolio backtests are a separate ADR.
- **No partial fills, no order book.** Execution is "fill at next bar's open, plus slippage_bps". Anything more realistic is its own ADR.
- **No risk metrics beyond drawdown.** No VaR, no expected shortfall, no rolling Sharpe. Add when there's a use case.

## When the ADR lands

The ADR will pin field names, types, and the contract that downstream consumers can rely on. At that point:

1. The ADR replaces this file (retire to `references/_retired/`).
2. The model in `src/market_analyser/backtest/result.py` becomes the canonical Python representation.
3. Any `result.json` written before the ADR is grandfathered — convert on read if the shapes differ.
