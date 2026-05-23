# Mode 1 report template

This is the skeleton for `report.md` produced after a single backtest. The numbers come from `result.json`; this template is just the layout. Don't change the section order — downstream readers (and the user's eye) rely on it.

Replace `{{...}}` placeholders. Drop the entire "Notes" section if `meta.notes` is empty; otherwise include it.

---

```markdown
# Backtest report — {{meta.strategy_id}} v{{meta.strategy_version}}

> **Ran at:** {{meta.ran_at}}  
> **Run hash:** `{{meta.run_hash}}`  
> **Bars:** {{meta.bars_source}} ({{meta.bars_count}} bars, {{meta.bars_first_ts}} → {{meta.bars_last_ts}})

## Headline

- **Total return:** {{metrics.total_return_pct}} (buy-and-hold benchmark: {{metrics.buy_and_hold_return_pct}})
- **Sharpe:** {{metrics.sharpe}} _(annualized; based on {{meta.bars_count}} bars)_
- **Max drawdown:** {{metrics.max_drawdown_pct}} over {{metrics.max_drawdown_duration_bars}} bars
- **Trades:** {{metrics.n_trades}} ({{metrics.n_wins}}W / {{metrics.n_losses}}L, win rate {{metrics.win_rate}})
- **Profit factor:** {{metrics.profit_factor}}

## Parameters

```json
{{params}}
```

## Costs

- Commission: {{costs.commission_bps}} bps per side
- Slippage: {{costs.slippage_bps}} bps per side
- Initial cash: ${{costs.initial_cash}}

## Trade behavior

- Average win: {{metrics.avg_win_pct}}
- Average loss: {{metrics.avg_loss_pct}}
- Sortino: {{metrics.sortino}}
- CAGR: {{metrics.cagr}}

{{#if open_trades}}
- **{{open_trades_count}} open position(s) at end of bars** — unrealized P&L included in equity curve.
{{/if}}

## Equity curve

![equity curve]({{equity_curve_filename}})

## Notes

{{#each meta.notes}}
- {{this}}
{{/each}}

## How to reproduce

`spec.json` in this directory captures the exact inputs. Re-run with:

```bash
uv run market-analyser backtest run --spec spec.json
```

The output should be byte-identical to this `result.json`. If it isn't, there's a non-determinism bug — file it.
```

---

## Guidance on numbers

- Format percentages as `+12.34%` / `-5.67%` (sign always visible).
- Format Sharpe / Sortino to 2 decimal places. `NaN` renders as the string `n/a` with a footnote explaining why (e.g. "all returns identical — no variance").
- Format dollar amounts as `$1,234.56` with thousands separators.
- Datetimes in headers use the format `2026-05-17 14:22 UTC` (drop the seconds for readability; the full ISO 8601 lives in `result.json`).

## What goes in "Notes"

Any anomaly from `meta.notes`. Common ones:

- "{{N}} bars with zero volume — fills may be unrealistic."
- "{{N}} terminal signal(s) dropped (no next bar to execute on)."
- "Run has only {{N}} trades; metrics are statistically unreliable below ~30 trades."
- "Sharpe is NaN — all bar returns identical, no variance to measure."
- "Final open position carries {{P}} unrealized P&L; total return treats this as marked-to-market at the last bar's close."
- "Profit factor is infinite — no losing trades. Consider whether the test fixture or cost model is realistic."
