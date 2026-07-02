# The `recommend` MCP tool — full interface

Source of truth: `src/market_analyser/api/mcp_tools/recommend.py` and `src/market_analyser/advisor/` (Plan 0038, ADR-0029). If this file disagrees with the code, trust the code and flag the drift.

## Parameters

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `strategy_id` | str | required | A registered strategy (see "Picking the strategy" below). Unknown id → error listing the known ids. |
| `symbol` | str | required | Same symbol vocabulary as `get_ohlcv` (Yahoo symbols like `AAPL`/`BTC-USD`; Binance pairs like `BTCUSDT` are distinct symbols). |
| `timeframe` | str | required | Must be supported by both the app and the strategy's `META.timeframes` — the tool errors otherwise. |
| `range_start` | datetime | required | Start of the warm-up lookback window (end is now). Request **several hundred bars**: indicator warm-up + `n_splits` walk-forward folds + forecast training all eat history. Too little history → legs starve → artificial flat or an error. |
| `params` | dict \| None | `None` | Strategy params, validated against the strategy's own `Params` model (`extra="forbid"` — unknown keys are rejected at the boundary, not dropped). |
| `horizon_bars` | int | `1` | Forecast horizon in bars (≥ 1). |
| `flat_band` | float | `0.001` | Forecast flat-band width (≥ 0). |
| `n_splits` | int | `5` | Walk-forward folds (≥ 2) — shared by the walk-forward leg and forecast validation. |
| `seed` | int | model default | Forecast training seed. Record it in the artifact — it's part of reproducibility. |

Bars are fetched on miss where the data layer supports it (via the backfill coordinator); only **closed** bars are used (a bar is closed once a full bar-duration has elapsed since it opened). All four legs are computed from that same closed-bar series, so the whole basis shares one as-of bar — no leg peeks past another.

## The `Recommendation` shape

```
symbol, timeframe
direction      : "long" | "short" | "flat"
entry_zone     : (low, high) | null      — null when flat
stop           : float | null            — null when flat
targets        : [float, ...]            — [] when flat
conviction     : 0.0–1.0                 — 0.0 when flat
rationale      : [str, ...]              — for a flat verdict: "no actionable edge" + one line per blocker
basis:
  conditions   : ["trend=up", "momentum=bullish", "volume=normal", "candlestick=..."]
  signals      : ["<strategy_id>: position=long, fresh_signal", ...]
  backtest     : {strategy_id, n_splits, sharpe_mean, sharpe_std, total_return_mean, total_return_std} | null
  forecast     : {prob_up, prob_down, prob_flat, horizon_bars, skill, baseline_skill, beats_baseline, edge_margin, edge_strength, model_version}
label          : "advisory"              — always; no other value can exist
as_of_bar_ts   : datetime                — the last closed bar every leg saw
```

Structural guarantees (enforced by the pydantic models — you never need to double-check them, but you should *narrate* them): a directional call always carries non-empty rationale, a backtest basis, a forecast basis, and complete entry/stop/target; a flat call never carries levels and always has conviction 0.0.

## How the verdict is decided

A **directional** call requires all of:

1. The forecast shipped probabilities (it ships `null`s when it has no edge over baseline) and its argmax is strictly `up` or `down` (ties and flat-argmax are conservative flats).
2. At least one live strategy signal implies the same direction, and **no** signal opposes it.
3. The walk-forward `sharpe_mean` is positive **and** belongs to a strategy that voted the direction.

Each failed condition becomes a named blocker in the flat verdict's rationale:

- `forecast shows no edge over baseline (no probability shipped)`
- `forecast direction is flat or undecided`
- `live signals conflict: long=[...], short=[...]`
- `no live strategy signal implies a direction`
- `live signals (X) disagree with the forecast direction (Y)`
- `no walk-forward backtest basis supplied`
- `no backtested edge: walk-forward sharpe_mean=...`
- `walk-forward edge is for '...', which is not among the agreeing signals`

## Conviction

```
conviction = P(direction) × clamp(sharpe_mean / 1.0, 0, 1)
```

`P(direction)` is the calibrated forecast probability of the called direction; the second factor is the walk-forward edge credit — linear up to `sharpe_mean = 1.0`, saturating at full credit above it, zero at or below zero. Monotone in both inputs. When narrating: a conviction of 0.30 from `P(long)=0.60 × edge=0.50` is a different story from `P(long)=0.75 × edge=0.40` — say the factors, not just the product.

## Levels (chart geometry, not opinion)

- **Entry zone**: last close ± 0.25 ATR.
- **Stop**: beyond the nearest opposing S/R level (support for a long, resistance for a short) with a 0.1 ATR buffer; fallback 2 ATR from close when no level exists on that side.
- **Target**: the nearest favouring level; fallback 2 ATR.
- No ATR available (thin history) → 2% of last close is used as the volatility unit.

## Picking the strategy

The strategy leg shapes the whole recommendation — `recommend` evaluates **one** strategy's live signal and demands *that* strategy's walk-forward edge. Strategies live in `src/market_analyser/strategies/` (one module each; `META.timeframes` says what each supports). When the user doesn't name one:

- Match style to ask: mean-reversion strategies for "is this oversold bounce real", breakout strategies for "should I chase this break", etc.
- Or run the Mode 2 sweep (one call per strategy) and report the spread.
- Say which you picked and why — an unstated strategy choice is an unstated assumption in the call.

## Common failure shapes

| Symptom | Likely cause | Move |
|---------|-------------|------|
| Error: no bars | cache empty for the window | offer `backfill_ohlcv` (visible step), then retry |
| Error: unknown strategy_id | typo / stale memory | the error lists known ids — pick from it |
| Error: timeframe not supported by strategy | strategy's `META.timeframes` is narrower | switch timeframe or strategy; tell the user |
| Flat with `no probability shipped` | forecast found no edge over baseline | honest no-edge; more history or a different horizon *may* change it — don't fish |
| Flat with everything blocked | thin history starving all legs | check `range_start` gives several hundred bars before concluding "no edge" |
