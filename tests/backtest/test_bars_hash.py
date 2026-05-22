"""Tests for `bars_hash`.

Per Plan 0008 phase 1 done-when:

- Same bar list → same hash on two consecutive calls.
- Empty bar list → SHA256 of the empty UTF-8 buffer (`e3b0c4...b855`).
- Different bar lists → different hashes.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from market_analyser.backtest import bars_hash
from market_analyser.data.types import Bar


def _bars(closes: Sequence[float], symbol: str = "TEST") -> list[Bar]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Bar(
            symbol=symbol,
            timeframe="1d",
            event_ts=start + timedelta(days=i),
            open=c,
            high=c,
            low=c,
            close=c,
            volume=0.0,
            source="fixture",
        )
        for i, c in enumerate(closes)
    ]


def test_same_bars_same_hash() -> None:
    bars = _bars([100.0, 105.0, 110.0])
    assert bars_hash(bars) == bars_hash(bars)


def test_empty_bars_hashes_to_sha256_of_empty_buffer() -> None:
    expected = hashlib.sha256(b"").hexdigest()
    assert bars_hash([]) == expected


def test_different_bars_different_hashes() -> None:
    a = _bars([100.0, 105.0, 110.0])
    b = _bars([100.0, 105.0, 111.0])
    assert bars_hash(a) != bars_hash(b)


def test_different_timestamps_different_hashes() -> None:
    bars_a = _bars([100.0, 110.0])
    start = datetime(2027, 1, 1, tzinfo=UTC)
    bars_b = [
        Bar(
            symbol="TEST",
            timeframe="1d",
            event_ts=start + timedelta(days=i),
            open=c,
            high=c,
            low=c,
            close=c,
            volume=0.0,
            source="fixture",
        )
        for i, c in enumerate([100.0, 110.0])
    ]
    assert bars_hash(bars_a) != bars_hash(bars_b)


def test_hash_is_hex_string_of_correct_length() -> None:
    bars = _bars([100.0])
    digest = bars_hash(bars)
    assert len(digest) == 64
    int(digest, 16)  # raises if not hex
