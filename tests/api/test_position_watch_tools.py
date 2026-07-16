"""Plan 0099 phase 2 — the position-watch MCP tool bodies, the renderer-facing
read routes, and the `/healthz` monitor heartbeat.

Pinned here:
- `create_position_watch`'s boundary (defaults applied, agent source stamped,
  repository validation surfaces before any write);
- `list_position_alerts`' honest ADR-0046 paging envelope;
- the read-only `GET /defi/position_watches` + `GET /defi/position_alerts`
  routes: bearer gating, masked wallets (a full address never reaches
  renderer state), newest-first paging with honest totals;
- `/healthz` exposes the position-monitor heartbeat when (and only when) the
  monitor is enabled.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from market_analyser.api.app import create_app
from market_analyser.api.mcp_tools.position_watches import (
    MAX_ALERTS,
    _create_position_watch_response,
    _list_position_alerts_response,
)
from market_analyser.persistence.engine import make_engine, make_session_factory
from market_analyser.persistence.repositories.defi_position_watches import (
    DefiPositionAlertsRepository,
    DefiPositionWatchesRepository,
)

RENDERER_SECRET = "renderer-test-secret"

# Synthetic placeholder addresses — never a real wallet (public repo).
WALLET = "0x" + "ab" * 20
POOL = "0x" + "cd" * 20
MASKED_WALLET = f"{WALLET[:6]}…{WALLET[-4:]}"

CREATED_AT = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
OUT_SINCE = datetime(2026, 7, 16, 3, 0, tzinfo=UTC)
FIRED_AT = OUT_SINCE + timedelta(hours=6)


class _UnusedProvider:
    def get_ohlcv(self, *args: Any, **kwargs: Any) -> list[Any]:
        raise NotImplementedError


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = make_engine(":memory:")
    yield engine
    engine.dispose()


@pytest.fixture
def client(engine: Engine) -> TestClient:
    app = create_app(
        secret=RENDERER_SECRET,
        provider=_UnusedProvider(),  # type: ignore[arg-type]  # routes never fetch bars
        engine=engine,
    )
    return TestClient(app)


@pytest.fixture
def watches(engine: Engine, client: TestClient) -> DefiPositionWatchesRepository:
    # create_app applied migrations on this engine; share its session factory.
    return DefiPositionWatchesRepository(make_session_factory(engine))


@pytest.fixture
def alerts(engine: Engine, client: TestClient) -> DefiPositionAlertsRepository:
    return DefiPositionAlertsRepository(make_session_factory(engine))


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {RENDERER_SECRET}"}


def _insert_alert(repo: DefiPositionAlertsRepository, watch_id: int, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "watch_id": watch_id,
        "wallet": WALLET,
        "chain": "base",
        "pool_address": POOL,
        "nft_token_id": 42,
        "fired_at": FIRED_AT,
        "out_since": OUT_SINCE,
        "hours_out": 6.0,
        "tick_lower": -100,
        "tick_upper": 100,
        "current_tick": 150,
        "uncollected_fees": None,
    }
    kwargs.update(overrides)
    return repo.insert(**kwargs)


class TestCreatePositionWatchBody:
    def test_defaults_and_agent_source(self, watches: DefiPositionWatchesRepository) -> None:
        watch = _create_position_watch_response(
            watches_repository=watches,
            wallet=WALLET,
            chain="base",
            pool_address=POOL,
            nft_token_id=None,
            dwell_hours=6.0,
            interval_seconds=900,
            enabled=True,
            now=CREATED_AT,
        )
        assert watch.source == "agent"
        assert watch.dwell_hours == 6.0
        assert watch.interval_seconds == 900
        assert watch.dwell_state.out_since is None

    def test_boundary_rejects_bad_wallet_before_write(
        self, watches: DefiPositionWatchesRepository
    ) -> None:
        with pytest.raises(ValueError, match="wallet must be an EVM address"):
            _create_position_watch_response(
                watches_repository=watches,
                wallet="not-an-address",
                chain="base",
                pool_address=POOL,
                nft_token_id=None,
                dwell_hours=6.0,
                interval_seconds=900,
                enabled=True,
                now=CREATED_AT,
            )
        assert watches.list() == []


class TestListPositionAlertsPaging:
    def test_full_page_has_no_partial_reason(
        self,
        watches: DefiPositionWatchesRepository,
        alerts: DefiPositionAlertsRepository,
    ) -> None:
        watch = watches.create(
            wallet=WALLET,
            chain="base",
            pool_address=POOL,
            nft_token_id=None,
            dwell_hours=6.0,
            interval_seconds=900,
            source="agent",
            created_at=CREATED_AT,
        )
        _insert_alert(alerts, watch.id)
        response = _list_position_alerts_response(
            alerts_repository=alerts, watch_id=None, offset=0, max_alerts=None
        )
        assert response.total_available == 1
        assert response.returned == 1
        assert response.partial_reason is None
        assert response.message is None

    def test_overflow_pages_honestly(
        self,
        watches: DefiPositionWatchesRepository,
        alerts: DefiPositionAlertsRepository,
    ) -> None:
        watch = watches.create(
            wallet=WALLET,
            chain="base",
            pool_address=POOL,
            nft_token_id=None,
            dwell_hours=6.0,
            interval_seconds=900,
            source="agent",
            created_at=CREATED_AT,
        )
        for i in range(5):
            _insert_alert(alerts, watch.id, fired_at=FIRED_AT + timedelta(hours=i))
        response = _list_position_alerts_response(
            alerts_repository=alerts, watch_id=None, offset=0, max_alerts=2
        )
        assert response.partial_reason == "too_large"
        assert response.total_available == 5
        assert response.returned == 2
        assert response.message is not None
        assert "offset=2" in response.message
        # Newest first.
        assert response.alerts[0].fired_at > response.alerts[1].fired_at

    def test_page_size_is_capped(self, alerts: DefiPositionAlertsRepository) -> None:
        response = _list_position_alerts_response(
            alerts_repository=alerts, watch_id=None, offset=0, max_alerts=10_000
        )
        assert response.returned <= MAX_ALERTS


class TestPositionWatchRoutes:
    def test_routes_are_bearer_gated(self, client: TestClient) -> None:
        assert client.get("/defi/position_watches").status_code == 401
        assert client.get("/defi/position_alerts").status_code == 401

    def test_watch_list_masks_the_wallet(
        self, client: TestClient, watches: DefiPositionWatchesRepository
    ) -> None:
        watches.create(
            wallet=WALLET,
            chain="base",
            pool_address=POOL,
            nft_token_id=7,
            dwell_hours=6.0,
            interval_seconds=900,
            source="config",
            created_at=CREATED_AT,
        )
        response = client.get("/defi/position_watches", headers=_auth())
        assert response.status_code == 200
        rows = response.json()
        assert len(rows) == 1
        assert rows[0]["wallet"] == MASKED_WALLET
        assert WALLET not in response.text
        assert rows[0]["pool_address"] == POOL
        assert rows[0]["source"] == "config"

    def test_alert_history_pages_newest_first_with_masked_wallet(
        self,
        client: TestClient,
        watches: DefiPositionWatchesRepository,
        alerts: DefiPositionAlertsRepository,
    ) -> None:
        watch = watches.create(
            wallet=WALLET,
            chain="base",
            pool_address=POOL,
            nft_token_id=None,
            dwell_hours=6.0,
            interval_seconds=900,
            source="agent",
            created_at=CREATED_AT,
        )
        for i in range(3):
            _insert_alert(alerts, watch.id, fired_at=FIRED_AT + timedelta(hours=i))
        response = client.get("/defi/position_alerts", headers=_auth(), params={"limit": 2})
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 3
        assert len(body["alerts"]) == 2
        fired = [a["fired_at"] for a in body["alerts"]]
        assert fired == sorted(fired, reverse=True)
        assert all(a["wallet"] == MASKED_WALLET for a in body["alerts"])
        assert WALLET not in response.text


class TestHealthzHeartbeat:
    def test_healthz_exposes_monitor_heartbeat_when_enabled(self, engine: Engine) -> None:
        app = create_app(
            secret=RENDERER_SECRET,
            provider=_UnusedProvider(),  # type: ignore[arg-type]
            engine=engine,
            position_monitor_enabled=True,
        )
        body = TestClient(app).get("/healthz").json()
        heartbeat = body["position_monitor"]
        assert heartbeat["tick_count"] == 0
        assert heartbeat["watch_errors"] == {}

    def test_healthz_omits_monitor_when_disabled(self, client: TestClient) -> None:
        assert "position_monitor" not in client.get("/healthz").json()
