"""Golden tests for `run()`.

Two acceptance criteria from Plan 0008 phase 2 done-when:

1. **In-process determinism:** two calls in the same process produce
   `BacktestResult` objects whose `.model_dump(mode="json")` dicts —
   minus `run_id` / `started_at` / `finished_at` — are equal
   element-by-element.

2. **Cross-process / cross-machine determinism:** the dump above equals
   the committed `tests/fixtures/backtest/rsi_default_expected.json`
   byte-for-byte. Regenerating the fixture requires bumping
   `ENGINE_VERSION`; the workflow is documented in
   `tests/fixtures/backtest/_generate.py`.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from market_analyser.backtest import run
from market_analyser.data.types import Bar
from market_analyser.strategies import rsi as rsi_strategy

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "backtest"
CSV_PATH = FIXTURE_DIR / "aapl_1d_200bars.csv"
EXPECTED_PATH = FIXTURE_DIR / "rsi_default_expected.json"

NONDETERMINISTIC_FIELDS = {"run_id", "started_at", "finished_at"}


def _load_bars() -> list[Bar]:
    """Load the AAPL/1d/200-bar fixture from disk."""

    bars: list[Bar] = []
    with CSV_PATH.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
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


def _run_with_default_params() -> dict[str, Any]:
    bars = _load_bars()
    result = run(
        rsi_strategy,
        bars,
        {"period": 14, "oversold": 30.0, "overbought": 70.0},
        timeframe="1d",
        commission_bps=0.0,
        slippage_bps=0.0,
        initial_capital=10_000.0,
    )
    dumped = result.model_dump(mode="json")
    for nondeterministic in NONDETERMINISTIC_FIELDS:
        dumped.pop(nondeterministic, None)
    return dumped


def test_in_process_determinism() -> None:
    a = _run_with_default_params()
    b = _run_with_default_params()
    assert a == b
    for key in a:
        assert a[key] == b[key], f"mismatch at field {key!r}"


def test_matches_committed_golden_fixture() -> None:
    actual = _run_with_default_params()
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    # Per-field comparison gives a much clearer failure than full-dict equality.
    assert actual.keys() == expected.keys(), (
        f"top-level field set drift: extra={set(actual) - set(expected)!r}, "
        f"missing={set(expected) - set(actual)!r}"
    )
    for key in expected:
        assert actual[key] == expected[key], (
            f"golden mismatch at field {key!r}: expected={expected[key]!r}, actual={actual[key]!r}"
        )


def test_fixture_files_exist() -> None:
    """Belt-and-suspenders: a missing CSV/JSON should fail clearly, not silently."""

    assert CSV_PATH.exists(), f"missing fixture: {CSV_PATH}"
    assert EXPECTED_PATH.exists(), f"missing fixture: {EXPECTED_PATH}"
