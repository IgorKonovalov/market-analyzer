"""Feature-set tier ladder selection (Plan 0062 phase 2, ADR-0057).

Feature sets form a fixed, ordered ladder — ``v2-full → v2-deep → v1`` — and a
forecast call trains the **richest tier whose post-join surviving-row count
clears its eligibility floor**. The floor for the exogenous tiers is
``max(2 * n_splits, MIN_TIER_ROWS)``: the ``2 * n_splits`` term is the
walk-forward crash-floor (fewer than two usable rows per fold degenerates the
validation toward unscored folds), and `MIN_TIER_ROWS` keeps a
technically-joinable-but-tiny tier from shadowing a deep one. v1 is the
terminal rung and keeps its existing (no-floor) behavior.

The honesty property Plan 0061 established holds at every rung: a skipped tier
is **stated, never silent**. `TierSelection.fallback_reason` carries the full
skip chain — each skipped tier named with its surviving-row count and the floor
it missed — and, when a non-top tier trains, which tier trained on how many
rows. It is ``None`` exactly when the top tier (v2-full) trains.

One exogenous column set is built for the union of all tiers' series and handed
to every tier builder (the builders ignore series they do not read), so the
lag-1 as-of join happens once per call and identically across tiers.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from market_analyser.data.types import Bar
from market_analyser.forecast.exogenous import (
    ExogenousColumns,
    MetricAsOfLookup,
    build_exogenous_columns,
)
from market_analyser.forecast.features import (
    EXOGENOUS_SERIES_IDS_V2,
    EXOGENOUS_SERIES_IDS_V2_DEEP,
    FEATURE_SET_ID,
    FEATURE_SET_ID_V2,
    FEATURE_SET_ID_V2_DEEP,
    FeatureRow,
    build_feature_rows,
    build_feature_rows_v2,
    build_feature_rows_v2_deep,
)
from market_analyser.forecast.result import SeriesInput

# Eligibility floor for the exogenous tiers (ADR-0057): a rough estimate of a
# meaningful training population, deliberately far above the 2*n_splits
# crash-floor. Rows, not days — timeframe-agnostic by design (the ADR carries
# the per-timeframe revisit note).
MIN_TIER_ROWS = 500


def tier_floor(n_splits: int) -> int:
    """The surviving-row floor an exogenous tier must clear to train."""

    return max(2 * n_splits, MIN_TIER_ROWS)


@dataclass(frozen=True)
class TierSelection:
    """The outcome of one richest-first ladder walk: the selected tier's frozen
    id, its feature matrix (aligned to the bars), the provenance inputs for
    exactly the series that tier consumed, and the skip chain (``None`` when
    v2-full trained — wire-absent under ``exclude_none``)."""

    feature_set_id: str
    rows: list[FeatureRow | None]
    series_inputs: tuple[SeriesInput, ...]
    fallback_reason: str | None


def select_feature_tier(
    bars: Sequence[Bar],
    metric_lookup: MetricAsOfLookup,
    *,
    n_splits: int,
) -> TierSelection:
    """Walk the ladder richest-first and return the first eligible tier.

    Deterministic: the tier order is fixed, the join reads the store through
    the same lag-1 ``as_of`` bound for every tier, and the skip chain is built
    by iteration over that fixed order.
    """

    exogenous = build_exogenous_columns(bars, EXOGENOUS_SERIES_IDS_V2, metric_lookup)
    floor = tier_floor(n_splits)
    skips: list[str] = []

    for tier_name, feature_set_id, series_ids, builder in (
        ("v2-full", FEATURE_SET_ID_V2, EXOGENOUS_SERIES_IDS_V2, build_feature_rows_v2),
        (
            "v2-deep",
            FEATURE_SET_ID_V2_DEEP,
            EXOGENOUS_SERIES_IDS_V2_DEEP,
            build_feature_rows_v2_deep,
        ),
    ):
        rows = builder(bars, exogenous)
        n_usable = sum(1 for row in rows if row is not None)
        if n_usable >= floor:
            return TierSelection(
                feature_set_id=feature_set_id,
                rows=rows,
                series_inputs=_series_inputs(exogenous, series_ids),
                fallback_reason=(
                    "; ".join((*skips, f"trained {tier_name} ({n_usable} rows)")) if skips else None
                ),
            )
        skips.append(
            f"{tier_name} unavailable: {n_usable} of {len(bars)} bars "
            f"survived the join (floor {floor})"
        )

    return TierSelection(
        feature_set_id=FEATURE_SET_ID,
        rows=build_feature_rows(bars),
        series_inputs=(),
        fallback_reason="; ".join(skips),
    )


def _series_inputs(
    exogenous: ExogenousColumns, series_ids: tuple[str, ...]
) -> tuple[SeriesInput, ...]:
    """Provenance rows for exactly the selected tier's consumed series, in the
    tier's own series order."""

    return tuple(
        SeriesInput(series_id=series_id, last_point_ts=exogenous.last_point_ts[series_id])
        for series_id in series_ids
    )


__all__ = [
    "MIN_TIER_ROWS",
    "TierSelection",
    "select_feature_tier",
    "tier_floor",
]
