"""Plan 0080 phase 5 prerequisite: GET /track_record.

The REST twin of the `get_track_record` MCP tool — the renderer's read path to
the advisor's live track record. Done-when:

- 200 + the honest aggregate (hit-rate, mean R, baseline) over scored rows;
- the insufficient-sample state (`sufficient: false`, null hit_rate) on a 3-row set;
- the recent-calls list is bounded (ADR-0046 `too_large` paging);
- the `symbol` filter scopes the record;
- a bad offset → 422;
- 401 without the renderer bearer / with the MCP bearer (cross-tenant isolation);
- the route is absent when persistence is unwired (no ledger, no route).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from fastapi.testclient import TestClient

from market_analyser.api.app import create_app
from market_analyser.api.mcp_tools.track_record import MAX_RECENT_CALLS
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.types import Bar
from market_analyser.persistence.advice_ledger_repository import (
    AdviceLedgerEntry,
    AdviceLedgerRepository,
)
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)

RENDERER_SECRET = "renderer-test-secret"
MCP_SECRET = "mcp-test-secret"
_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_DAY = timedelta(days=1)


class _StubProvider:
    """Coverage-less provider so the engine-wired app builds network-free; the
    track_record route never touches the provider."""

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> list[Bar]:
        return []


def _scored(
    *,
    symbol: str = "AAA",
    idx: int = 0,
    directional_correct: bool = True,
    realized_r: float = 1.0,
) -> AdviceLedgerEntry:
    return AdviceLedgerEntry(
        symbol=symbol,
        timeframe="1d",
        strategy_id="rsi",
        as_of_bar_ts=_T0 + idx * _DAY,
        horizon_bars=5,
        direction="long",
        entry_zone=(99.0, 101.0),
        stop=90.0,
        targets=[110.0],
        conviction=0.6,
        forecast_prob=0.6,
        artifact_path=None,
        created_at=_T0 + idx * _DAY,
        outcome_class="target_hit" if directional_correct else "stopped",
        realized_return=0.1 if realized_r >= 0 else -0.1,
        realized_r=realized_r,
        directional_correct=directional_correct,
        scored_at=_T0 + idx * _DAY,
    )


@pytest.fixture
def wired() -> Iterator[tuple[TestClient, AdviceLedgerRepository]]:
    engine = make_engine(":memory:")
    # create_app applies migrations + builds the ledger repo internally and mounts
    # the route; a second repo on the SAME engine is our seed handle.
    app = create_app(
        secret=RENDERER_SECRET,
        mcp_secret=MCP_SECRET,
        engine=engine,
        provider=cast(MarketDataProvider, _StubProvider()),
    )
    apply_migrations(engine)  # idempotent; ensures the table exists for the seed repo
    seed_repo = AdviceLedgerRepository(make_session_factory(engine))
    yield TestClient(app), seed_repo
    engine.dispose()


def _renderer_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {RENDERER_SECRET}"}


def _mcp_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {MCP_SECRET}"}


def test_returns_aggregate_over_scored_rows(
    wired: tuple[TestClient, AdviceLedgerRepository],
) -> None:
    client, repo = wired
    for i in range(13):
        repo.record(_scored(idx=i, directional_correct=True, realized_r=1.0))
    for i in range(7):
        repo.record(_scored(idx=100 + i, directional_correct=False, realized_r=-1.0))

    response = client.get("/track_record", headers=_renderer_auth())
    assert response.status_code == 200, response.text
    body = response.json()
    tr = body["track_record"]
    assert tr["n"] == 20
    assert tr["sufficient"] is True
    assert tr["hit_rate"] == pytest.approx(0.65)
    assert tr["mean_r"] == pytest.approx(0.3)
    assert tr["baseline_hit_rate"] is not None  # baseline always present
    assert tr["hit_rate_vs_baseline"] == pytest.approx(0.0)  # all-long → no edge
    assert body["total_available"] == 20
    assert body["partial_reason"] is None


def test_insufficient_sample_withholds_hit_rate(
    wired: tuple[TestClient, AdviceLedgerRepository],
) -> None:
    client, repo = wired
    for i in range(3):
        repo.record(_scored(idx=i))
    body = client.get("/track_record", headers=_renderer_auth()).json()
    assert body["track_record"]["sufficient"] is False
    assert body["track_record"]["hit_rate"] is None


def test_recent_list_is_bounded_and_pages(
    wired: tuple[TestClient, AdviceLedgerRepository],
) -> None:
    client, repo = wired
    total = MAX_RECENT_CALLS + 1
    for i in range(total):
        repo.record(_scored(idx=i))

    first = client.get("/track_record", headers=_renderer_auth()).json()
    assert first["returned"] == MAX_RECENT_CALLS
    assert first["total_available"] == total
    assert first["partial_reason"] == "too_large"

    rest = client.get(f"/track_record?offset={MAX_RECENT_CALLS}", headers=_renderer_auth()).json()
    assert rest["returned"] == 1
    assert rest["partial_reason"] is None


def test_symbol_filter_scopes_the_record(
    wired: tuple[TestClient, AdviceLedgerRepository],
) -> None:
    client, repo = wired
    for i in range(20):
        repo.record(_scored(symbol="AAA", idx=i))
    for i in range(5):
        repo.record(_scored(symbol="BBB", idx=200 + i))
    body = client.get("/track_record?symbol=BBB", headers=_renderer_auth()).json()
    assert body["track_record"]["n"] == 5
    assert all(call["symbol"] == "BBB" for call in body["recent"])


def test_negative_offset_returns_422(wired: tuple[TestClient, AdviceLedgerRepository]) -> None:
    client, _ = wired
    response = client.get("/track_record?offset=-1", headers=_renderer_auth())
    assert response.status_code == 422


def test_without_bearer_returns_401(wired: tuple[TestClient, AdviceLedgerRepository]) -> None:
    client, _ = wired
    assert client.get("/track_record").status_code == 401


def test_with_mcp_bearer_returns_401(wired: tuple[TestClient, AdviceLedgerRepository]) -> None:
    """Cross-tenant isolation: the MCP bearer must not authenticate the renderer route."""
    client, _ = wired
    assert client.get("/track_record", headers=_mcp_auth()).status_code == 401


def test_route_absent_without_persistence() -> None:
    # No engine → no ledger → the route is never mounted (a valid bearer 404s).
    app = create_app(secret=RENDERER_SECRET)
    client = TestClient(app)
    assert client.get("/track_record", headers=_renderer_auth()).status_code == 404
