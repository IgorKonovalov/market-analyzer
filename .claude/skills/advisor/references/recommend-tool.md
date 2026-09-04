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

Only **gating** checks decide the verdict. A **directional** call requires all of:

1. At least one live strategy signal implies the direction, and **no** signal opposes it.
2. The walk-forward `sharpe_mean` is positive **and** belongs to a strategy that voted the direction.
3. *If — and only if — the direction forecast leg gates*, its argmax agrees with that direction.

**The direction leg gates conditionally** (ADR-0071). It votes only when its out-of-sample beats-baseline margin clears the pinned `DIRECTION_SKILL_MARGIN`. Below that — including whenever it shipped no probabilities at all — it is **demoted**: its checks still appear in the trace but carry `gating=False`, so they **block nothing**. A demoted leg cannot veto a corroborated call and cannot be the deciding vote; it also cannot manufacture one. Because the direction target has near-absent edge (ADR-0070), **expect the demoted path to be the normal case**, and expect `reason.direction_leg_nongating` in the rationale rather than a `forecast: P(long)=…` line.

Failed **gating** checks become named blockers in the flat verdict's rationale:

- `live signals conflict: long=[...], short=[...]`
- `no live strategy signal implies a direction`
- `live signals (X) disagree with the forecast direction (Y)` — only reachable while the leg gates
- `no walk-forward backtest basis supplied`
- `no backtested edge: walk-forward sharpe_mean=...`
- `walk-forward edge is for '...', which is not among the agreeing signals`

These two appear as **non-gating** trace rows, not blockers — seeing them does **not** mean the verdict is flat:

- `probabilities shipped (baseline beaten out-of-sample)` failing
- `argmax direction is directional` failing

The non-directional forecasts (volatility, regime) are likewise non-voting: they shape sizing, stop distance and conviction magnitude only.

## Conviction

```
edge_credit = clamp(sharpe_mean / 1.0, 0, 1)

# direction leg GATING (skill margin >= DIRECTION_SKILL_MARGIN):
conviction = P(direction) × edge_credit × regime_factor
# direction leg DEMOTED (no probability shipped) — the common case:
conviction =                edge_credit × regime_factor
```

`edge_credit` is the walk-forward edge — linear up to `sharpe_mean = 1.0`, saturating at full credit above it, zero at or below zero. `P(direction)` is the calibrated forecast probability of the called direction, and it is a factor **only when the direction leg gated**; on the demoted path there is no probability, so conviction rests on the backtested edge alone. `regime_factor` is the non-voting regime dampener (`1.0` unless a *trusted* transition model expects the regime to break).

**When narrating, name the branch — this is the failure mode the reference exists to prevent.** On the gating path, say the factors: a conviction of 0.30 from `P(long)=0.60 × edge=0.50` is a different story from `P(long)=0.75 × edge=0.40`. On the **demoted** path there are no factors to split, and the number means something narrower than it looks: conviction *is* the sharpe credit, so `1.0` means `sharpe_mean >= 1.0`, **not** near-certainty. Reporting a saturated demoted conviction as confidence that the call is right misrepresents a backtest as a probability — the boundary violation this contract is written to stop.

Check `direction_leg.gating` (and `basis.forecast.prob_up`/`prob_down` being `null`) to know which branch you are on before writing a word about conviction.

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
| **Directional** with `reason.direction_leg_nongating` | forecast found no edge over baseline, so the leg demoted — **not** a bug and **not** a flat | the normal case (ADR-0070); read conviction as edge credit alone, and say so |
| Flat with everything blocked | thin history starving all legs | check `range_start` gives several hundred bars before concluding "no edge" |
