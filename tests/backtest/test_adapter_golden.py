"""Golden test: RSI on a 200-bar synthetic fixture, byte-equal to expected JSON.

The fixture (`rsi_signals_to_trades.bars.csv`) and the expected trade list
(`rsi_signals_to_trades.expected.json`) are produced by
`scripts/gen_rsi_fixture.py`. The serialization format is fixed: `indent=2`,
`sort_keys=True`, trailing newline, UTF-8. That guarantees the byte-for-byte
diff this test relies on stays stable under formatter or pydantic changes.

The unit tests in `test_adapter_unit.py` are the correctness anchor — they
compare the adapter's output against hand-rolled values. This file's job is
to catch regressions across the full strategy → adapter chain.

When the strategy or the adapter changes intentionally and breaks this test,
the fix is to regenerate both fixture files in the same commit and verify the
diff against the unit tests' invariants before accepting the new golden.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from market_analyser.backtest import signals_to_trades
from market_analyser.contracts import Bar
from market_analyser.strategies import rsi

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
BARS_CSV = FIXTURES / "rsi_signals_to_trades.bars.csv"
EXPECTED_JSON = FIXTURES / "rsi_signals_to_trades.expected.json"


def _load_bars() -> list[Bar]:
    bars: list[Bar] = []
    with BARS_CSV.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bars.append(
                Bar(
                    symbol=row["symbol"],
                    timeframe=row["timeframe"],
                    event_ts=datetime.fromisoformat(row["event_ts"]),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                    source=row["source"],
                )
            )
    return bars


def _serialize_trades_matching_generator(trades_payload: list[dict[str, object]]) -> str:
    return json.dumps(trades_payload, indent=2, sort_keys=True) + "\n"


def test_rsi_signals_to_trades_matches_expected_json_byte_for_byte() -> None:
    bars = _load_bars()
    signals = list(rsi.generate_signals(bars, rsi.Params()))
    trades = signals_to_trades(bars, signals)
    serialized = _serialize_trades_matching_generator([t.model_dump(mode="json") for t in trades])
    expected = EXPECTED_JSON.read_text(encoding="utf-8")
    assert serialized == expected


def test_adapter_is_referentially_transparent_on_fixture() -> None:
    # Sanity: running the same (bars, signals) twice through the adapter
    # produces equal lists in memory. Cheaper than re-serializing twice, and
    # catches non-determinism the JSON test cannot (e.g. set iteration).
    bars = _load_bars()
    signals = list(rsi.generate_signals(bars, rsi.Params()))
    a = signals_to_trades(bars, signals)
    b = signals_to_trades(bars, signals)
    assert a == b
