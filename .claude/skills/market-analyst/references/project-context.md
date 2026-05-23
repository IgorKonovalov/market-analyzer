# Project context — market-analyst

The market-analyst-specific view of the `market-analyser` project. For architecture, ADR rationale, and the running ADR list, see `.claude/skills/architect/references/project-context.md` — the architect's view is the source of truth.

## Repo root

```
<repo-root>/market-analyser
```

## What you read from and write to

```
docs/architecture/
├── adrs/0007-market-data-provider.md     # YOU consume — Bar shape, Provider Protocol
├── adrs/0006-persistence-layout.md       # YOU consume — SQLite cache layout
└── adrs/0004-strategy-interface.md       # context only — strategy contract, not your job

src/market_analyser/
├── data/
│   ├── types.py                          # READ — Bar / Quote / etc. pydantic models
│   └── default_provider.py               # READ via repository — never call .get_ohlcv directly
├── persistence/                          # READ via BarRepository — the cache layer
│   ├── engine.py
│   ├── models.py
│   └── repository.py
└── analysis/                             # READ via import — in-house per ADR-0009
    ├── indicators.py                     # RSI, MACD, EMA, Bollinger, Supertrend, etc.
    │                                     # DOES NOT EXIST YET — block honestly if a snapshot
    │                                     # needs indicators; route to architect.
    └── patterns.py                       # DOES NOT EXIST YET — block honestly if asked
                                          # for a pattern scan; route to architect.

runs/analysis/                            # YOU write here (gitignored)
├── scan/<UTC-timestamp>-<symbol>-<timeframe>/
│   ├── scan.json
│   └── scan.md
├── snapshot/<UTC-timestamp>-<symbol>-<timeframe>/
│   ├── snapshot.json
│   └── snapshot.md
└── screens/<UTC-timestamp>-<screen-slug>/
    ├── screen.json
    └── screen.md
```

The persistence layer (Plan 0001 phase 3) and the analysis layer (no plan yet) are the two dependencies the skill leans on. Always `Glob` them before proceeding — if either is missing, surface the block and stop.

## Sibling-skill ownership map

| Owner skill       | Code area                                       | Their job vs yours                                            |
|-------------------|-------------------------------------------------|---------------------------------------------------------------|
| `architect`       | `docs/architecture/`                            | Decides architecture (ADRs, plans, diagrams). Route new screener / new pattern detector / new indicator vendor decisions here. |
| `dev`             | API, data layer, persistence, vendoring, CI     | Owns the Python sidecar. You consume the data; you don't write sidecar code. |
| `strategy-author` | `src/market_analyser/strategies/`               | Writes trading strategies. You give them setups to consider; they decide what to encode. |
| `backtester`      | `src/market_analyser/backtest/`, `runs/`        | Runs backtests, computes Sharpe / drawdown. You produce *conditions*; they produce *historical P&L*. Don't confuse the two. |
| `ui-builder`      | `desktop/`                                      | Renders the analysis as charts / tables in the desktop app. You produce JSON; they render. |
| `defi-analyst`    | `src/defi_analyser/`                            | Analogous skill for on-chain (DeFi pools, lending, LP). Strict TradFi/DeFi split — you don't touch DeFi, they don't touch TradFi. |
| **`market-analyst` (you)** | `runs/analysis/` artifacts                 | Read-only condition analyst for TradFi. No code, no trades, no fetches. |

The TradFi/DeFi split is enforced by the skill descriptions. If the user is asking about a stock or an index, that's you. If they're asking about an LP or a lending position, that's `defi-analyst`. Don't reach across.

## Canonical commands

Python sidecar territory — same commands as `dev`'s context, repeated here for ease.

| Task                      | Command                                              |
|---------------------------|------------------------------------------------------|
| Install / sync env        | `uv sync`                                            |
| Run a Python REPL with the package importable | `uv run python`                  |
| Run a script              | `uv run python -m <module>`                          |
| Run tests                 | `uv run pytest`                                      |
| Lint                      | `uv run ruff check`                                  |
| Type-check (strict)       | `uv run mypy --strict src tests`                     |

For ad-hoc analysis, the pattern is:

```python
# inside `uv run python`
from market_analyser.persistence import BarRepository, get_engine
from market_analyser.analysis.indicators import (
    calc_rsi, calc_macd, calc_ema, calc_bollinger,
)

engine = get_engine()
repo = BarRepository(engine)
bars = repo.get_bars(symbol="AAPL", timeframe="1d", start=..., end=...)
# bars is a list[Bar]; pass to indicator funcs or your pattern detectors
```

If `BarRepository` doesn't exist yet (phase 3 unshipped), this import errors out — that's your honest block signal.

## The cache-only data path (why)

The user picked cached-SQLite-only over live-Yahoo per the iteration-1 design decisions. Reasons:

- **Determinism.** Analysis at time T should produce the same result if re-run later. Live data fetches break this.
- **Side-effect free.** No rate-limit surprises, no Yahoo outages mid-analysis, no "the chart changed under me" mid-session.
- **Honest blockers.** If a symbol isn't cached, that's a real gap the user should know about, not silently papered over.

Practical consequence: the skill is **paired with the UI**. Bars get into the cache when the user opens a chart for that symbol (Plan 0001 phase 5). The analyst then runs against whatever's there. If the user wants to analyse `XYZ` and the chart's never been opened for `XYZ`, the analyst blocks with a clear message: "no bars cached for XYZ; open the chart for it first, or have `dev` add a CLI to pre-populate."

This boundary is a feature, not a bug.

## State checkpoints

When you start a session, these tell you what mode the skill is in:

| Check                                                      | If present → you can do      |
|------------------------------------------------------------|-------------------------------|
| `src/market_analyser/persistence/` exists                  | Mode 2 (snapshot) on cached symbols. |
| `src/market_analyser/analysis/patterns.py` exists          | Mode 1 (pattern scan) — full version. |
| `src/market_analyser/analysis/indicators.py` | Mode 2 (snapshot) gets RSI/MACD/etc. |
| The Provider Protocol's `get_screener` is implemented      | Mode 3 (screener) without a watchlist. |

Today's expected state (early in the project):

- ✅ persistence layer exists (Plan 0001 phase 3 shipped)
- ❌ analysis/indicators.py doesn't exist (in-house per ADR-0009; no plan written yet)
- ❌ analysis/patterns.py doesn't exist (no plan written yet)
- ❌ get_screener raises NotImplementedError (per ADR-0007)

So at the time this skill was authored, **every mode blocks honestly**. That's correct — it mirrors how `backtester` blocked honestly when the engine didn't exist yet. The skill being correctly in "block" state is part of its value: it surfaces gaps clearly instead of fabricating results.

## Plan numbering & status

- Plans: `docs/architecture/plans/NNNN-<slug>.md` — zero-padded.
- ADRs: `docs/architecture/adrs/NNNN-<slug>.md`.
- Reviews: in-conversation only. There is **no** `docs/architecture/reviews/` directory.
- Completed plans: `docs/architecture/plans/done/NNNN-<slug>.md`.

You don't author plans or ADRs. If a question reveals a missing piece (new pattern, new indicator, new screener filter), route to `/architect` with a one-paragraph framing.

## Where to escalate

| Situation                                                    | Where to go                                                 |
|--------------------------------------------------------------|-------------------------------------------------------------|
| Persistence layer missing → can't read cache                 | Route to `/architect` / `/dev` for Plan 0001 phase 3         |
| `analysis/patterns.py` missing → can't run pattern scans     | Route to `/architect` — needs an ADR + plan; ~150 lines but shared infra |
| Indicator missing from `analysis/indicators.py`              | Route to `/architect` for an indicator-module expansion plan |
| User wants live data                                         | Stop. Cache-only is a deliberate boundary. Surface, don't pierce. |
| User asks for a buy/sell call                                | Restate the conditions. Stop short of the trade call. |
| User asks for a backtest                                     | Route to `/backtester`                                       |
| User asks for a strategy                                     | Route to `/strategy-author`                                  |
| User asks to render the analysis as a chart                  | Route to `/ui-builder`                                       |
| Question is on-chain (DeFi)                                  | Route to `/defi-analyst`                                     |
