"""Plan 0025 phases 1-2: the canonical timeframe registry (`data/timeframes.py`).

The load-bearing test is `test_registry_keys_equal_supported_timeframes` — it is
the enforced invariant ADR-0028 relies on so the two views of the supported set
(the `SUPPORTED_TIMEFRAMES` frozenset in `annotations/types.py` and the registry)
cannot drift without a cross-layer import.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from market_analyser.annotations.types import SUPPORTED_TIMEFRAMES
from market_analyser.data import timeframes as tf


def test_registry_keys_equal_supported_timeframes() -> None:
    # ADR-0028 invariant: the set in annotations/types.py and the registry are two
    # views of one truth. If this fails, widen both together.
    assert tf.registry_timeframes() == SUPPORTED_TIMEFRAMES


def test_native_timeframes_exclude_resampled() -> None:
    # 4h is derived (resampled from 1h), so it is NOT natively fetchable; every
    # other registered timeframe is.
    assert tf.native_timeframes() == tf.registry_timeframes() - {"4h"}
    assert "4h" not in tf.native_timeframes()


def test_4h_is_resampled_from_1h_and_has_no_yahoo_interval() -> None:
    assert tf.resampled_from("4h") == "1h"
    assert tf.yahoo_interval("4h") is None
    # require_native_interval rejects a derived timeframe (defensive guard).
    with pytest.raises(ValueError, match="derived"):
        tf.require_native_interval("4h")


@pytest.mark.parametrize(
    ("timeframe", "expected_interval"),
    [("15m", "15m"), ("1h", "1h"), ("1d", "1d"), ("1w", "1wk")],
)
def test_yahoo_interval_maps_canonical_to_upstream(timeframe: str, expected_interval: str) -> None:
    assert tf.yahoo_interval(timeframe) == expected_interval
    # require_native_interval returns the same value and never None for natives.
    assert tf.require_native_interval(timeframe) == expected_interval


@pytest.mark.parametrize(
    ("timeframe", "expected"),
    [
        ("15m", timedelta(minutes=15)),
        ("1h", timedelta(hours=1)),
        ("4h", timedelta(hours=4)),
        ("1d", timedelta(days=1)),
        ("1w", timedelta(days=7)),
    ],
)
def test_bar_duration_matches_cadence(timeframe: str, expected: timedelta) -> None:
    assert tf.bar_duration(timeframe) == expected


def test_max_history_caps_intraday_and_unbounds_daily_weekly() -> None:
    assert tf.max_history("15m") == timedelta(days=60)
    assert tf.max_history("1h") == timedelta(days=730)
    # 4h inherits the 1h base's reach.
    assert tf.max_history("4h") == timedelta(days=730)
    assert tf.max_history("1d") is None
    assert tf.max_history("1w") is None


@pytest.mark.parametrize(
    ("timeframe", "intraday"),
    [("15m", True), ("1h", True), ("4h", True), ("1d", False), ("1w", False)],
)
def test_uses_intraday_timestamp_splits_sub_daily(timeframe: str, intraday: bool) -> None:
    assert tf.uses_intraday_timestamp(timeframe) is intraday


@pytest.mark.parametrize(
    ("interval", "intraday"),
    [("15m", True), ("1h", True), ("1d", False), ("1wk", False)],
)
def test_yahoo_interval_intraday_keyed_on_upstream_interval(interval: str, intraday: bool) -> None:
    # The fetcher parses the payload with the Yahoo interval string ("1wk"), not
    # the canonical timeframe ("1w") — this helper keys on the former.
    assert tf.yahoo_interval_uses_intraday_timestamp(interval) is intraday


def test_unknown_timeframe_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unknown timeframe"):
        tf.timeframe_spec("5m")
    with pytest.raises(ValueError, match="unknown timeframe"):
        tf.require_native_interval("5m")


def test_supported_label_is_cadence_ordered() -> None:
    # Sorted ascending by bar duration so the agent-facing tool docs read naturally.
    assert tf.supported_timeframes_label() == "15m, 1h, 4h, 1d, 1w"
