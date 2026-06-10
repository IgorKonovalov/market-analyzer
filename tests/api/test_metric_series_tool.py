"""Plan 0055 phase 4 — the paged `get_metric_series` MCP tool (ADR-0046).

Done-when claim (c) pinned here: the tool pages per ADR-0046 with the typed
`too_large` envelope, asserted at the cap boundary — exactly `MAX_METRIC_POINTS`
points fit in one un-flagged page, one more flips `partial_reason="too_large"`
with an honest total and a paging hint, and offset paging walks the remainder
deterministically. Unknown series ids are rejected loudly (the registry is the
schema, ADR-0051).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import anyio
import pytest
from mcp.server.fastmcp import FastMCP
from sqlalchemy.orm import Session, sessionmaker

from market_analyser.api.mcp_tools.metric_series import (
    MAX_METRIC_POINTS,
    _get_metric_series_response,
    register_get_metric_series,
)
from market_analyser.data.metric_series import (
    SERIES_FNG_VALUE,
    MetricPoint,
    UnknownMetricSeriesError,
)
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repositories.metric_points import MetricPointsRepository

_T0 = 1_517_443_200  # 2018-02-01T00:00:00Z
_DAY = 86_400


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def store(session_factory: sessionmaker[Session]) -> MetricPointsRepository:
    return MetricPointsRepository(session_factory)


def _seed(store: MetricPointsRepository, n: int) -> None:
    store.upsert_points(
        [
            MetricPoint(series_id=SERIES_FNG_VALUE, ts=_T0 + i * _DAY, value=float(i % 101))
            for i in range(n)
        ],
    )


def _call(
    store: MetricPointsRepository,
    *,
    series_id: str = SERIES_FNG_VALUE,
    start: int = 0,
    end: int | None = None,
    offset: int = 0,
    max_points: int | None = None,
) -> Any:
    return _get_metric_series_response(
        store=store,
        series_id=series_id,
        start=start,
        end=end,
        offset=offset,
        max_points=max_points,
    )


# --- basic read: ascending points, inclusive window --------------------------------


def test_returns_stored_points_ascending_with_values(store: MetricPointsRepository) -> None:
    _seed(store, 5)

    resp = _call(store)

    assert resp.series_id == SERIES_FNG_VALUE
    assert [p.ts for p in resp.points] == [_T0 + i * _DAY for i in range(5)]
    assert [p.value for p in resp.points] == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert resp.partial_reason is None
    assert resp.message is None
    assert resp.total_available == 5
    assert resp.returned == 5


def test_window_is_inclusive_on_both_ends(store: MetricPointsRepository) -> None:
    _seed(store, 5)

    resp = _call(store, start=_T0 + _DAY, end=_T0 + 3 * _DAY)

    assert [p.ts for p in resp.points] == [_T0 + _DAY, _T0 + 2 * _DAY, _T0 + 3 * _DAY]
    assert resp.total_available == 3


# --- (c) the cap boundary -----------------------------------------------------------


def test_exactly_max_points_is_one_unflagged_page(store: MetricPointsRepository) -> None:
    _seed(store, MAX_METRIC_POINTS)

    resp = _call(store)

    assert resp.returned == MAX_METRIC_POINTS
    assert resp.total_available == MAX_METRIC_POINTS
    assert resp.partial_reason is None
    assert resp.message is None


def test_one_past_the_cap_flags_too_large_with_paging_hint(
    store: MetricPointsRepository,
) -> None:
    total = MAX_METRIC_POINTS + 1
    _seed(store, total)

    resp = _call(store)

    assert resp.returned == MAX_METRIC_POINTS
    assert len(resp.points) == MAX_METRIC_POINTS
    assert resp.total_available == total
    assert resp.partial_reason == "too_large"
    assert resp.message is not None
    assert str(total) in resp.message
    assert f"offset={MAX_METRIC_POINTS}" in resp.message
    # The first page is the head of the series — deterministic slicing.
    assert resp.points[0].ts == _T0
    assert resp.points[-1].ts == _T0 + (MAX_METRIC_POINTS - 1) * _DAY


def test_offset_pages_out_the_remainder(store: MetricPointsRepository) -> None:
    total = MAX_METRIC_POINTS + 1
    _seed(store, total)

    resp = _call(store, offset=MAX_METRIC_POINTS)

    assert resp.returned == 1
    assert resp.points[0].ts == _T0 + MAX_METRIC_POINTS * _DAY
    assert resp.partial_reason is None
    assert resp.offset == MAX_METRIC_POINTS


def test_max_points_caps_the_page_and_is_clamped_to_the_cap(
    store: MetricPointsRepository,
) -> None:
    _seed(store, 10)

    small = _call(store, max_points=3)
    assert small.returned == 3
    assert small.partial_reason == "too_large"
    assert small.total_available == 10

    oversized = _call(store, max_points=MAX_METRIC_POINTS + 999)
    assert oversized.returned == 10  # request clamped to the cap, not honored verbatim


# --- boundary validation -------------------------------------------------------------


def test_unknown_series_id_is_rejected_with_the_registered_list(
    store: MetricPointsRepository,
) -> None:
    with pytest.raises(UnknownMetricSeriesError, match=r"fng\.value"):
        _call(store, series_id="totally.unknown")


def test_bad_paging_arguments_are_rejected(store: MetricPointsRepository) -> None:
    with pytest.raises(ValueError, match="offset"):
        _call(store, offset=-1)
    with pytest.raises(ValueError, match="max_points"):
        _call(store, max_points=0)
    with pytest.raises(ValueError, match="start"):
        _call(store, start=-5)


# --- the registered tool end-to-end --------------------------------------------------


def test_tool_call_through_the_real_server(store: MetricPointsRepository) -> None:
    _seed(store, 3)
    server = FastMCP(name="test", stateless_http=True, json_response=True)
    register_get_metric_series(server, metric_points_repository=store)

    result = anyio.run(
        server.call_tool,
        "get_metric_series",
        {"series_id": SERIES_FNG_VALUE, "start": _T0, "end": _T0 + 2 * _DAY},
    )
    _content, structured = cast("tuple[Any, dict[str, Any]]", result)

    assert structured["series_id"] == SERIES_FNG_VALUE
    assert [p["ts"] for p in structured["points"]] == [_T0, _T0 + _DAY, _T0 + 2 * _DAY]
    assert structured["partial_reason"] is None
    assert structured["total_available"] == 3


def test_description_names_the_registered_series() -> None:
    server = FastMCP(name="test", stateless_http=True, json_response=True)
    engine = make_engine(":memory:")
    apply_migrations(engine)
    register_get_metric_series(
        server,
        metric_points_repository=MetricPointsRepository(make_session_factory(engine)),
    )

    tool = next(t for t in anyio.run(server.list_tools) if t.name == "get_metric_series")
    description = tool.description or ""
    assert "fng.value" in description
    assert "coingecko.btc_dominance" in description
    engine.dispose()
