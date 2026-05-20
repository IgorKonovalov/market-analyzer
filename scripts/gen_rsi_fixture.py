"""Generate the deterministic fixture + expected-trades JSON for the golden test.

Run with `uv run python scripts/gen_rsi_fixture.py` from the repo root. Writes
two files under `tests/fixtures/`:

- `rsi_signals_to_trades.bars.csv` — 200 synthetic bars (a triangle wave
  between 75.0 and 100.0, period 50, integer steps). Carries the full `Bar`
  schema: `symbol`, `timeframe`, `event_ts`, OHLCV, `source`.
- `rsi_signals_to_trades.expected.json` — the trade list the RSI strategy
  produces on this fixture, serialized with `indent=2, sort_keys=True`,
  trailing newline. The golden test compares the live re-run against this
  file byte-for-byte.

Why a triangle wave: the symmetric leg structure produces Wilder-RSI crossings
into both the oversold (40) and overbought (60) zones once per cycle, so the
adapter sees a handful of clean entry→exit pairs over the 200-bar history
rather than a single trade or none at all.

Re-running this script is idempotent on a clean tree: same code in, same two
files out, byte-for-byte. If the fixture or the strategy changes
intentionally, regenerate both files in the same commit.
"""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from market_analyser.backtest import signals_to_trades
from market_analyser.contracts import Bar
from market_analyser.strategies import rsi

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures"
BARS_CSV = FIXTURES_DIR / "rsi_signals_to_trades.bars.csv"
EXPECTED_JSON = FIXTURES_DIR / "rsi_signals_to_trades.expected.json"

N_BARS = 200
PERIOD = 50  # 25 bars down, 25 bars up
BASELINE = 100.0
TROUGH = 75.0
START_TS = datetime(2026, 1, 1, tzinfo=UTC)
SYMBOL = "SYNTH"
TIMEFRAME = "1h"
SOURCE = "fixture"


def _triangle_close(i: int) -> float:
    """Close at bar `i` in a 50-bar triangle wave between 75 and 100."""

    pos = i % PERIOD
    if pos <= PERIOD // 2:
        return round(BASELINE - pos, 4)  # 100 at pos=0, 75 at pos=25
    return round(BASELINE - (PERIOD - pos), 4)  # 76 at pos=26, 99 at pos=49


def _build_bars() -> list[Bar]:
    closes = [_triangle_close(i) for i in range(N_BARS)]
    # open[i] = close[i-1] so each bar's open is the prior close, except the
    # first bar whose open equals its own close. high/low are the open/close
    # extremes (no intra-bar wiggle), keeping the Bar invariants satisfied
    # without inventing data.
    bars: list[Bar] = []
    for i, close in enumerate(closes):
        open_ = closes[i - 1] if i > 0 else close
        bars.append(
            Bar(
                symbol=SYMBOL,
                timeframe=TIMEFRAME,
                event_ts=START_TS + timedelta(hours=i),
                open=open_,
                high=max(open_, close),
                low=min(open_, close),
                close=close,
                volume=0.0,
                source=SOURCE,
            )
        )
    return bars


def _write_bars_csv(bars: list[Bar]) -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    with BARS_CSV.open("w", encoding="utf-8", newline="\n") as f:
        writer = csv.writer(f, lineterminator="\n")
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
    signals = list(rsi.generate_signals(bars, rsi.Params()))
    trades = signals_to_trades(bars, signals)
    payload = [t.model_dump(mode="json") for t in trades]
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    EXPECTED_JSON.write_text(serialized, encoding="utf-8", newline="\n")


def main() -> None:
    bars = _build_bars()
    _write_bars_csv(bars)
    _write_expected_json(bars)
    print(f"wrote {BARS_CSV.relative_to(REPO_ROOT)} ({len(bars)} bars)")
    print(f"wrote {EXPECTED_JSON.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
