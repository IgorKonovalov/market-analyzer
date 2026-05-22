"""Generator for Plan 0008 phase-2 golden fixtures.

Run this once (and again whenever `ENGINE_VERSION` bumps with output-affecting
changes) to regenerate the committed `aapl_1d_200bars.csv` +
`rsi_default_expected.json` pair the phase-2 golden test pins against.

Bar generation:

- 200 daily bars starting at 2025-01-01 UTC.
- Close path: `100 + math.sin(i / 10.0) * 12 + (i / 200) * 18`, giving an
  oscillation amplitude of 12 around a slow linear uptrend of +18 over the
  series. The 10-bar period plus the 14-bar RSI period guarantee at least a
  handful of cross-down / cross-up events for the default 30/70 thresholds.
- Open := close of the previous bar (close[-1] for the first bar); high :=
  max(open, close); low := min(open, close); volume := 0.
- All values rounded to 4 decimal places at write time so the committed CSV
  diffs cleanly.

Usage (from the repo root):

    uv run python tests/fixtures/backtest/_generate.py

Outputs (overwritten):

- tests/fixtures/backtest/aapl_1d_200bars.csv
- tests/fixtures/backtest/rsi_default_expected.json
"""

from __future__ import annotations

import csv
import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

from market_analyser.backtest import run
from market_analyser.data.types import Bar
from market_analyser.strategies import rsi as rsi_strategy

FIXTURE_DIR = Path(__file__).parent
CSV_PATH = FIXTURE_DIR / "aapl_1d_200bars.csv"
JSON_PATH = FIXTURE_DIR / "rsi_default_expected.json"

N_BARS = 200
SYMBOL = "AAPL"
TIMEFRAME = "1d"
START = datetime(2025, 1, 1, tzinfo=UTC)


def _generate_closes() -> list[float]:
    closes: list[float] = []
    for i in range(N_BARS):
        c = 100.0 + math.sin(i / 10.0) * 12.0 + (i / 200.0) * 18.0
        closes.append(round(c, 4))
    return closes


def _generate_bars() -> list[Bar]:
    closes = _generate_closes()
    bars: list[Bar] = []
    prev_close = closes[0]
    for i, close in enumerate(closes):
        bar_open = round(prev_close, 4)
        high = round(max(bar_open, close), 4)
        low = round(min(bar_open, close), 4)
        bars.append(
            Bar(
                symbol=SYMBOL,
                timeframe=TIMEFRAME,
                event_ts=START + timedelta(days=i),
                open=bar_open,
                high=high,
                low=low,
                close=close,
                volume=0.0,
                source="fixture",
            )
        )
        prev_close = close
    return bars


def _write_csv(bars: list[Bar]) -> None:
    with CSV_PATH.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(
            ["symbol", "timeframe", "event_ts", "open", "high", "low", "close", "volume", "source"]
        )
        for bar in bars:
            writer.writerow(
                [
                    bar.symbol,
                    bar.timeframe,
                    bar.event_ts.isoformat(),
                    f"{bar.open:.4f}",
                    f"{bar.high:.4f}",
                    f"{bar.low:.4f}",
                    f"{bar.close:.4f}",
                    f"{bar.volume:.4f}",
                    bar.source,
                ]
            )


def _write_expected_json(bars: list[Bar]) -> None:
    result = run(
        rsi_strategy,
        bars,
        {"period": 14, "oversold": 30.0, "overbought": 70.0},
        timeframe=TIMEFRAME,
        commission_bps=0.0,
        slippage_bps=0.0,
        initial_capital=10_000.0,
    )
    dumped = result.model_dump(mode="json")
    for nondeterministic in ("run_id", "started_at", "finished_at"):
        dumped.pop(nondeterministic, None)
    JSON_PATH.write_text(
        json.dumps(dumped, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    bars = _generate_bars()
    _write_csv(bars)
    _write_expected_json(bars)
    print(f"Wrote {CSV_PATH.relative_to(FIXTURE_DIR.parent.parent.parent)}")
    print(f"Wrote {JSON_PATH.relative_to(FIXTURE_DIR.parent.parent.parent)}")


if __name__ == "__main__":
    main()
