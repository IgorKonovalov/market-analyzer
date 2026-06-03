"""Plan 0014 phase 1 done-when: the `/agent_mode` + `/ui_events` HTTP routes.

Asserts the renderer-side contract: the toggle round-trips and is cross-tenant
isolated (MCP bearer 401s), `POST /ui_events` enforces agent mode server-side
(403 when OFF, buffer untouched), accepts a valid envelope when ON (202 +
server-stamped event_id), and rejects unknown types / bad payloads / missing
bearers at the boundary.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from market_analyser.api.app import create_app
from market_analyser.api.mcp_secret import load_or_generate_mcp_secret
from market_analyser.data.types import (
    Bar,
    MacroContext,
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

_VALID_RANGE_PAYLOAD = {
    "symbol": "AAPL",
    "timeframe": "1d",
    "range_start": "2026-05-01T00:00:00+00:00",
    "range_end": "2026-05-15T00:00:00+00:00",
}


class _FakeProvider:
    """UI-event routes don't touch the data path; bodies are never invoked."""

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        return []

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

    def get_macro_context(
        self, market: str = "crypto", as_of: datetime | None = None
    ) -> MacroContext:
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
def mcp_secret_path(tmp_path: Path) -> Path:
    return tmp_path / "mcp-secret.json"


@pytest.fixture
def mcp_secret(mcp_secret_path: Path) -> str:
    return load_or_generate_mcp_secret(mcp_secret_path)


@pytest.fixture
def annotations_repo() -> Iterator[AnnotationsRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield AnnotationsRepository(make_session_factory(engine))
    engine.dispose()


@pytest.fixture
def app(
    mcp_secret: str,
    mcp_secret_path: Path,
    annotations_repo: AnnotationsRepository,
    tmp_path: Path,
) -> FastAPI:
    return create_app(
        secret=RENDERER_SECRET,
        mcp_secret=mcp_secret,
        mcp_secret_path=mcp_secret_path,
        provider=_FakeProvider(),
        annotations_repository=annotations_repo,
        agent_mode_path=tmp_path / "agent_mode.json",
    )


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _renderer_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {RENDERER_SECRET}"}


def _mcp_headers(secret: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {secret}"}


# --------------------------------------------------------------------------- #
# GET / PUT /agent_mode                                                       #
# --------------------------------------------------------------------------- #


def test_get_agent_mode_fresh_is_disabled(client: TestClient) -> None:
    response = client.get("/agent_mode", headers=_renderer_headers())
    assert response.status_code == 200, response.text
    assert response.json() == {"enabled": False}


def test_get_agent_mode_rejects_missing_bearer(client: TestClient) -> None:
    assert client.get("/agent_mode").status_code == 401


def test_get_agent_mode_rejects_mcp_bearer(client: TestClient, mcp_secret: str) -> None:
    """Cross-tenant: an agent cannot read the consent gate via the MCP bearer."""
    assert client.get("/agent_mode", headers=_mcp_headers(mcp_secret)).status_code == 401


def test_put_agent_mode_persists_and_synthesises_toggle_event(
    client: TestClient, app: FastAPI
) -> None:
    response = client.put("/agent_mode", json={"enabled": True}, headers=_renderer_headers())
    assert response.status_code == 200, response.text
    assert response.json() == {"enabled": True}

    # Subsequent GET reflects the new state.
    assert client.get("/agent_mode", headers=_renderer_headers()).json() == {"enabled": True}

    # Exactly one synthesised ui.agent_mode_toggled v1 envelope in the buffer.
    snap = app.state.ui_event_buffer.snapshot()
    assert len(snap) == 1
    env = snap[0]
    assert env.type == "ui.agent_mode_toggled"
    assert env.version == 1
    assert env.payload["enabled"] is True


def test_put_agent_mode_rejects_wrong_type(client: TestClient) -> None:
    response = client.put("/agent_mode", json={"enabled": "yes"}, headers=_renderer_headers())
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# POST /ui_events                                                             #
# --------------------------------------------------------------------------- #


def test_post_ui_event_403_when_mode_off(client: TestClient, app: FastAPI) -> None:
    response = client.post(
        "/ui_events",
        json={"type": "ui.range_selected", "version": 1, "payload": _VALID_RANGE_PAYLOAD},
        headers=_renderer_headers(),
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "agent mode is off"}
    # Buffer untouched.
    assert app.state.ui_event_buffer.snapshot() == []


def test_post_ui_event_202_when_mode_on(client: TestClient, app: FastAPI) -> None:
    # Flip the store directly so the buffer starts empty (a PUT would itself
    # buffer a toggle event); we want "exactly that envelope" afterwards.
    app.state.agent_mode_store.set_enabled(True)

    response = client.post(
        "/ui_events",
        json={"type": "ui.range_selected", "version": 1, "payload": _VALID_RANGE_PAYLOAD},
        headers=_renderer_headers(),
    )
    assert response.status_code == 202, response.text
    body = response.json()
    event_id = body["event_id"]
    assert uuid.UUID(event_id).version == 4

    snap = app.state.ui_event_buffer.snapshot()
    assert len(snap) == 1
    env = snap[0]
    assert env.event_id == event_id
    assert env.type == "ui.range_selected"
    assert env.version == 1
    assert env.payload["symbol"] == "AAPL"


def test_post_ui_event_server_stamps_event_id_and_ts(client: TestClient, app: FastAPI) -> None:
    """The renderer supplies neither event_id nor ts; the server generates both.
    Even if the renderer tries to send them, the body model has no such fields,
    so they cannot be injected."""
    app.state.agent_mode_store.set_enabled(True)

    response = client.post(
        "/ui_events",
        json={
            "type": "ui.range_selected",
            "version": 1,
            "payload": _VALID_RANGE_PAYLOAD,
            "event_id": "attacker-supplied",
            "ts": "1999-01-01T00:00:00+00:00",
        },
        headers=_renderer_headers(),
    )
    assert response.status_code == 202, response.text
    env = app.state.ui_event_buffer.snapshot()[0]
    assert env.event_id != "attacker-supplied"
    assert uuid.UUID(env.event_id).version == 4
    # ts is server-generated (recent), not the 1999 value the renderer tried.
    assert env.ts.year >= 2026


def test_post_ui_event_rejects_unknown_type(client: TestClient, app: FastAPI) -> None:
    app.state.agent_mode_store.set_enabled(True)
    response = client.post(
        "/ui_events",
        json={"type": "ui.something_unknown", "version": 1, "payload": {}},
        headers=_renderer_headers(),
    )
    assert response.status_code == 422
    assert app.state.ui_event_buffer.snapshot() == []


def test_post_ui_event_rejects_bad_payload(client: TestClient, app: FastAPI) -> None:
    app.state.agent_mode_store.set_enabled(True)
    bad = {
        "symbol": "AAPL",
        "timeframe": "1d",
        "range_start": "2026-05-15T00:00:00+00:00",
        "range_end": "2026-05-01T00:00:00+00:00",  # end < start
    }
    response = client.post(
        "/ui_events",
        json={"type": "ui.range_selected", "version": 1, "payload": bad},
        headers=_renderer_headers(),
    )
    assert response.status_code == 422
    assert app.state.ui_event_buffer.snapshot() == []


def test_post_ui_event_rejects_missing_bearer(client: TestClient) -> None:
    response = client.post(
        "/ui_events",
        json={"type": "ui.range_selected", "version": 1, "payload": _VALID_RANGE_PAYLOAD},
    )
    assert response.status_code == 401


def test_post_ui_event_rejects_mcp_bearer(client: TestClient, mcp_secret: str) -> None:
    """Cross-tenant: the MCP bearer cannot POST renderer UI events."""
    response = client.post(
        "/ui_events",
        json={"type": "ui.range_selected", "version": 1, "payload": _VALID_RANGE_PAYLOAD},
        headers=_mcp_headers(mcp_secret),
    )
    assert response.status_code == 401
