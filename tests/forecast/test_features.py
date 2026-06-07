"""Phase-1 done-when for Plan 0036: the causal feature pipeline.

The two load-bearing tests the plan names:

* **Anti-lookahead / no leakage** — truncating the bar series at ``i`` and
  rebuilding yields a row ``i`` that is *byte-identical* to the full-series row
  ``i``. This is the structural defence against a centered indicator, a
  full-series normalisation, or any feature that bleeds the future into the past;
  it is run for every defined row, not as a one-off (plan Risk: feature leakage).
* **Frozen feature order** — `FEATURE_NAMES` is pinned against a literal expected
  tuple, so adding or reordering a feature without updating the frozen list fails
  here; and every emitted row carries exactly that many values in that order.

Plus supporting guards: no label column is present in the feature set, rows are
``None`` only as a leading prefix (no interior gaps), and the build is
deterministic.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from market_analyser.data.types import Bar
from market_analyser.forecast.features import (
    FEATURE_NAMES,
    FEATURE_SET_ID,
    build_feature_rows,
    feature_names,
)

# The exact, ordered feature set this plan ships. If you change the pipeline's
# columns you must change this literal too — that is the point of the test.
EXPECTED_FEATURE_NAMES = (
    "ret_1",
    "ret_5",
    "rsi_14",
    "macd",
    "macd_signal",
    "macd_hist",
    "bb_pct_b",
    "atr_pct",
    "adx",
    "plus_di",
    "minus_di",
    "supertrend_dir",
    "ema20_dist",
    "ema50_dist",
    "donchian_pos",
    "rel_volume",
)


def _synthetic_bars(n: int) -> list[Bar]:
    """A deterministic, non-degenerate OHLCV series. The sinusoid + drift keeps the
    Bollinger/Donchian bands from collapsing (so rows become defined) and the
    closed-form construction keeps it reproducible without a committed fixture."""

    bars: list[Bar] = []
    for i in range(n):
        base = 100.0 + 0.15 * i + 8.0 * math.sin(i / 6.0)
        delta = 1.5 * math.sin(i / 3.0 + 1.0)
        open_ = base
        close = base + delta
        spread = 0.5 + 0.4 * abs(math.cos(i / 5.0))
        high = max(open_, close) + spread
        low = min(open_, close) - spread
        volume = 1_000_000.0 + 50_000.0 * math.sin(i / 4.0) + 1_000.0 * i
        bars.append(
            Bar(
                symbol="SYN",
                timeframe="1d",
                event_ts=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                source="synthetic",
            )
        )
    return bars


BARS = _synthetic_bars(150)
ROWS = build_feature_rows(BARS)


def test_feature_order_is_frozen() -> None:
    """The frozen list is exactly the expected tuple, and `feature_names()` agrees.
    Adding/reordering a feature without updating EXPECTED_FEATURE_NAMES fails here."""

    assert FEATURE_NAMES == EXPECTED_FEATURE_NAMES
    assert feature_names() == EXPECTED_FEATURE_NAMES


def test_every_row_has_one_value_per_feature() -> None:
    for row in ROWS:
        if row is None:
            continue
        assert len(row.values) == len(FEATURE_NAMES)


def test_no_label_column_in_feature_set() -> None:
    """The feature set must not carry the prediction target. Guards against the
    label (phase 2) leaking in as a feature."""

    forbidden = {"label", "target", "y", "direction", "next_return", "fwd_return"}
    assert forbidden.isdisjoint(FEATURE_NAMES)


def test_rows_are_defined_only_as_a_leading_prefix() -> None:
    """`None` rows form a contiguous leading run (indicators warming up); once the
    matrix starts there are no interior gaps."""

    first_defined = next((i for i, r in enumerate(ROWS) if r is not None), None)
    assert first_defined is not None, "no defined rows on a 150-bar series"
    assert all(ROWS[i] is None for i in range(first_defined))
    assert all(ROWS[i] is not None for i in range(first_defined, len(ROWS)))


def test_anti_lookahead_truncation_invariance() -> None:
    """The load-bearing leakage guard: for every defined bar ``i``, building on
    ``bars[0..=i]`` reproduces the full-series row ``i`` byte-identically. Run for
    every defined row, not a sampled few — a single leaking feature would surface
    at the bar it first reads the future."""

    full = build_feature_rows(BARS)
    for i, full_row in enumerate(full):
        if full_row is None:
            continue
        truncated = build_feature_rows(BARS[: i + 1])
        assert len(truncated) == i + 1
        trunc_row = truncated[i]
        assert trunc_row is not None, f"row {i} defined on full series but not truncated at {i}"
        assert trunc_row.bar_index == full_row.bar_index == i
        assert trunc_row.event_ts == full_row.event_ts
        # Byte-identical: exact float-tuple equality, not a tolerance compare.
        assert trunc_row.values == full_row.values, f"row {i} differs when the future is truncated"


def test_build_is_deterministic() -> None:
    assert build_feature_rows(BARS) == build_feature_rows(BARS)


def test_feature_set_id_is_stable_and_order_sensitive() -> None:
    """The id is a deterministic hash of the ordered names — stable across runs,
    and different if the order changes (the property phase 4's model_version relies
    on)."""

    from market_analyser.forecast.features import _compute_feature_set_id

    assert _compute_feature_set_id(FEATURE_NAMES) == FEATURE_SET_ID
    reordered = (FEATURE_NAMES[1], FEATURE_NAMES[0], *FEATURE_NAMES[2:])
    assert _compute_feature_set_id(reordered) != FEATURE_SET_ID
