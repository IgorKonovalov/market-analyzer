"""Phase-2 done-when for Plan 0062 (helper half): the richest-first tier
ladder (ADR-0057).

Covered here, against `select_feature_tier` directly:

* all five series warm past the floor → **v2-full** trains and the skip chain
  is ``None`` (wire-absent downstream under ``exclude_none``);
* only the deep series warm → **v2-deep** trains, ``series_inputs`` names
  exactly the three deep series, and the reason states the v2-full skip with
  its surviving-row count plus which tier trained on how many rows (the plan's
  example string shape);
* **the floor is real, tested at the boundary**: a tier surviving exactly
  ``MIN_TIER_ROWS`` rows trains; one row fewer — still far above the
  ``2 * n_splits`` crash-floor — is skipped;
* both exogenous tiers starved → **v1** with the full two-tier skip chain.

The tool-level half (provenance on the wire, no Plan 0061 regressions) lives
in ``tests/api/test_forecast_tool.py``.
"""

from __future__ import annotations

from collections.abc import Sequence

from market_analyser.data.metric_series import MetricPoint
from market_analyser.data.types import Bar
from market_analyser.forecast.exogenous import build_exogenous_columns
from market_analyser.forecast.features import (
    EXOGENOUS_SERIES_IDS_V2,
    EXOGENOUS_SERIES_IDS_V2_DEEP,
    FEATURE_SET_ID,
    FEATURE_SET_ID_V2,
    FEATURE_SET_ID_V2_DEEP,
    build_feature_rows_v2_deep,
)
from market_analyser.forecast.tiers import MIN_TIER_ROWS, select_feature_tier, tier_floor
from tests.forecast._synthetic import synthetic_bars

N_SPLITS = 5  # tier_floor(5) == MIN_TIER_ROWS — the constant is the binding floor


class _StaticLookup:
    """Every configured series has one point at ``ts``; unconfigured series
    have none."""

    def __init__(self, ts: int, values: dict[str, float]) -> None:
        self._ts = ts
        self._values = values

    def as_of(self, series_id: str, ts: int) -> MetricPoint | None:
        if series_id not in self._values or ts < self._ts:
            return None
        return MetricPoint(series_id=series_id, ts=self._ts, value=self._values[series_id])


def _lookup_for(bars: Sequence[Bar], series_ids: Sequence[str]) -> _StaticLookup:
    first_open = int(bars[0].event_ts.timestamp())
    values = {sid: 10.0 + 1.0 * i for i, sid in enumerate(series_ids)}
    return _StaticLookup(first_open - 60, values)


def _first_defined_deep_index() -> int:
    """The deep tier's warm-up length on the synthetic series (dominated by the
    200W-MA's 1400-close requirement) — computed, not hardcoded, so the
    boundary tests below construct exact surviving-row counts."""

    bars = synthetic_bars(1450)
    columns = build_exogenous_columns(
        bars, EXOGENOUS_SERIES_IDS_V2, _lookup_for(bars, EXOGENOUS_SERIES_IDS_V2_DEEP)
    )
    rows = build_feature_rows_v2_deep(bars, columns)
    return next(i for i, row in enumerate(rows) if row is not None)


FIRST_DEFINED = _first_defined_deep_index()


def test_tier_floor_is_the_max_of_crash_floor_and_min_tier_rows() -> None:
    assert MIN_TIER_ROWS == 500
    assert tier_floor(5) == MIN_TIER_ROWS  # 2*5 << 500: the named constant binds
    assert tier_floor(300) == 600  # pathological split counts keep the crash-floor


def test_all_series_warm_selects_v2_full_and_no_fallback_reason() -> None:
    bars = synthetic_bars(FIRST_DEFINED + MIN_TIER_ROWS + 50)
    selection = select_feature_tier(
        bars, _lookup_for(bars, EXOGENOUS_SERIES_IDS_V2), n_splits=N_SPLITS
    )

    assert selection.feature_set_id == FEATURE_SET_ID_V2
    assert selection.fallback_reason is None
    assert tuple(s.series_id for s in selection.series_inputs) == EXOGENOUS_SERIES_IDS_V2
    assert sum(1 for row in selection.rows if row is not None) >= MIN_TIER_ROWS


def test_deep_series_only_selects_v2_deep_and_states_the_v2_full_skip() -> None:
    """The defining ladder behavior at tool level: dominance/OI empty vetoes
    v2-full (0 survivors), the deep tier trains, and the reason names the skip
    with its row count — the plan's example string shape, exactly."""

    bars = synthetic_bars(FIRST_DEFINED + MIN_TIER_ROWS + 50)
    selection = select_feature_tier(
        bars, _lookup_for(bars, EXOGENOUS_SERIES_IDS_V2_DEEP), n_splits=N_SPLITS
    )

    assert selection.feature_set_id == FEATURE_SET_ID_V2_DEEP
    n_deep = sum(1 for row in selection.rows if row is not None)
    assert n_deep >= MIN_TIER_ROWS
    assert selection.fallback_reason == (
        f"v2-full unavailable: 0 of {len(bars)} bars survived the join "
        f"(floor {MIN_TIER_ROWS}); trained v2-deep ({n_deep} rows)"
    )
    assert tuple(s.series_id for s in selection.series_inputs) == EXOGENOUS_SERIES_IDS_V2_DEEP
    first_open = int(bars[0].event_ts.timestamp())
    assert all(s.last_point_ts == first_open - 60 for s in selection.series_inputs)


def test_floor_boundary_exactly_min_rows_trains_one_fewer_skips() -> None:
    """Done-when (d): the floor is real. At exactly MIN_TIER_ROWS surviving
    rows the deep tier trains; at MIN_TIER_ROWS - 1 — still ~50x the
    2*n_splits crash-floor, so only the named constant can be doing the
    gating — the tier is skipped and the call lands on v1."""

    at_floor = synthetic_bars(FIRST_DEFINED + MIN_TIER_ROWS)
    selection = select_feature_tier(
        at_floor, _lookup_for(at_floor, EXOGENOUS_SERIES_IDS_V2_DEEP), n_splits=N_SPLITS
    )
    assert sum(1 for row in selection.rows if row is not None) == MIN_TIER_ROWS
    assert selection.feature_set_id == FEATURE_SET_ID_V2_DEEP

    below_floor = synthetic_bars(FIRST_DEFINED + MIN_TIER_ROWS - 1)
    selection = select_feature_tier(
        below_floor, _lookup_for(below_floor, EXOGENOUS_SERIES_IDS_V2_DEEP), n_splits=N_SPLITS
    )
    assert selection.feature_set_id == FEATURE_SET_ID
    assert selection.series_inputs == ()
    n_skipped = MIN_TIER_ROWS - 1
    assert n_skipped >= 2 * N_SPLITS  # joinable for the walk-forward, yet skipped
    assert selection.fallback_reason == (
        f"v2-full unavailable: 0 of {len(below_floor)} bars survived the join "
        f"(floor {MIN_TIER_ROWS}); "
        f"v2-deep unavailable: {n_skipped} of {len(below_floor)} bars survived the join "
        f"(floor {MIN_TIER_ROWS})"
    )


def test_starved_store_falls_to_v1_with_the_full_two_tier_skip_chain() -> None:
    bars = synthetic_bars(220)
    selection = select_feature_tier(bars, _lookup_for(bars, ()), n_splits=N_SPLITS)

    assert selection.feature_set_id == FEATURE_SET_ID
    assert selection.series_inputs == ()
    assert selection.fallback_reason == (
        f"v2-full unavailable: 0 of 220 bars survived the join (floor {MIN_TIER_ROWS}); "
        f"v2-deep unavailable: 0 of 220 bars survived the join (floor {MIN_TIER_ROWS})"
    )
    # v1 is the terminal rung: rows still come back (no floor of its own).
    assert any(row is not None for row in selection.rows)


def test_selection_is_deterministic() -> None:
    bars = synthetic_bars(220)
    first = select_feature_tier(bars, _lookup_for(bars, ()), n_splits=N_SPLITS)
    second = select_feature_tier(bars, _lookup_for(bars, ()), n_splits=N_SPLITS)
    assert first.feature_set_id == second.feature_set_id
    assert first.fallback_reason == second.fallback_reason
    assert first.series_inputs == second.series_inputs
    assert first.rows == second.rows
