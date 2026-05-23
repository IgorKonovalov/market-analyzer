# Project context — backtester

This is the backtester-specific lens on the `market-analyser` project. The architect skill has its own broader context file; this one focuses on what you need to run backtests and write engine code.

## Where things live

```
src/market_analyser/
├── contracts/
│   └── strategy.py             # Bar, Signal, SignalKind, BaseParams, StrategyMeta, StrategyProtocol, discover()
├── strategies/                 # You CONSUME these. You do NOT write here.
│   └── <slug>.py
├── data/
│   └── types.py                # Bar lives here (Plan 0001 phase 2)
├── analysis/
│   └── indicators.py           # consumed by strategies, not by you directly (in-house per ADR-0009)
└── backtest/                   # YOUR territory
    ├── __init__.py
    ├── engine.py               # run(strategy, bars, params, costs) -> BacktestResult
    ├── adapter.py              # signals_to_trades(bars, signals, costs) -> list[trade_dict]
    ├── metrics.py              # Sharpe, Sortino, drawdown, win rate, etc.
    ├── equity.py               # equity-curve construction
    ├── costs.py                # BacktestCosts model + cost application
    └── result.py               # BacktestResult model

runs/                           # gitignored run artifacts
├── <strategy-id>/
│   └── <UTC-timestamp>-<short-hash>/
│       ├── result.json
│       ├── report.md
│       ├── equity_curve.png
│       └── spec.json
└── _comparisons/
    └── <UTC-timestamp>-<comparison-slug>/
        ├── comparison.json
        ├── comparison.md
        └── equity_overlay.png

tests/
├── backtest/
│   └── test_*.py               # engine, adapter, metrics, costs tests
└── fixtures/
    ├── btc-1h-2024.csv         # deterministic test bars
    └── ...
```

If `src/market_analyser/backtest/` doesn't exist yet, the engine hasn't been built. Plan 0002 phase 3 is the phase that creates it. Until that lands, the only thing you can do is implement-mode (Mode 4 in SKILL.md). Run-mode, compare-mode, and report-mode all require the engine to exist.

## What you import

Engine code typically imports:

```python
from collections.abc import Sequence

from pydantic import BaseModel, Field

from market_analyser.contracts import (
    Bar,
    BaseParams,
    Signal,
    SignalKind,
    StrategyMeta,
    StrategyProtocol,
    discover,
)
from market_analyser.backtest.costs import BacktestCosts
from market_analyser.backtest.result import BacktestResult, TradeRecord, EquityPoint
```

Run-mode scripts may also import `pandas` (for CSV loading) and `matplotlib` (for `equity_curve.png`). Neither is allowed inside engine code itself — engine input is `list[Bar]` and engine output is `BacktestResult`, both pure-Python pydantic. Pandas/matplotlib only at the I/O edges.

## Sibling skills (so you know what's yours vs theirs)

- **architect** — owns the contracts, ADRs, plans. If you find yourself wanting to change the `BacktestResult` shape, the cost model schema, or anything that another skill imports, route to architect first. Don't invent new shapes mid-implementation.
- **strategy-author** — produces strategy modules. You consume their output. If a strategy is missing or broken, tell the user — don't try to fix it yourself.
- **dev** — the generalist implementer. You overlap with dev only on backtest-engine plans (or specific phases within them), where you are the more-specialized choice; everything else stays with dev.
- **ui-builder** (planned) — reads `result.json` and renders dashboards. You produce the JSON; you don't render it in HTML.

## The `BacktestResult` shape (until the ADR pins it)

There is **no accepted ADR for `BacktestResult` yet** — it's listed as open ADR-#4 in the architect's backlog. Until that lands, treat the shape in `result-schema.md` as the working draft: a pydantic model with `params`, `costs`, `metrics`, `trades`, `equity`, `meta` (strategy_id, version, bar source, ran_at, run_hash). When the ADR lands, the shape becomes authoritative and `result-schema.md` will be retired.

If you make a change to the shape during implementation, **update `result-schema.md` in the same commit** so the next session reads the truth, and flag it to the user so they can decide whether to ask architect to formalize.

## The execution-timing convention (worth repeating)

A `Signal` at `bar_index = i` is interpreted as "the strategy decided at the close of bar i; the engine executes at the open of bar i+1". This is not negotiable — it's the only way to keep strategies lookahead-safe without forcing them to know engine internals.

Consequences:
- Signals at `i = len(bars) - 1` have no `i+1` to execute on. They are **dropped** (with `result.meta.dropped_terminal_signals += 1`), not executed at `bars[-1].close`.
- The price the trade fills at is `bars[i+1].open`, not `bars[i].close`. The slippage model is applied on top.
- `metrics.last_signal_bar_index` may be > the last trade's `bar_index` — that's fine, that just means the final signal was dropped.

## Cost model (until the ADR pins it)

Working shape (`backtest/costs.py`):

```python
class BacktestCosts(BaseModel):
    commission_bps: float = Field(default=5.0, ge=0, le=100, description="Per-side commission in basis points")
    slippage_bps:   float = Field(default=5.0, ge=0, le=500, description="Per-side slippage in basis points")
    initial_cash:   float = Field(default=10_000.0, gt=0)
    model_config = {"frozen": True}
```

Costs apply on both entry and exit (one application per side, not summed once). The architect plan for the engine pins the exact semantics; the cost model is symmetric until/unless an ADR changes them.

## Current state (as of 2026-05-17)

- `docs/architecture/` exists with the relevant ADRs (0004 strategy interface) and plan (0002 strategy interface, phase 3 is yours).
- **No code has been written yet.** The bootstrap plan (`plans/0001-bootstrap.md`) is in `draft` status; until it's implemented, there's no `src/market_analyser/` at all.
- The backtest engine is written in-house per ADR-0009; Plan 0002's phase 3 (which originally lifted helpers from upstream) is parked pending an architect Mode 4 pass to re-plan against an in-house implementation. Until that re-plan lands, treat the engine as not-yet-designed.

When asked to run a backtest in this state, **block on the engine**: say "the backtest engine isn't built yet — Plan 0002 phase 3 needs to be re-planned post-ADR-0009. Want me to route to architect for the re-plan, or wait?" Don't fake a run against an engine that doesn't exist.
