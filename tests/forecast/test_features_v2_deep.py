"""Phase-1 done-when for Plan 0062: the v2-deep feature set (ADR-0057).

The four criteria the plan names:

* **(a) Truncation invariance** — the deep set is truncation-invariant per row
  (the ADR-0030 invariant-1 guard, same shape as v2-full's spot test).
* **(b) Id pins** — v1's and v2-full's ``FEATURE_SET_ID``s are pinned as
  literals and unmoved by this plan; the deep id is pinned as a third literal.
* **(c) The defining behavior** — with dominance and OI empty but
  F&G/funding/MVRV seeded, deep rows survive from the seeded intersection
  onward while ``build_feature_rows_v2`` yields zero usable rows on the SAME
  inputs (the conjunctive veto this tier exists to escape).
* **(d) Perturbation** — a future-timestamped point in any deep series is
  byte-invisible to the matrix (the ADR-0054 perturbation test extended from
  the column join to the deep builder, against the real repository).
"""

from __future__ import annotations

import struct
from collections.abc import Iterator, Sequence

import pytest

from market_analyser.data.metric_series import MetricPoint
from market_analyser.forecast.exogenous import (
    ExogenousColumns,
    build_exogenous_columns,
)
from market_analyser.forecast.features import (
    EXOGENOUS_SERIES_IDS_V2,
    EXOGENOUS_SERIES_IDS_V2_DEEP,
    FEATURE_NAMES,
    FEATURE_NAMES_V2,
    FEATURE_NAMES_V2_DEEP,
    FEATURE_SET_ID,
    FEATURE_SET_ID_V2,
    FEATURE_SET_ID_V2_DEEP,
    FeatureRow,
    build_feature_rows_v2,
    build_feature_rows_v2_deep,
)
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repositories.metric_points import MetricPointsRepository
from tests.forecast._synthetic import synthetic_bars

# The two literals this plan must not move, plus the deep id it introduces.
PINNED_FEATURE_SET_ID_V1 = "49c020d0794fd2a7"
PINNED_FEATURE_SET_ID_V2 = "2fb15f47d51cbafa"
PINNED_FEATURE_SET_ID_V2_DEEP = "3d8643321ac2cec3"

# The three features the deep tier drops — fed by the accrual-only series.
ACCRUAL_FED_FEATURES = ("btc_dominance", "dominance_delta_7", "oi_delta_7")

EXPECTED_DEEP_EXTRAS = (
    "halving_phase",
    "days_since_halving",
    "mayer_multiple",
    "dist_200w_ma",
    "fng_value",
    "fng_delta_7",
    "funding_rate",
    "mvrv",
)

# Deep rows need 1400 daily closes for dist_200w_ma (same as v2-full); the
# defined tail (~100 rows) keeps the build fast while exercising every feature.
N_BARS = 1500
BARS = synthetic_bars(N_BARS)

# Distinct per-series values so a joined feature is attributable to its series.
DEEP_VALUES = {sid: 10.0 + 1.0 * i for i, sid in enumerate(EXOGENOUS_SERIES_IDS_V2_DEEP)}


class _StaticLookup:
    """Every configured series has one point at ``ts``; unconfigured series have
    none — the shape that leaves dominance/OI columns all-NaN."""

    def __init__(self, ts: int, values: dict[str, float]) -> None:
        self._ts = ts
        self._values = values

    def as_of(self, series_id: str, ts: int) -> MetricPoint | None:
        if series_id not in self._values or ts < self._ts:
            return None
        return MetricPoint(series_id=series_id, ts=self._ts, value=self._values[series_id])


def _deep_seeded_full_columns(bars: Sequence = BARS) -> ExogenousColumns:  # type: ignore[type-arg]
    """Columns over ALL FIVE v2 series with only the three deep series seeded —
    the live 2026-07-06 store shape (accrual-only series empty for every
    historical bar), and the shared column set the ladder hands both builders."""

    first_open = int(bars[0].event_ts.timestamp())
    lookup = _StaticLookup(first_open - 60, DEEP_VALUES)
    return build_exogenous_columns(bars, EXOGENOUS_SERIES_IDS_V2, lookup)


def _matrix_bytes(rows: list[FeatureRow | None], upto: int) -> bytes:
    """Pack rows[0..=upto] (values + definedness) into raw bytes so prefix
    equality is byte-for-byte, the test_exogenous.py pattern lifted to rows."""

    out = bytearray()
    for row in rows[: upto + 1]:
        if row is None:
            out += b"\x00"
        else:
            out += b"\x01"
            for value in row.values:
                out += struct.pack("<d", value)
    return bytes(out)


@pytest.fixture
def repository() -> Iterator[MetricPointsRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield MetricPointsRepository(make_session_factory(engine))
    engine.dispose()


# --------------------------------------------------------------------------- #
# (b) The frozen deep tuple and the three pinned ids.                          #
# --------------------------------------------------------------------------- #


def test_deep_names_are_frozen_and_drop_exactly_the_accrual_fed_features() -> None:
    expected = (*FEATURE_NAMES, *EXPECTED_DEEP_EXTRAS)
    assert expected == FEATURE_NAMES_V2_DEEP
    assert len(FEATURE_NAMES_V2_DEEP) == 24
    # Deep is v2-full minus exactly the three accrual-fed features, order kept.
    assert set(FEATURE_NAMES_V2) - set(FEATURE_NAMES_V2_DEEP) == set(ACCRUAL_FED_FEATURES)
    derived = tuple(name for name in FEATURE_NAMES_V2 if name not in ACCRUAL_FED_FEATURES)
    assert derived == FEATURE_NAMES_V2_DEEP


def test_deep_series_are_the_three_with_deep_history() -> None:
    assert len(EXOGENOUS_SERIES_IDS_V2_DEEP) == 3
    assert set(EXOGENOUS_SERIES_IDS_V2_DEEP) < set(EXOGENOUS_SERIES_IDS_V2)


def test_three_feature_set_ids_are_pinned_literals_and_distinct() -> None:
    """v1's and v2-full's ids are unmoved by this plan; the deep id is a third
    literal — every model stays auditable to its exact input set (ADR-0040)."""

    assert FEATURE_SET_ID == PINNED_FEATURE_SET_ID_V1
    assert FEATURE_SET_ID_V2 == PINNED_FEATURE_SET_ID_V2
    assert FEATURE_SET_ID_V2_DEEP == PINNED_FEATURE_SET_ID_V2_DEEP
    assert len({FEATURE_SET_ID, FEATURE_SET_ID_V2, FEATURE_SET_ID_V2_DEEP}) == 3


# --------------------------------------------------------------------------- #
# (c) The defining behavior: deep survives where the conjunctive full set dies.#
# --------------------------------------------------------------------------- #


def test_deep_rows_survive_where_v2_full_yields_zero_on_the_same_inputs() -> None:
    columns = _deep_seeded_full_columns()

    full_rows = build_feature_rows_v2(BARS, columns)
    assert all(row is None for row in full_rows)  # dominance/OI veto every bar

    deep_rows = build_feature_rows_v2_deep(BARS, columns)
    defined = [i for i, row in enumerate(deep_rows) if row is not None]
    assert defined, "expected deep rows to survive with only the deep series seeded"
    # The surviving rows carry the real joined values, never a fabricated fill.
    fng_index = FEATURE_NAMES_V2_DEEP.index("fng_value")
    first = deep_rows[defined[0]]
    assert first is not None
    assert first.values[fng_index] == DEEP_VALUES[EXOGENOUS_SERIES_IDS_V2_DEEP[0]]
    for row in deep_rows:
        if row is not None:
            assert len(row.values) == len(FEATURE_NAMES_V2_DEEP)


def test_deep_rows_start_at_the_seeded_intersection() -> None:
    """A deep series first observable mid-window moves the survival boundary to
    the intersection of the three deep series — the row-drop rule is the same
    conjunctive join, just over fewer series."""

    first_open = int(BARS[0].event_ts.timestamp())
    late_series = EXOGENOUS_SERIES_IDS_V2_DEEP[1]  # funding: no delta lookback
    late_ts = int(BARS[1420].event_ts.timestamp()) + 60

    class _MixedLookup:
        def as_of(self, series_id: str, ts: int) -> MetricPoint | None:
            start = late_ts if series_id == late_series else first_open - 60
            if series_id not in DEEP_VALUES or ts < start:
                return None
            return MetricPoint(series_id=series_id, ts=start, value=DEEP_VALUES[series_id])

    columns = build_exogenous_columns(BARS, EXOGENOUS_SERIES_IDS_V2, _MixedLookup())
    deep_rows = build_feature_rows_v2_deep(BARS, columns)
    first_defined = next(i for i, row in enumerate(deep_rows) if row is not None)
    # The point in bar 1420's interior becomes observable at bar 1421 (lag-1);
    # funding has no delta feature, so the row survives immediately there.
    assert first_defined == 1421
    for i in range(first_defined):
        assert deep_rows[i] is None


# --------------------------------------------------------------------------- #
# (a) Truncation invariance carries to the deep tier.                          #
# --------------------------------------------------------------------------- #


def test_deep_rows_are_truncation_invariant_at_spot_indices() -> None:
    columns = _deep_seeded_full_columns()
    full = build_feature_rows_v2_deep(BARS, columns)
    defined = [i for i, row in enumerate(full) if row is not None]
    for i in (defined[0], defined[len(defined) // 2], defined[-1]):
        truncated_columns = ExogenousColumns(
            series_ids=columns.series_ids,
            columns={s: columns.columns[s][: i + 1] for s in columns.series_ids},
            last_point_ts=columns.last_point_ts,
        )
        truncated = build_feature_rows_v2_deep(BARS[: i + 1], truncated_columns)
        full_row = full[i]
        trunc_row = truncated[i]
        assert full_row is not None and trunc_row is not None
        assert trunc_row.values == full_row.values


# --------------------------------------------------------------------------- #
# (d) Future-point perturbation: the past matrix is byte-identical.            #
# --------------------------------------------------------------------------- #


def test_future_point_in_a_deep_series_is_byte_invisible_to_past_rows(
    repository: MetricPointsRepository,
) -> None:
    """Adding a point inside bar 1450 leaves every deep row at or before bar
    1450 byte-identical; only strictly-later bars may change — run against the
    real repository, so the seam the ladder ships against is the one under
    test."""

    first_open = int(BARS[0].event_ts.timestamp())
    repository.upsert_points(
        [
            MetricPoint(series_id=series_id, ts=first_open - 60, value=DEEP_VALUES[series_id])
            for series_id in EXOGENOUS_SERIES_IDS_V2_DEEP
        ]
    )
    before = build_feature_rows_v2_deep(
        BARS, build_exogenous_columns(BARS, EXOGENOUS_SERIES_IDS_V2_DEEP, repository)
    )

    perturbed_bar = 1450
    perturbed_series = EXOGENOUS_SERIES_IDS_V2_DEEP[0]
    repository.upsert_points(
        [
            MetricPoint(
                series_id=perturbed_series,
                ts=int(BARS[perturbed_bar].event_ts.timestamp()) + 60,
                value=999.0,
            )
        ]
    )
    after = build_feature_rows_v2_deep(
        BARS, build_exogenous_columns(BARS, EXOGENOUS_SERIES_IDS_V2_DEEP, repository)
    )

    assert _matrix_bytes(before, perturbed_bar) == _matrix_bytes(after, perturbed_bar)
    # And the perturbation is real: the next bar's row does see the new point.
    fng_index = FEATURE_NAMES_V2_DEEP.index("fng_value")
    later = after[perturbed_bar + 1]
    assert later is not None
    assert later.values[fng_index] == 999.0
