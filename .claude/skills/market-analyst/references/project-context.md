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
├── adrs/0006-persistence-layout.md       # context — SQLite cache layout (provider reads it)
├── adrs/0023-technical-analysis-surface.md  # YOUR backend's decision record (accepted 2026-05-30)
└── adrs/0004-strategy-interface.md       # context only — strategy contract, not your job

src/market_analyser/
├── data/types.py                         # READ — Bar / Quote / ConditionSnapshot field shapes
└── analysis/                             # YOUR backend — in-house, pure, trailing (ADR-0023, Plan 0018)
    ├── indicators.py                     # EMA SMA RSI Bollinger MACD ATR Supertrend Donchian ADX
    ├── patterns.py                       # detect_patterns — the 14-pattern candlestick vocabulary
    ├── snapshot.py                       # condition_snapshot — composes the above into one read
    └── types.py                          # ConditionSnapshot / PatternHit / Trend / MomentumStance

# You normally reach all of the above through ONE MCP tool, not by importing:
#   analyze_symbol(symbol, timeframe, lookback="6mo", as_of=None)  -> {snapshot, partial_reason, message, analyzed_at}
#   screener_query(filters, market, exchange, limit)               -> universe screen (Plan 0009)
#   get_ohlcv / backfill_ohlcv                                     -> read / populate cached bars (Plan 0013)

runs/analysis/                            # YOU write here (gitignored)
├── scan/<UTC-timestamp>-<symbol>-<timeframe>/      { scan.json, scan.md }
├── snapshot/<UTC-timestamp>-<symbol>-<timeframe>/  { snapshot.json, snapshot.md }
└── screens/<UTC-timestamp>-<screen-slug>/          { screen.json, screen.md }
```

Your backend is **live** (Plan 0018 closed 2026-05-30). You don't glob for it to check existence anymore — you call `analyze_symbol`. The only honest block left is an **empty cache** for a symbol (the tool returns `partial_reason: "no_bars"`), which the user resolves by backfilling or opening the chart.

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

## How you run analysis

**Primary path — the MCP tool.** Almost everything you do is one call to `analyze_symbol` on the `market-analyser` sidecar, then narrating the returned `snapshot` and writing the `runs/analysis/` artifacts. You don't import Python for the common case. The tool dispatches through the provider (which reads the cache) and runs `condition_snapshot` off-thread.

```
analyze_symbol(symbol="AAPL", timeframe="1d", lookback="6mo")
# -> { "snapshot": { "trend": "...", "momentum": "...", "indicators": {...},
#                    "support_resistance": {...},
#                    "nearest_support": { "price": ..., "strength": ..., ... } | null,
#                    "nearest_resistance": { ... } | null,   # Plan 0051: clustered
#                    "recent_patterns": [...] },             # levels framing the close
#      "partial_reason": null, "message": null, "analyzed_at": "..." }
# Empty cache -> { "snapshot": null, "partial_reason": "no_bars", "message": "..." }
```

**Rare fallback — direct import.** Only when the snapshot tool genuinely can't answer (e.g. a full multi-bar historical pattern sweep, which `recent_patterns` doesn't cover), the in-house modules are importable. Note the real function names — bare verbs, **no `calc_` prefix**:

```python
# inside `uv run python` — deep-sweep fallback only
from market_analyser.analysis.indicators import rsi, macd, ema, bollinger, atr, supertrend, adx
from market_analyser.analysis.patterns import detect_patterns
from market_analyser.analysis.snapshot import condition_snapshot
# bars: get them via the provider's get_ohlcv (cache read); each is a data.types.Bar.
hits = detect_patterns(bars)   # every PatternHit over the whole series, sorted by (bar_index, pattern)
```

| Task                      | Command                                              |
|---------------------------|------------------------------------------------------|
| Install / sync env        | `uv sync`                                            |
| Ad-hoc REPL               | `uv run python`                                      |
| Run tests                 | `uv run pytest`                                      |
| Type-check (strict)       | `uv run mypy --strict src tests`                     |

## The cache-only data path (why)

The user picked cached-SQLite-only over live-Yahoo per the iteration-1 design decisions. Reasons:

- **Determinism.** Analysis at time T should produce the same result if re-run later. Live data fetches break this.
- **Side-effect free.** No rate-limit surprises, no Yahoo outages mid-analysis, no "the chart changed under me" mid-session.
- **Honest blockers.** If a symbol isn't cached, that's a real gap the user should know about, not silently papered over.

Practical consequence: bars get into the cache when the user opens a chart for that symbol, or when something calls `backfill_ohlcv` (Plan 0013). The analyst runs against whatever's cached. If the user wants to analyse `XYZ` and it's never been fetched, `analyze_symbol` returns `partial_reason: "no_bars"` and the analyst surfaces a clear message: "no bars cached for XYZ — want me to backfill it (`backfill_ohlcv`), or open the chart for it first?" Backfilling is a user-authorized step, distinct from the analysis itself, which stays deterministic on cached bars.

This boundary is a feature, not a bug.

## Capability state (as of 2026-05-30)

All four modes are live. The skill is past its "everything blocks honestly" infancy:

- ✅ **Mode 1 (pattern scan)** — `analyze_symbol` returns `recent_patterns` (most-recent-bars window); deep historical sweep via `detect_patterns` fallback.
- ✅ **Mode 2 (snapshot)** — `analyze_symbol` returns the full trend/momentum/indicators/S-R read in one call.
- ✅ **Mode 3 (screener)** — `screener_query` (TradingView universe, Plan 0009) for universe screens; `analyze_symbol` per-symbol for watchlist screens.
- ✅ **Mode 4 (brainstorm)** — conversational; never fetched anything, still doesn't.

The **only** honest block left is data, not code: a symbol with no cached bars makes `analyze_symbol` return `partial_reason: "no_bars"`. That's resolved by populating the cache (`backfill_ohlcv`, or the user opening the chart), not by routing to architect. Routing to `architect` is now reserved for genuinely new capability — a new indicator/pattern the backend doesn't expose, or a new screener filter shape.

## Plan numbering & status

- Plans: `docs/architecture/plans/NNNN-<slug>.md` — zero-padded.
- ADRs: `docs/architecture/adrs/NNNN-<slug>.md`.
- Reviews: in-conversation only. There is **no** `docs/architecture/reviews/` directory.
- Completed plans: `docs/architecture/plans/done/NNNN-<slug>.md`.

You don't author plans or ADRs. If a question reveals a missing piece (new pattern, new indicator, new screener filter), route to `/architect` with a one-paragraph framing.

## Where to escalate

| Situation                                                    | Where to go                                                 |
|--------------------------------------------------------------|-------------------------------------------------------------|
| `analyze_symbol` returns `partial_reason: "no_bars"`         | Surface the empty-cache gap; offer `backfill_ohlcv` or "open the chart for it". User-authorized, not silent. |
| User wants an indicator/pattern the backend doesn't expose   | Route to `/architect` — extending the analysis surface is an ADR/plan decision, not skill-private math. |
| User wants a screener filter `screener_query` doesn't support | Route to `/architect` (provider/adapter change) — don't fake the column. |
| User wants the analysis silently kept "fresh" / live-streamed | Stop. Analysis runs on cached bars; backfill is an explicit user step, never folded into a snapshot. |
| User asks for a buy/sell call                                | Restate the conditions. Stop short of the trade call. |
| User asks for a backtest                                     | Route to `/backtester`                                       |
| User asks for a strategy                                     | Route to `/strategy-author`                                  |
| User asks to render the analysis as a chart                  | Route to `/ui-builder`                                       |
| Question is on-chain (DeFi)                                  | Route to `/defi-analyst`                                     |
