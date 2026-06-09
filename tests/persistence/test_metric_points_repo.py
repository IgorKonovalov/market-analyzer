"""Plan 0055 phase 1 — the `metric_points` repository (ADR-0051).

Done-when claims pinned here:
(a) `as_of` returns the latest point at-or-before the bound and NEVER a later
    one — asserted with a point one second past the bound;
(b) an unregistered `series_id` is rejected at the repository boundary (reads
    and writes both);
(c) upsert of an existing `(series_id, ts)` with a different value is refused
    outside the explicit `refresh` path (ADR-0051 immutability), while a
    same-value re-upsert is an idempotent no-op.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session, sessionmaker

from market_analyser.data import metric_series
from market_analyser.data.metric_series import (
    MetricPoint,
    MetricSeriesSpec,
    UnknownMetricSeriesError,
)
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repositories.metric_points import (
    MetricPointConflictError,
    MetricPointsRepository,
)

TEST_SERIES = "test.metric"
OTHER_SERIES = "test.other"


@pytest.fixture(autouse=True)
def _register_test_series(monkeypatch: pytest.MonkeyPatch) -> None:
    for series_id in (TEST_SERIES, OTHER_SERIES):
        monkeypatch.setitem(
            metric_series.SERIES_REGISTRY,
            series_id,
            MetricSeriesSpec(
                series_id=series_id,
                description="test-only series",
                source="test",
                cadence="test",
            ),
        )


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def repo(session_factory: sessionmaker[Session]) -> MetricPointsRepository:
    return MetricPointsRepository(session_factory)


def _point(ts: int, value: float, series_id: str = TEST_SERIES) -> MetricPoint:
    return MetricPoint(series_id=series_id, ts=ts, value=value)


# --- (a) as_of: latest at-or-before the bound, never later -------------------


def test_as_of_returns_latest_point_at_or_before_bound(repo: MetricPointsRepository) -> None:
    repo.upsert_points([_point(1_000, 10.0), _point(2_000, 20.0), _point(3_000, 30.0)])

    result = repo.as_of(TEST_SERIES, 2_500)

    assert result is not None
    assert result.ts == 2_000
    assert result.value == 20.0


def test_as_of_bound_is_inclusive(repo: MetricPointsRepository) -> None:
    repo.upsert_points([_point(1_000, 10.0), _point(2_000, 20.0)])

    result = repo.as_of(TEST_SERIES, 2_000)

    assert result is not None
    assert result.ts == 2_000
    assert result.value == 20.0


def test_as_of_never_returns_a_point_one_second_past_the_bound(
    repo: MetricPointsRepository,
) -> None:
    """The plan's lookahead probe: a stored point at bound+1s must be invisible."""
    bound = 5_000
    repo.upsert_points([_point(4_000, 40.0), _point(bound + 1, 99.0)])

    result = repo.as_of(TEST_SERIES, bound)

    assert result is not None
    assert result.ts == 4_000
    assert result.value == 40.0


def test_as_of_returns_none_when_nothing_at_or_before_bound(
    repo: MetricPointsRepository,
) -> None:
    repo.upsert_points([_point(9_000, 90.0)])

    assert repo.as_of(TEST_SERIES, 8_999) is None


def test_as_of_does_not_cross_series(repo: MetricPointsRepository) -> None:
    repo.upsert_points([_point(1_000, 10.0, series_id=OTHER_SERIES)])

    assert repo.as_of(TEST_SERIES, 2_000) is None


# --- range: inclusive bounds, ascending ts -----------------------------------


def test_range_is_inclusive_both_ends_and_ts_ascending(repo: MetricPointsRepository) -> None:
    repo.upsert_points(
        [
            _point(3_000, 30.0),
            _point(1_000, 10.0),
            _point(2_000, 20.0),
            _point(4_000, 40.0),
        ],
    )

    result = repo.range(TEST_SERIES, 1_000, 3_000)

    assert [(p.ts, p.value) for p in result] == [(1_000, 10.0), (2_000, 20.0), (3_000, 30.0)]


def test_range_rejects_inverted_bounds(repo: MetricPointsRepository) -> None:
    with pytest.raises(ValueError, match="start"):
        repo.range(TEST_SERIES, 2_000, 1_000)


# --- (b) unregistered series ids fail loudly at the boundary -----------------


def test_upsert_rejects_unregistered_series_id_and_writes_nothing(
    repo: MetricPointsRepository,
) -> None:
    points = [_point(1_000, 10.0), _point(2_000, 20.0, series_id="nope.unregistered")]

    with pytest.raises(UnknownMetricSeriesError, match=r"nope\.unregistered"):
        repo.upsert_points(points)

    # The registered half of the rejected batch must not have landed either.
    assert repo.range(TEST_SERIES, 0, 10_000) == []


def test_range_rejects_unregistered_series_id(repo: MetricPointsRepository) -> None:
    with pytest.raises(UnknownMetricSeriesError):
        repo.range("nope.unregistered", 0, 1)


def test_as_of_rejects_unregistered_series_id(repo: MetricPointsRepository) -> None:
    with pytest.raises(UnknownMetricSeriesError):
        repo.as_of("nope.unregistered", 1)


# --- (c) immutability: conflicting re-upsert refused outside refresh ---------


def test_same_value_reupsert_is_idempotent_noop(repo: MetricPointsRepository) -> None:
    assert repo.upsert_points([_point(1_000, 10.0)]) == 1
    assert repo.upsert_points([_point(1_000, 10.0)]) == 0

    result = repo.range(TEST_SERIES, 0, 10_000)
    assert [(p.ts, p.value) for p in result] == [(1_000, 10.0)]


def test_conflicting_value_is_refused_without_refresh(repo: MetricPointsRepository) -> None:
    repo.upsert_points([_point(1_000, 10.0)])

    with pytest.raises(MetricPointConflictError, match="refresh"):
        repo.upsert_points([_point(1_000, 11.0)])

    stored = repo.as_of(TEST_SERIES, 1_000)
    assert stored is not None
    assert stored.value == 10.0  # the original survives the refused overwrite


def test_refresh_path_overwrites_explicitly(repo: MetricPointsRepository) -> None:
    repo.upsert_points([_point(1_000, 10.0)])

    inserted = repo.upsert_points([_point(1_000, 11.0)], refresh=True)

    assert inserted == 0  # an overwrite is not a new point
    stored = repo.as_of(TEST_SERIES, 1_000)
    assert stored is not None
    assert stored.value == 11.0


def test_refused_conflict_batch_writes_nothing(repo: MetricPointsRepository) -> None:
    repo.upsert_points([_point(1_000, 10.0)])

    with pytest.raises(MetricPointConflictError):
        repo.upsert_points([_point(2_000, 20.0), _point(1_000, 99.0)])

    result = repo.range(TEST_SERIES, 0, 10_000)
    assert [(p.ts, p.value) for p in result] == [(1_000, 10.0)]
