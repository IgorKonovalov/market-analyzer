"""Healthz + auth-before-routing checks for the sidecar.

Covers Plan 0001 phase 1 done-when items:
- /healthz returns 200 with and without a bearer secret.
- /ohlcv returns 401 without a bearer secret and 404 with one (auth runs first).
- create_app refuses to build with an empty secret.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from market_analyser.api.app import create_app

SECRET = "test-secret"


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
    response = client.get(
        "/does-not-exist", headers={"Authorization": f"Bearer {SECRET}"}
    )
    assert response.status_code == 404


def test_create_app_rejects_empty_secret() -> None:
    with pytest.raises(ValueError):
        create_app(secret="")
