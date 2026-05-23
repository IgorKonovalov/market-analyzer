# Backtester best practices

The longer-form correctness checklist. SKILL.md highlights the three non-negotiables (lookahead-safe execution, determinism, no silent information loss); this file covers the failure modes in more depth and the report-quality pitfalls that don't show up in unit tests.

## Cost realism — the silent overstater

A backtest with zero costs is a fantasy generator. The model is forgiving because every entry and exit is free, so high-turnover strategies look great. The number a user actually wants is "what would I have made *after* the broker's cut" — not the gross.

Defaults (until the ADR pins them) are `commission_bps = 5`, `slippage_bps = 5`. These are reasonable for liquid crypto and major equities; for thin altcoins or small-cap stocks, doubling slippage is more honest. If the user is running on an asset you suspect is thin, **say so in the report** — don't silently apply the default.

Apply costs **both sides**: entry and exit. A 5 bps commission means 5 bps off entry and 5 bps off exit, not 5 bps total. The engine's `_apply_costs` enforces this.

## Lookahead via the engine — the cardinal sin

The strategy might be lookahead-safe and the engine might still produce lookahead-tainted P&L if:

1. **The engine fills at `bars[i].close` instead of `bars[i+1].open`.** Same-instant execution; impossible in reality, and inflates P&L when momentum is the dominant factor.
2. **Slippage is applied as a free fraction of P&L instead of price.** Slippage should move the *fill price* unfavorably (worse open for a buy, worse close for a sell) — not be subtracted from final P&L. The difference matters when the price moves a lot during the trade.
3. **Equity curve uses next-bar prices to mark current-bar positions.** Mark-to-market at bar `i` should use `bars[i].close`, not `bars[i+1].open` (even though the next trade will fill at i+1's open).

A useful sanity test: run a strategy that emits an `ENTER_LONG` at every bar and an `EXIT_LONG` at the next bar (constant turnover). With realistic costs, this should lose money — every trade pays the spread for zero edge. If the engine reports gains, look for one of the three bugs above.

## Drawdown depth vs. drawdown duration

Headline drawdown (`max_drawdown_pct`) is only half the story. A strategy with 15% max drawdown that recovers in two weeks is very different from one with 15% max drawdown that takes 18 months to recover. The recovery duration is what makes a strategy psychologically tradable.

In the report, show both: depth and duration. The user looking at the equity curve wants to see the "flat stretch", not just the dip.

## Sharpe gotchas

Sharpe is the most-quoted, most-misused metric in backtesting:

1. **Sharpe over a 30-bar window is statistical noise.** Anything under ~200 bars and you're computing a random number. Surface the bar count next to Sharpe; don't trust it on short series.
2. **Sharpe assumes returns are roughly normal.** Strategies with rare big losses (negative skew) get a flattering Sharpe because the bad days are outliers. Sortino is slightly more honest (it only penalizes downside variance), but the same caveat applies.
3. **Annualization factor matters.** For 1h bars, the annualization factor is `sqrt(24 * 365) ≈ 92.6`, not the daily-bar `sqrt(252) ≈ 15.87`. Getting this wrong shifts Sharpe by 6×.
4. **Risk-free rate is zero in v1.** Document this; the user reading "Sharpe 1.5" should know it's an *excess* Sharpe of 1.5 vs. cash.

Show Sharpe, but show `n_trades` and `bars_count` right next to it. The user can then judge whether the Sharpe is signal or noise.

## Win rate is misleading on its own

A strategy with 90% win rate that loses $100 in the 10% bad cases and wins $10 in the 90% good cases is a loser. Always report win rate **alongside** average-win/average-loss:

- High win rate + small avg win + big avg loss = "death by a thousand cuts" — bad.
- Low win rate + big avg win + small avg loss = trend-following pattern — can be great.
- Both numbers without the other is half the picture.

`profit_factor = sum(wins) / abs(sum(losses))` collapses these into one number; > 1.0 is profitable. Show it.

## Few-trade backtests are not backtests

A run with 6 trades over 5 years is one good trade away from looking like a star and one bad trade away from looking like a dud. The number to watch is `n_trades`. As a rough rule:

- `n_trades < 30`: anything you say about the strategy is hand-waving. Note this in the report.
- `n_trades 30..100`: take the result with a grain of salt; look at the trade distribution.
- `n_trades > 100`: the metrics start being statistically meaningful.

Don't refuse to report; just call it out. The user might still want to see what happened — they just shouldn't conclude anything from it.

## Comparison fairness

When running Mode 2 (comparison or sweep), make sure variants are actually comparable:

1. **Same bars.** Always. If a strategy has a different `timeframes` constraint, either resample or document the asymmetry — don't silently run on different bars and pretend the comparison is apples-to-apples.
2. **Same costs.** A strategy that pays a 5 bps commission is in a different game than one paying 20 bps. Variants share the cost model.
3. **Same initial cash.** Otherwise return percentages aren't directly comparable.
4. **Same date range.** A strategy that starts later misses the early years of any bull market.
5. **Same number of warmup bars trimmed.** If RSI needs 14 bars to warm up and EMA needs 200, the EMA strategy effectively starts later — but the metrics get computed against the same denominator. Trim consistently (drop the first `max(warmup_bars_across_variants)` bars from all of them) or note the asymmetry.

If the user explicitly *wants* an unfair comparison (e.g., "real-world cost comparison: my broker charges 5 bps, theirs charges 30"), do it — but label the report so the difference is obvious.

## Determinism — the run-twice test

Determinism is testable. After landing the engine, the CI suite should include a "run the same fixture twice, assert byte-identical `result.json`" test. Until that exists, any non-deterministic source is a latent bug:

- `set` ordering in trade aggregation — use `list`/`dict`.
- `dict.popitem()` — Python 3.7+ ordered, but `.popitem(last=False)` differs from `.popitem(last=True)`; pick one explicitly.
- Floating-point reduction across threads — backtests are single-threaded; never spawn from inside engine code.
- Hash randomization of strings — Python 3 randomizes by default; don't `hash()` strings in trade IDs.
- `time.time()` in cost models or metric calculations — the only time-related field is `ran_at` in `meta`, set once at the start of `engine.run()`.

The `run_hash` in `BacktestMeta` is precisely a defense against this: two runs of the same spec should produce the same hash *and* the same `result.json`. If hashes match but JSON differs, you've found a non-determinism bug.

## Edge cases worth handling

- **Zero trades.** All metrics that divide by `n_trades` should return `0.0` or `NaN`, not raise. Report still gets written.
- **All-winners.** `profit_factor` is `+inf`; serialize as the string `"inf"` in JSON (not `null`), and surface "no losing trades — statistically suspicious" as a note.
- **Final position open at end of bars.** The trade has `exit_bar_index = None`; mark-to-market at the final bar's close. P&L is reported as "unrealized" in the report.
- **Bars with `volume = 0`.** The engine still fills at next bar's open; flag as a note (`"5 bars with zero volume — fills may be unrealistic"`).
- **Time gaps in bars.** Weekend gaps in stock data, missing-bar gaps in crypto. Don't interpolate; let the gap be visible in the equity curve. Note in `meta.notes` if gaps > 1 bar exist.

## When to ship vs. when to flag

You produce metrics. You don't certify a strategy as profitable — that's the user's call after looking at *all* of them. But you should flag:

- **Sharpe > 3 on `n_trades < 50`** — almost certainly overfit on noise. Surface as a note.
- **Max drawdown of 0%** — usually a bug (no losing trade ever closed); investigate before reporting.
- **Final equity matches initial cash to 4+ decimal places** — the strategy probably did nothing. Confirm before reporting "0% return".
- **All trades have identical P&L** — bug in cost application or sizing.

These don't block the report; they just appear in `meta.notes` so the user sees them.
