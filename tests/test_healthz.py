"""Healthz + auth-before-routing checks for the sidecar.

Covers Plan 0001 phase 1 done-when items:
- /healthz returns 200 with and without a bearer secret.
- /ohlcv returns 401 without a bearer secret and 404 with one (auth runs first).
- create_app refuses to build with an empty secret.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast

import pytest
from fastapi.testclient import TestClient

from market_analyser.api.app import create_app
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.types import Bar
from market_analyser.persistence.engine import make_engine

SECRET = "test-secret"


class _StubProvider:
    """A coverage-less provider so an engine-wired test app builds without
    constructing the real network adapters."""

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> list[Bar]:
        return []


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(secret=SECRET))


def test_healthz_without_auth_returns_ok(client: TestClient) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert isinstance(body["version"], str) and body["version"]


def test_healthz_with_auth_also_returns_ok(client: TestClient) -> None:
    response = client.get("/healthz", headers={"Authorization": f"Bearer {SECRET}"})
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_protected_route_without_auth_returns_401(client: TestClient) -> None:
    response = client.get("/ohlcv")
    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}


def test_protected_route_with_wrong_secret_returns_401(client: TestClient) -> None:
    response = client.get("/ohlcv", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


def test_protected_route_with_non_bearer_scheme_returns_401(client: TestClient) -> None:
    response = client.get("/ohlcv", headers={"Authorization": f"Basic {SECRET}"})
    assert response.status_code == 401


def test_protected_route_with_valid_auth_returns_404_when_route_absent(
    client: TestClient,
) -> None:
    # Auth-before-routing: valid bearer reaches the routing layer and finds nothing.
    # /ohlcv exists from phase 2, so use an unrouted path here.
    response = client.get("/does-not-exist", headers={"Authorization": f"Bearer {SECRET}"})
    assert response.status_code == 404


def test_create_app_rejects_empty_secret() -> None:
    with pytest.raises(ValueError):
        create_app(secret="")


def test_healthz_has_no_metric_accrual_heartbeat_without_persistence(
    client: TestClient,
) -> None:
    # The metric-accrual job (Plan 0061, ADR-0056) is absent in the
    # persistence-free app, so its heartbeat never appears here. The
    # engine-wired heartbeat shape is pinned in tests/data/test_metric_accrual.py.
    body = client.get("/healthz").json()
    assert "metric_accrual" not in body


def test_healthz_has_no_recommendation_scoring_heartbeat_without_persistence(
    client: TestClient,
) -> None:
    # The recommendation scorer (Plan 0080, ADR-0075) needs the ledger (persistence)
    # AND the opt-in flag; the persistence-free app has neither, so its heartbeat
    # is absent — nothing silently degrades.
    body = client.get("/healthz").json()
    assert "recommendation_scoring" not in body


def test_healthz_exposes_recommendation_scoring_heartbeat_when_enabled() -> None:
    # Engine wired (ledger exists) + the flag on → the scorer is constructed and
    # its heartbeat is served, so a wedged scorer degrades loudly. A stub provider
    # keeps construction network-free; not entering the lifespan keeps it idle
    # (running=False, tick_count=0) — we assert the surface, not a live tick.
    app = create_app(
        secret=SECRET,
        engine=make_engine(":memory:"),
        provider=cast(MarketDataProvider, _StubProvider()),
        recommendation_scoring_enabled=True,
    )
    client = TestClient(app)
    body = client.get("/healthz").json()
    assert "recommendation_scoring" in body
    heartbeat = body["recommendation_scoring"]
    assert heartbeat["running"] is False
    assert heartbeat["tick_count"] == 0
    assert heartbeat["scored_count"] == 0
    assert heartbeat["row_errors"] == {}


def test_healthz_has_no_recommendation_scoring_heartbeat_when_flag_off() -> None:
    # Engine wired but the flag defaults off in the factory (the on-by-default
    # lives in AppConfig, applied by __main__) → no scorer, no heartbeat.
    app = create_app(
        secret=SECRET,
        engine=make_engine(":memory:"),
        provider=cast(MarketDataProvider, _StubProvider()),
    )
    client = TestClient(app)
    assert "recommendation_scoring" not in client.get("/healthz").json()
