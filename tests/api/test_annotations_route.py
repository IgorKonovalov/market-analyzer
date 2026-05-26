"""Plan 0006 phase 3 done-when: GET /annotations is renderer-bearer-gated and reads via the repo.

Covers:
- inserted annotation round-trips through the route with all fields
- 401 without any bearer
- 401 with the MCP bearer (cross-tenant escalation blocked at the read path)
- 422 for inverted `start > end` and for an unsupported timeframe
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from market_analyser.annotations.types import Annotation, AnnotationKind
from market_analyser.api.app import create_app
from market_analyser.data.types import (
    Bar,
    MarketSentimentSample,
    NewsItem,
    Quote,
    ScreenerRow,
    SentimentSample,
    SymbolInfo,
)
from market_analyser.persistence.annotations_repository import AnnotationsRepository
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)

RENDERER_SECRET = "renderer-test-secret"
MCP_SECRET = "mcp-test-secret"


class _UnusedProvider:
    """Provider stub: /annotations does not touch the provider; methods would assert if called."""

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        raise AssertionError("annotations route must not call the provider")

    def get_quote(self, symbol: str, as_of: datetime | None = None) -> Quote:
        raise NotImplementedError

    def search_symbols(self, query: str, as_of: datetime | None = None) -> Sequence[SymbolInfo]:
        raise NotImplementedError

    def get_screener(
        self,
        filters: dict[str, str | float | None],
        market: str = "america",
        exchange: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
    ) -> Sequence[ScreenerRow]:
        raise NotImplementedError

    def get_sentiment(
        self, symbol: str, window: str, source: str = "rss-vader", as_of: datetime | None = None
    ) -> SentimentSample:
        raise NotImplementedError

    def get_market_sentiment(
        self, market: str, window: str = "current", as_of: datetime | None = None
    ) -> MarketSentimentSample:
        raise NotImplementedError

    def get_news(
        self,
        symbol: str | None = None,
        window: str = "24h",
        limit: int = 50,
        with_sentiment: bool = False,
        as_of: datetime | None = None,
    ) -> Sequence[NewsItem]:
        raise NotImplementedError


@pytest.fixture
def repo() -> Iterator[AnnotationsRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield AnnotationsRepository(make_session_factory(engine))
    engine.dispose()


@pytest.fixture
def client(repo: AnnotationsRepository) -> TestClient:
    app = create_app(
        secret=RENDERER_SECRET,
        mcp_secret=MCP_SECRET,
        provider=_UnusedProvider(),
        annotations_repository=repo,
    )
    return TestClient(app)


def _renderer_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {RENDERER_SECRET}"}


def _params(
    symbol: str = "AAPL",
    timeframe: str = "1d",
    start: str = "2026-04-01T00:00:00+00:00",
    end: str = "2026-05-01T00:00:00+00:00",
) -> dict[str, str]:
    return {"symbol": symbol, "timeframe": timeframe, "start": start, "end": end}


def test_inserted_annotation_round_trips_through_route(
    client: TestClient, repo: AnnotationsRepository
) -> None:
    """A row inserted via the repo appears verbatim through GET /annotations + renderer bearer."""
    original = Annotation(
        symbol="AAPL",
        timeframe="1d",
        event_ts=datetime(2026, 4, 15, 0, 0, 0, 234_000, tzinfo=UTC),
        kind=AnnotationKind.BULLISH_MARKER,
        label="hammer at support",
        agent_id="claude-desktop-test",
    )
    repo.insert(original)

    response = client.get("/annotations", params=_params(), headers=_renderer_auth())

    assert response.status_code == 200, response.text
    payload = response.json()
    assert isinstance(payload, list)
    assert len(payload) == 1
    row = payload[0]
    assert row["id"] == original.id
    assert row["symbol"] == "AAPL"
    assert row["timeframe"] == "1d"
    assert row["kind"] == "bullish_marker"
    assert row["label"] == "hammer at support"
    assert row["agent_id"] == "claude-desktop-test"
    # event_ts is serialized as ISO8601 — ms precision is in the string.
    assert "2026-04-15" in row["event_ts"]
    assert row["event_ts"].endswith("+00:00") or row["event_ts"].endswith("Z")


def test_annotations_without_bearer_returns_401(client: TestClient) -> None:
    response = client.get("/annotations", params=_params())
    assert response.status_code == 401


def test_annotations_with_mcp_bearer_returns_401(client: TestClient) -> None:
    """Cross-tenant escalation blocked: the MCP bearer must not authenticate the renderer route."""
    response = client.get(
        "/annotations",
        params=_params(),
        headers={"Authorization": f"Bearer {MCP_SECRET}"},
    )
    assert response.status_code == 401


def test_inverted_window_returns_422(client: TestClient) -> None:
    response = client.get(
        "/annotations",
        params=_params(
            start="2026-05-01T00:00:00+00:00",
            end="2026-04-01T00:00:00+00:00",
        ),
        headers=_renderer_auth(),
    )
    assert response.status_code == 422
    assert "start" in response.json()["detail"].lower()


def test_unsupported_timeframe_returns_422(client: TestClient) -> None:
    response = client.get(
        "/annotations",
        params=_params(timeframe="5m"),
        headers=_renderer_auth(),
    )
    assert response.status_code == 422
    assert "5m" in response.json()["detail"]


def test_empty_window_returns_empty_list(client: TestClient) -> None:
    response = client.get("/annotations", params=_params(), headers=_renderer_auth())
    assert response.status_code == 200
    assert response.json() == []


def test_delete_removes_annotation(client: TestClient, repo: AnnotationsRepository) -> None:
    """DELETE /annotations/{id} + renderer bearer returns 204 and the row is gone."""
    ann = Annotation(
        symbol="AAPL",
        timeframe="1d",
        event_ts=datetime(2026, 4, 15, tzinfo=UTC),
        kind=AnnotationKind.BULLISH_MARKER,
        agent_id="smoke",
    )
    repo.insert(ann)

    delete_response = client.delete(f"/annotations/{ann.id}", headers=_renderer_auth())
    assert delete_response.status_code == 204, delete_response.text
    assert delete_response.content == b""

    get_response = client.get("/annotations", params=_params(), headers=_renderer_auth())
    assert get_response.status_code == 200
    assert get_response.json() == []


def test_delete_unknown_id_returns_404(client: TestClient) -> None:
    response = client.delete("/annotations/does-not-exist", headers=_renderer_auth())
    assert response.status_code == 404
    assert "does-not-exist" in response.json()["detail"]


def test_delete_without_bearer_returns_401(client: TestClient) -> None:
    response = client.delete("/annotations/whatever")
    assert response.status_code == 401


def test_delete_with_mcp_bearer_returns_401(client: TestClient) -> None:
    """Cross-tenant escalation blocked: the MCP bearer must not reach the delete path."""
    response = client.delete(
        "/annotations/whatever",
        headers={"Authorization": f"Bearer {MCP_SECRET}"},
    )
    assert response.status_code == 401
