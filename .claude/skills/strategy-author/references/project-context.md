# Project context — strategy-author

This is the strategy-author-specific lens on the `market-analyser` project. The architect skill has its own broader context file; this one focuses on what you need to write strategies.

## Where strategies live

```
src/market_analyser/
├── contracts/
│   └── strategy.py           # Bar, Signal, StrategyMeta, StrategyProtocol — IMPORT FROM HERE
├── strategies/               # One file per strategy: rsi_bounds.py, macd_cross.py, ...
│   └── <slug>.py
├── analysis/
│   └── indicators.py         # calc_rsi, calc_bollinger, calc_macd, calc_ema, calc_supertrend, calc_donchian (in-house per ADR-0009)
└── backtest/                 # The backtester. You do NOT write here.

tests/
└── strategies/
    └── test_<slug>.py        # One pytest file per strategy
```

If `contracts/strategy.py` doesn't exist yet, the strategy-interface plan (`plans/0002-strategy-interface.md`) hasn't been implemented yet. `BaseParams`, `Signal`, `SignalKind`, `StrategyMeta`, and `StrategyProtocol` all land there. The bootstrap plan (`plans/0001-bootstrap.md`) ships the canonical `Bar` model in `src/market_analyser/data/types.py`; if that file is missing, Plan 0001 phase 2 hasn't landed either. **Do not stub out the contract types from your own memory** — the contract is the architect's responsibility, not yours. Surface the gap and stop.

## What you import

For every strategy, the imports look approximately like this:

```python
from collections.abc import Sequence

from pydantic import BaseModel, Field

from market_analyser.contracts import BaseParams, Bar, Signal, SignalKind, StrategyMeta
# Indicators — pick whichever the strategy needs:
from market_analyser.analysis.indicators import (
    calc_rsi,
    calc_bollinger,
    calc_macd,
    calc_ema,
    calc_supertrend,
    calc_donchian,
)
```

The indicator module is written in-house per ADR-0009. **Verify the file exists before writing imports** — the indicator plan may not have shipped yet; if it hasn't, surface the gap rather than fabricating an import.

## Sibling skills (so you know what's yours vs theirs)

- **architect** — owns the contract and the file layout. If you find yourself wanting to change either, route to architect, don't change them yourself.
- **backtester** — consumes your signals and produces trades, P&L, equity curve, Sharpe. Owns `src/market_analyser/backtest/`. You produce signals; the backtester decides what to do with them.
- **ui-builder** — auto-renders `Params.model_json_schema()` into a form. You don't need to write any UI code for parameters as long as your `Params` model has good `Field(...)` constraints and field descriptions.

## The Bar and Signal shapes (as of writing)

Read `src/market_analyser/data/types.py` (for `Bar`) and `src/market_analyser/contracts/strategy.py` (for everything else) for the authoritative versions. For grounding:

- A **`Bar`** lives in `data/types.py` (Plan 0001 phase 2) and is re-exported from `market_analyser.contracts` for ergonomic imports. Shape: `symbol: str`, `timeframe: str`, `event_ts: datetime` (UTC-aware), `open/high/low/close: float`, `volume: float` (≥ 0), `source: str`.
- A **`Signal`** lives in `contracts/strategy.py` (Plan 0002 phase 1). Shape: `bar_index: int` (the index into the bars list where the signal fires), `kind: SignalKind` (`ENTER_LONG` / `EXIT_LONG` / `ENTER_SHORT` / `EXIT_SHORT` — shorts are fully supported since Plan 0053 / ADR-0050), `reason: str | None`.
- The strategy parameter base class is **`BaseParams`** — not `Params` — so your strategy can declare `class Params(BaseParams):` without name shadowing.


## Current state (as of 2026-07-03 — verify with `Glob`/`Read`; trust the tree over this section)

- **The contract and its consumers are fully shipped.** `contracts/strategy.py` (Signal, SignalKind incl. shorts, BaseParams, META, `discover()`), `analysis/` (indicators, candlestick + chart patterns, levels, volume — ADR-0023), and the backtest engine (flat/long/short, ADR-0050) all exist. Strategies run for real, live (`evaluate_signals`) and historically (`run_backtest`).
- **Eight strategies live in `src/market_analyser/strategies/`**: `rsi`, `rsi_stop`, `bollinger`, `macd`, `ema_cross`, `supertrend`, `donchian`, `chart_pattern_breakout`. Each has a pytest smoke test; `tests/test_cli.py` pins the roster count — registering a new strategy means bumping that assertion in the same commit.
- Write new strategies directly against the real contract; no bootstrap-friendly mode is needed anymore. A no-lookahead truncation-invariance test (re-run on every bars prefix) is the house pattern for new modules.
