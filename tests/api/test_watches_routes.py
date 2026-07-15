"""Plan 0060 phase 4 — the renderer-facing watch/alert routes.

The viewer's Alerts surface reads the watch list and alert history and
manages watches. Pinned here: bearer gating, the enable round-trip the
phase's done-when (b) rides on, 404 on unknown ids, and newest-first paging
with honest totals.

Plan 0110 phase 1 widened the mutation surface; also pinned:
- `POST /watches/{id}` is a partial update — it mutates exactly the supplied
  fields (an enable/disable toggle never wipes the note), `note: null`
  clears, and an empty body is 422;
- `DELETE /watches/{id}` removes the watch AND its alert-history rows
  (asserted by row count), 404 on unknown ids.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from market_analyser.api.app import create_app
from market_analyser.persistence.engine import make_engine, make_session_factory
from market_analyser.persistence.repositories.watches import (
    AlertsRepository,
    WatchesRepository,
)

RENDERER_SECRET = "renderer-test-secret"
CREATED_AT = datetime(2026, 7, 1, tzinfo=UTC)
FIRED_AT = datetime(2026, 7, 2, tzinfo=UTC)


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
def watches(engine: Engine) -> WatchesRepository:
    # create_app applied migrations on this engine; share its session factory.
    return WatchesRepository(make_session_factory(engine))


@pytest.fixture
def alerts(engine: Engine) -> AlertsRepository:
    return AlertsRepository(make_session_factory(engine))


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {RENDERER_SECRET}"}


def _seed_watch(
    watches: WatchesRepository, *, symbol: str = "BTC-USD", note: str | None = None
) -> int:
    return watches.create(
        symbol=symbol,
        timeframe="1d",
        kind="indicator_threshold",
        params={"indicator": "rsi", "operator": "<", "level": 30.0},
        interval_seconds=86_400,
        created_at=CREATED_AT,
        note=note,
    ).id


class TestAuth:
    def test_all_four_routes_reject_missing_bearer(self, client: TestClient) -> None:
        assert client.get("/watches").status_code == 401
        assert client.post("/watches/1", json={"enabled": False}).status_code == 401
        assert client.delete("/watches/1").status_code == 401
        assert client.get("/alerts").status_code == 401


class TestWatchesRoutes:
    def test_empty_list(self, client: TestClient) -> None:
        response = client.get("/watches", headers=_auth())
        assert response.status_code == 200
        assert response.json() == []

    def test_list_returns_created_watches(
        self, client: TestClient, watches: WatchesRepository
    ) -> None:
        watch_id = _seed_watch(watches)
        response = client.get("/watches", headers=_auth())
        assert response.status_code == 200
        [row] = response.json()
        assert row["id"] == watch_id
        assert row["symbol"] == "BTC-USD"
        assert row["kind"] == "indicator_threshold"
        assert row["params"] == {"indicator": "rsi", "operator": "<", "level": 30.0}
        assert row["enabled"] is True
        assert row["last_state"] is None

    def test_enable_round_trips(self, client: TestClient, watches: WatchesRepository) -> None:
        """Done-when (b), sidecar half: disabling persists and the list
        reflects it; re-enabling restores."""
        watch_id = _seed_watch(watches)

        disabled = client.post(f"/watches/{watch_id}", json={"enabled": False}, headers=_auth())
        assert disabled.status_code == 200
        assert disabled.json()["enabled"] is False

        listed = client.get("/watches", headers=_auth())
        assert [w["enabled"] for w in listed.json()] == [False]

        enabled = client.post(f"/watches/{watch_id}", json={"enabled": True}, headers=_auth())
        assert enabled.status_code == 200
        assert enabled.json()["enabled"] is True

    def test_unknown_watch_is_404(self, client: TestClient) -> None:
        response = client.post("/watches/9999", json={"enabled": False}, headers=_auth())
        assert response.status_code == 404

    def test_extra_body_keys_are_422(self, client: TestClient, watches: WatchesRepository) -> None:
        watch_id = _seed_watch(watches)
        response = client.post(
            f"/watches/{watch_id}",
            json={"enabled": False, "action": "buy"},
            headers=_auth(),
        )
        assert response.status_code == 422

    def test_list_returns_note(self, client: TestClient, watches: WatchesRepository) -> None:
        _seed_watch(watches, note="ETH long scenario A")
        [row] = client.get("/watches", headers=_auth()).json()
        assert row["note"] == "ETH long scenario A"


class TestPartialUpdate:
    def test_note_only_update_leaves_enabled_untouched(
        self, client: TestClient, watches: WatchesRepository
    ) -> None:
        watch_id = _seed_watch(watches)
        response = client.post(
            f"/watches/{watch_id}", json={"note": "neckline retest"}, headers=_auth()
        )
        assert response.status_code == 200
        body = response.json()
        assert body["note"] == "neckline retest"
        assert body["enabled"] is True

    def test_enabled_only_update_never_wipes_the_note(
        self, client: TestClient, watches: WatchesRepository
    ) -> None:
        """The plan's named risk: absent `note` must mean untouched, or every
        enable/disable toggle silently wipes notes."""
        watch_id = _seed_watch(watches, note="keep me")
        response = client.post(f"/watches/{watch_id}", json={"enabled": False}, headers=_auth())
        assert response.status_code == 200
        body = response.json()
        assert body["enabled"] is False
        assert body["note"] == "keep me"

    def test_explicit_null_note_clears(
        self, client: TestClient, watches: WatchesRepository
    ) -> None:
        watch_id = _seed_watch(watches, note="stale context")
        response = client.post(f"/watches/{watch_id}", json={"note": None}, headers=_auth())
        assert response.status_code == 200
        assert response.json()["note"] is None

    def test_empty_body_is_422(self, client: TestClient, watches: WatchesRepository) -> None:
        watch_id = _seed_watch(watches)
        assert client.post(f"/watches/{watch_id}", json={}, headers=_auth()).status_code == 422

    def test_null_enabled_is_422(self, client: TestClient, watches: WatchesRepository) -> None:
        watch_id = _seed_watch(watches)
        response = client.post(f"/watches/{watch_id}", json={"enabled": None}, headers=_auth())
        assert response.status_code == 422

    def test_over_length_note_is_422(self, client: TestClient, watches: WatchesRepository) -> None:
        watch_id = _seed_watch(watches)
        response = client.post(f"/watches/{watch_id}", json={"note": "x" * 501}, headers=_auth())
        assert response.status_code == 422


class TestDeleteWatch:
    def test_delete_removes_watch_and_history_rows(
        self,
        client: TestClient,
        watches: WatchesRepository,
        alerts: AlertsRepository,
    ) -> None:
        watch_id = _seed_watch(watches)
        for i in range(3):
            alerts.insert(
                watch_id=watch_id,
                fired_at=FIRED_AT + timedelta(hours=i),
                payload={"condition": f"fact {i}"},
            )
        assert client.get("/alerts", headers=_auth()).json()["total"] == 3

        response = client.delete(f"/watches/{watch_id}", headers=_auth())
        assert response.status_code == 200
        assert response.json() == {"deleted": True}

        assert client.get("/watches", headers=_auth()).json() == []
        assert client.get("/alerts", headers=_auth()).json()["total"] == 0

    def test_delete_unknown_watch_is_404(self, client: TestClient) -> None:
        assert client.delete("/watches/9999", headers=_auth()).status_code == 404


class TestAlertsRoute:
    def test_newest_first_paging_with_totals(
        self,
        client: TestClient,
        watches: WatchesRepository,
        alerts: AlertsRepository,
    ) -> None:
        watch_id = _seed_watch(watches)
        for i in range(5):
            alerts.insert(
                watch_id=watch_id,
                fired_at=FIRED_AT + timedelta(hours=i),
                payload={"condition": f"fact {i}", "values": {}},
            )

        first = client.get("/alerts?limit=2", headers=_auth())
        assert first.status_code == 200
        body = first.json()
        assert body["total"] == 5
        assert [a["payload"]["condition"] for a in body["alerts"]] == ["fact 4", "fact 3"]

        second = client.get("/alerts?limit=2&offset=2", headers=_auth())
        assert [a["payload"]["condition"] for a in second.json()["alerts"]] == [
            "fact 2",
            "fact 1",
        ]

    def test_watch_id_filter(
        self,
        client: TestClient,
        watches: WatchesRepository,
        alerts: AlertsRepository,
    ) -> None:
        first_id = _seed_watch(watches)
        second_id = _seed_watch(watches, symbol="ETH-USD")
        alerts.insert(watch_id=first_id, fired_at=FIRED_AT, payload={"n": 1})
        alerts.insert(watch_id=second_id, fired_at=FIRED_AT, payload={"n": 2})

        response = client.get(f"/alerts?watch_id={first_id}", headers=_auth())
        body = response.json()
        assert body["total"] == 1
        assert [a["watch_id"] for a in body["alerts"]] == [first_id]

    def test_bad_paging_params_are_422(self, client: TestClient) -> None:
        assert client.get("/alerts?offset=-1", headers=_auth()).status_code == 422
        assert client.get("/alerts?limit=0", headers=_auth()).status_code == 422
        assert client.get("/alerts?limit=1000", headers=_auth()).status_code == 422
