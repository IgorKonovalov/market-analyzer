"""Phase-1 done-when for Plan 0059: the lag-1 exogenous as-of join (ADR-0054).

The three load-bearing tests the plan names, plus supporting guards:

* **Lag-1 asserted directly** — a metric point timestamped *inside* bar ``i``
  (between its open and close) is invisible to bar ``i``'s column entry and
  visible to bar ``i+1``'s. Run against the **real** `MetricPointsRepository`
  (in-memory SQLite), so the seam this module actually ships against is the one
  under test. A point timestamped *exactly at* a bar's open is likewise
  invisible to that bar (strict ``ts < T_open``, not ``<=``).
* **Future-point perturbation** — adding a point in bar ``j``'s interior leaves
  every column entry at or before bar ``j`` byte-identical.
* **Missing series → NaN, never zero** — a registered series with no points
  yields an all-NaN column.
"""

from __future__ import annotations

import struct
from bisect import bisect_right
from collections.abc import Iterator

import pytest

from market_analyser.data.metric_series import (
    SERIES_COINGECKO_BTC_DOMINANCE,
    SERIES_FNG_VALUE,
    MetricPoint,
    UnknownMetricSeriesError,
)
from market_analyser.forecast.exogenous import (
    ExogenousColumns,
    build_exogenous_columns,
)
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repositories.metric_points import MetricPointsRepository
from tests.forecast._synthetic import synthetic_bars

BARS = synthetic_bars(20)  # daily bars, event_ts = bar open (00:00 UTC)


def _open_epoch(i: int) -> int:
    return int(BARS[i].event_ts.timestamp())


def _column_bytes(columns: ExogenousColumns) -> bytes:
    """Pack every column value into raw bytes so equality is byte-for-byte —
    plain float ``==`` would be false for NaN-vs-NaN and hide identity."""

    out = bytearray()
    for series_id in columns.series_ids:
        for value in columns.columns[series_id]:
            out += struct.pack("<d", value)
    return bytes(out)


@pytest.fixture
def repository() -> Iterator[MetricPointsRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield MetricPointsRepository(make_session_factory(engine))
    engine.dispose()


class _FakeLookup:
    """In-memory as-of lookup with the same contract as the repository:
    latest point with ``ts <= bound``, else None."""

    def __init__(self, points: dict[str, list[tuple[int, float]]]) -> None:
        self._points = {sid: sorted(pts) for sid, pts in points.items()}

    def as_of(self, series_id: str, ts: int) -> MetricPoint | None:
        pts = self._points.get(series_id, [])
        idx = bisect_right(pts, (ts, float("inf")))
        if idx == 0:
            return None
        point_ts, value = pts[idx - 1]
        return MetricPoint(series_id=series_id, ts=point_ts, value=value)


# --------------------------------------------------------------------------- #
# (a) The lag-1 guarantee, asserted directly against the real repository.      #
# --------------------------------------------------------------------------- #


def test_point_inside_bar_i_is_seen_by_bar_i_plus_1_not_bar_i(
    repository: MetricPointsRepository,
) -> None:
    """A point published *during* bar 5 (open + 1h) is not observable at bar 5
    but is observable at bar 6 — the lag-1 guarantee, per ADR-0054."""

    inside_bar_5 = _open_epoch(5) + 3600
    repository.upsert_points([MetricPoint(series_id=SERIES_FNG_VALUE, ts=inside_bar_5, value=42.0)])

    cols = build_exogenous_columns(BARS, (SERIES_FNG_VALUE,), repository)
    column = cols.columns[SERIES_FNG_VALUE]

    for i in range(6):  # bars 0..5: nothing observable strictly before their open
        assert column[i] != column[i], f"bar {i} saw a point it must not (NaN expected)"
    for i in range(6, len(BARS)):
        assert column[i] == 42.0


def test_point_exactly_at_bar_open_is_excluded_from_that_bar(
    repository: MetricPointsRepository,
) -> None:
    """The bound is strict: ``ts == T_open`` is *not* observable at that bar —
    the geometry that makes an optimistically-timestamped source harmless."""

    at_open_8 = _open_epoch(8)
    repository.upsert_points([MetricPoint(series_id=SERIES_FNG_VALUE, ts=at_open_8, value=77.0)])

    cols = build_exogenous_columns(BARS, (SERIES_FNG_VALUE,), repository)
    column = cols.columns[SERIES_FNG_VALUE]

    assert column[8] != column[8]  # NaN — invisible at the bar it is stamped on
    assert column[9] == 77.0


def test_lag1_degrades_to_yesterdays_value_for_daily_prints(
    repository: MetricPointsRepository,
) -> None:
    """A daily series printing at each bar's open joins as yesterday's value —
    the deliberate one-bar freshness surrender ADR-0054 documents."""

    repository.upsert_points(
        [
            MetricPoint(series_id=SERIES_FNG_VALUE, ts=_open_epoch(i), value=float(i))
            for i in range(len(BARS))
        ]
    )

    cols = build_exogenous_columns(BARS, (SERIES_FNG_VALUE,), repository)
    column = cols.columns[SERIES_FNG_VALUE]

    assert column[0] != column[0]  # nothing before bar 0's open
    for i in range(1, len(BARS)):
        assert column[i] == float(i - 1)


# --------------------------------------------------------------------------- #
# (b) Future-point perturbation: the past is byte-identical.                   #
# --------------------------------------------------------------------------- #


def test_future_point_perturbation_leaves_past_rows_byte_identical(
    repository: MetricPointsRepository,
) -> None:
    """Adding a point inside bar 15 leaves every column entry for bars <= 15
    byte-identical; only strictly-later bars may change."""

    repository.upsert_points(
        [
            MetricPoint(series_id=SERIES_FNG_VALUE, ts=_open_epoch(2) + 60, value=10.0),
            MetricPoint(series_id=SERIES_FNG_VALUE, ts=_open_epoch(9) + 60, value=20.0),
        ]
    )
    before = build_exogenous_columns(BARS, (SERIES_FNG_VALUE,), repository)

    perturbed_bar = 15
    repository.upsert_points(
        [
            MetricPoint(
                series_id=SERIES_FNG_VALUE,
                ts=_open_epoch(perturbed_bar) + 60,
                value=999.0,
            )
        ]
    )
    after = build_exogenous_columns(BARS, (SERIES_FNG_VALUE,), repository)

    prefix = perturbed_bar + 1  # bars 0..15 inclusive must be untouched
    before_prefix = ExogenousColumns(
        series_ids=before.series_ids,
        columns={s: before.columns[s][:prefix] for s in before.series_ids},
        last_point_ts=before.last_point_ts,
    )
    after_prefix = ExogenousColumns(
        series_ids=after.series_ids,
        columns={s: after.columns[s][:prefix] for s in after.series_ids},
        last_point_ts=after.last_point_ts,
    )
    assert _column_bytes(before_prefix) == _column_bytes(after_prefix)
    # And the perturbation is real: a later bar does see the new point.
    assert after.columns[SERIES_FNG_VALUE][perturbed_bar + 1] == 999.0


# --------------------------------------------------------------------------- #
# (c) Missing series: NaN columns, never zeros.                                #
# --------------------------------------------------------------------------- #


def test_missing_series_yields_nan_column_not_zeros(
    repository: MetricPointsRepository,
) -> None:
    repository.upsert_points(
        [MetricPoint(series_id=SERIES_FNG_VALUE, ts=_open_epoch(0) - 60, value=55.0)]
    )

    cols = build_exogenous_columns(
        BARS, (SERIES_FNG_VALUE, SERIES_COINGECKO_BTC_DOMINANCE), repository
    )

    warm = cols.columns[SERIES_FNG_VALUE]
    empty = cols.columns[SERIES_COINGECKO_BTC_DOMINANCE]
    assert all(v == 55.0 for v in warm)
    assert all(v != v for v in empty)  # NaN throughout
    assert not any(v == 0.0 for v in empty)  # and in particular never zero-filled
    assert cols.last_point_ts[SERIES_COINGECKO_BTC_DOMINANCE] is None


def test_unregistered_series_fails_loudly(repository: MetricPointsRepository) -> None:
    """The registry-is-the-schema guarantee holds through this seam."""

    with pytest.raises(UnknownMetricSeriesError):
        build_exogenous_columns(BARS, ("typo.series",), repository)


# --------------------------------------------------------------------------- #
# Supporting guards (deterministic ordering, provenance, alignment).           #
# --------------------------------------------------------------------------- #


def test_columns_align_to_bars_and_preserve_series_order() -> None:
    lookup = _FakeLookup({SERIES_FNG_VALUE: [(_open_epoch(0) - 1, 1.0)]})
    cols = build_exogenous_columns(BARS, (SERIES_COINGECKO_BTC_DOMINANCE, SERIES_FNG_VALUE), lookup)
    assert cols.series_ids == (SERIES_COINGECKO_BTC_DOMINANCE, SERIES_FNG_VALUE)
    assert cols.n_bars() == len(BARS)
    for series_id in cols.series_ids:
        assert len(cols.columns[series_id]) == len(BARS)


def test_last_point_ts_records_freshest_consumed_point() -> None:
    first_ts = _open_epoch(3) + 10
    fresh_ts = _open_epoch(12) + 10
    lookup = _FakeLookup({SERIES_FNG_VALUE: [(first_ts, 1.0), (fresh_ts, 2.0)]})
    cols = build_exogenous_columns(BARS, (SERIES_FNG_VALUE,), lookup)
    assert cols.last_point_ts[SERIES_FNG_VALUE] == fresh_ts


def test_build_is_deterministic() -> None:
    lookup = _FakeLookup(
        {
            SERIES_FNG_VALUE: [(_open_epoch(4) + 5, 3.0)],
            SERIES_COINGECKO_BTC_DOMINANCE: [],
        }
    )
    first = build_exogenous_columns(
        BARS, (SERIES_FNG_VALUE, SERIES_COINGECKO_BTC_DOMINANCE), lookup
    )
    second = build_exogenous_columns(
        BARS, (SERIES_FNG_VALUE, SERIES_COINGECKO_BTC_DOMINANCE), lookup
    )
    assert _column_bytes(first) == _column_bytes(second)
    assert first.last_point_ts == second.last_point_ts
