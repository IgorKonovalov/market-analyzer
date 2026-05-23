# Mode 2 comparison template

Skeleton for `comparison.md` produced by Mode 2 (sweep or strategy-vs-strategy). Keep section order — downstream readers and the user's eye rely on it.

Replace `{{...}}` placeholders. Sort the table by whatever the user asked for (default: Sharpe descending, then total return descending as tiebreaker).

---

```markdown
# Comparison — {{comparison_title}}

> **Ran at:** {{ran_at}}  
> **Variants:** {{n_variants}}  
> **Bars:** {{bars_source}} ({{bars_count}} bars, {{bars_first_ts}} → {{bars_last_ts}})  
> **Costs (shared):** {{costs.commission_bps}} bps commission, {{costs.slippage_bps}} bps slippage, ${{costs.initial_cash}} initial

## Ranking

| Rank | Variant | Sharpe | Total return | Max DD | Trades | Win rate | Profit factor |
|------|---------|--------|--------------|--------|--------|----------|---------------|
{{#each variants}}
| {{rank}} | [{{label}}]({{run_dir}}/report.md) | {{metrics.sharpe}} | {{metrics.total_return_pct}} | {{metrics.max_drawdown_pct}} | {{metrics.n_trades}} | {{metrics.win_rate}} | {{metrics.profit_factor}} |
{{/each}}

Each row links to the underlying single-run report.

## Equity-curve overlay

![equity overlay]({{equity_overlay_filename}})

Buy-and-hold benchmark for the same bars: {{buy_and_hold_return_pct}}.

## Observations

{{observations}}

(Auto-generated highlights. The numbers don't lie, but they also don't reason about *why* — that's the user's call.)

## Fairness audit

- Same bars: ✓ all variants run on `{{bars_source}}`.
- Same costs: ✓ all variants use the same `BacktestCosts`.
- Same date range: {{same_date_range_check}}
- Warmup bars trimmed: {{warmup_trim_note}}

{{#if fairness_warnings}}
{{#each fairness_warnings}}
- ⚠ {{this}}
{{/each}}
{{/if}}

## How to reproduce

```bash
uv run market-analyser backtest compare --spec specs.json
```

`specs.json` in this directory captures every variant's spec.
```

---

## Guidance on "Observations"

These are auto-generated highlights, not commentary. Keep them factual and specific:

- **Spread of the metric.** "Sharpe ranges from -0.12 to 1.84 across variants — wide spread suggests param sensitivity."
- **Winner caveats.** "Best Sharpe (1.84) has only 18 trades — statistically thin." or "Best total return (+45%) has 38% drawdown — risky path to that return."
- **Surprising patterns.** "Variants with `oversold < 30` cluster at low Sharpe — strategy may need a more aggressive entry threshold than the user expected."
- **No-difference results.** "All five variants returned within 0.5% of each other — the swept parameter has near-zero effect on this fixture." (This is a *useful* observation — saves the user time.)

What **not** to put in observations:
- "X is a great strategy" — judgment call, not yours.
- "You should pick variant 3" — the user picks; you measure.
- Speculation about why ("the market was trending so EMA won"). Stick to what's in the numbers; the strategy-author or the user can read the chart.

## When to flag the comparison as unfair

If you detect any of these, surface them in "Fairness audit":

- Different `timeframes` declared in the META of compared strategies (1h vs 1d, etc.) — annualized metrics aren't directly comparable.
- Different bar ranges (one strategy needs ≥200 warmup bars, another needs ≥14) — without consistent trimming, the comparison is silently skewed.
- Different cost models (if the user explicitly overrode for some variants).
- A strategy that crashed during one variant and got `n_trades = 0` — show the result but flag prominently.
