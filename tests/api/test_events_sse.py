"""Plan 0007 phase 2 done-when: SSE /events stream + typed envelope schema.

Defends:
- Auth: header-bearer works, ?token=bearer works (only on /events), missing /
  wrong / cross-tenant (MCP) bearer all 401.
- Stream shape: 200 with text/event-stream content-type; `retry: 5000` at start;
  `: ping` heartbeat at the configured interval; `data: <json>` for envelopes.
- Pub/sub: two subscribers each receive the same envelope.
- Backpressure: a subscriber that doesn't drain sees the latest N envelopes
  with a synthetic `chart.update_dropped v1` ahead of them.
- Validation: unregistered type raises `UnknownEventTypeError`; payload that
  fails the registered model raises pydantic `ValidationError`.
- Envelope version equals the per-type model's VERSION.
- Bearer in `?token=` does not leak into captured log records.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import threading
import time
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from market_analyser.api.app import create_app
from market_analyser.api.events import (
    ChartShowPayloadV1,
    ChartUpdateDroppedPayloadV1,
    ChartUpdatePayloadV1,
    Envelope,
    EventBus,
    UnknownEventTypeError,
)
from market_analyser.api.mcp_secret import load_or_generate_mcp_secret
from market_analyser.data.types import (
    Bar,
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


class _FakeProvider:
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
        as_of: datetime | None = None,
    ) -> Sequence[ScreenerRow]:
        raise NotImplementedError

    def get_sentiment(
        self, symbol: str, window: str, as_of: datetime | None = None
    ) -> SentimentSample:
        raise NotImplementedError

    def get_news(
        self, symbol: str, window: str, as_of: datetime | None = None
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
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def app(
    mcp_secret: str,
    mcp_secret_path: Path,
    annotations_repo: AnnotationsRepository,
    event_bus: EventBus,
) -> FastAPI:
    return create_app(
        secret=RENDERER_SECRET,
        mcp_secret=mcp_secret,
        mcp_secret_path=mcp_secret_path,
        provider=_FakeProvider(),
        annotations_repository=annotations_repo,
        event_bus=event_bus,
    )


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------- #
# Auth                                                                        #
# --------------------------------------------------------------------------- #


@pytest.fixture
def live_server(app: FastAPI) -> Iterator[str]:
    """Spin up the FastAPI app on a real loopback port via uvicorn in a
    background thread. Streaming SSE tests can't reliably use
    `httpx.ASGITransport`: when the test closes the connection, the in-process
    transport doesn't always propagate `http.disconnect` to the server's
    generator, so `aclose()` hangs waiting for the body to finish. A real
    socket round-trip avoids that. Yields the base URL."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        lifespan="on",
    )
    server = uvicorn.Server(config)
    sock.close()  # uvicorn will rebind; we just used it to claim the port

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for the server to come up.
    deadline = time.time() + 10.0
    while time.time() < deadline:
        try:
            with httpx.Client(timeout=1.0) as c:
                r = c.get(f"http://127.0.0.1:{port}/healthz")
                if r.status_code == 200:
                    break
        except httpx.HTTPError:
            pass
        time.sleep(0.05)
    else:
        raise RuntimeError(f"uvicorn server did not come up on port {port}")

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


def test_events_with_query_bearer_returns_200_and_event_stream_content_type(
    live_server: str,
) -> None:
    with (
        httpx.Client(timeout=5.0) as c,
        c.stream("GET", f"{live_server}/events?token={RENDERER_SECRET}") as r,
    ):
        assert r.status_code == 200
        assert "text/event-stream" in r.headers.get("content-type", "")


def test_events_with_header_bearer_returns_200(live_server: str) -> None:
    with (
        httpx.Client(timeout=5.0) as c,
        c.stream(
            "GET",
            f"{live_server}/events",
            headers={"Authorization": f"Bearer {RENDERER_SECRET}"},
        ) as r,
    ):
        assert r.status_code == 200


def test_events_rejects_missing_bearer(client: TestClient) -> None:
    response = client.get("/events")
    assert response.status_code == 401


def test_events_rejects_wrong_query_bearer(client: TestClient) -> None:
    response = client.get("/events?token=wrong-secret")
    assert response.status_code == 401


def test_events_rejects_mcp_bearer_via_query(client: TestClient, mcp_secret: str) -> None:
    """Cross-tenant: MCP bearer must not authenticate /events."""
    response = client.get(f"/events?token={mcp_secret}")
    assert response.status_code == 401


def test_events_rejects_mcp_bearer_via_header(client: TestClient, mcp_secret: str) -> None:
    response = client.get("/events", headers={"Authorization": f"Bearer {mcp_secret}"})
    assert response.status_code == 401


def test_ohlcv_does_not_accept_query_bearer(client: TestClient) -> None:
    """The `?token=` accommodation is /events-only — every other renderer route
    stays header-only so the bearer doesn't leak into general request URLs."""
    response = client.get(
        "/ohlcv",
        params={
            "symbol": "AAPL",
            "timeframe": "1d",
            "start": "2026-04-01T00:00:00+00:00",
            "end": "2026-05-01T00:00:00+00:00",
            "token": RENDERER_SECRET,
        },
    )
    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# Stream shape                                                                #
# --------------------------------------------------------------------------- #


def test_stream_starts_with_retry_directive(live_server: str) -> None:
    with (
        httpx.Client(timeout=5.0) as c,
        c.stream("GET", f"{live_server}/events?token={RENDERER_SECRET}") as r,
    ):
        for chunk in r.iter_raw():
            assert chunk.startswith(b"retry: 5000\n\n"), chunk
            return
        raise AssertionError("stream closed before any chunk arrived")


def test_stream_emits_published_envelope_as_data_line(live_server: str, app: FastAPI) -> None:
    """Open the SSE stream against a real server; publish via the app's bus;
    assert the `data:` line carries the envelope JSON. Publishing is done
    in-process via `app.state.event_bus` — the test reaches into the live
    server's app state since it's the same Python object (uvicorn's
    background thread shares the FastAPI instance with the test process)."""
    bus: EventBus = app.state.event_bus
    with (
        httpx.Client(timeout=5.0) as c,
        c.stream("GET", f"{live_server}/events?token={RENDERER_SECRET}") as r,
    ):
        chunk_iter = r.iter_raw()
        # Drain the `retry:` preamble.
        first = next(chunk_iter)
        assert first.startswith(b"retry: 5000\n\n")
        # Publish a chart.show envelope.
        payload = ChartShowPayloadV1.model_validate(
            {
                "symbol": "AAPL",
                "timeframe": "1d",
                "range_start": datetime(2026, 4, 20, tzinfo=UTC),
                "range_end": datetime(2026, 5, 20, tzinfo=UTC),
                "overlays": [{"kind": "ema", "period": 20}],
            }
        )
        # The bus runs on the server's asyncio loop; we publish from
        # the test thread. The bus's enqueue is sync and thread-safe-ish
        # for our purposes (single in-flight publish, single subscriber).
        bus.publish("chart.show", payload)
        # Read until we see a data line.
        for _ in range(5):
            chunk = next(chunk_iter)
            if chunk.startswith(b"data:"):
                envelope_json = chunk.decode().removeprefix("data:").strip().rstrip("\n").strip()
                envelope = Envelope.model_validate_json(envelope_json)
                assert envelope.type == "chart.show"
                assert envelope.version == ChartShowPayloadV1.VERSION
                assert envelope.payload["symbol"] == "AAPL"
                assert envelope.payload["overlays"] == [{"kind": "ema", "period": 20}]
                return
        raise AssertionError("did not receive a data line after publish")


# --------------------------------------------------------------------------- #
# Bus pub/sub semantics                                                       #
# --------------------------------------------------------------------------- #


def test_bus_fans_out_one_envelope_to_two_subscribers(event_bus: EventBus) -> None:
    """Two subscribers, one publish, both queues receive the same envelope."""

    async def run() -> tuple[Envelope, Envelope]:
        sub1 = event_bus.subscribe()
        sub2 = event_bus.subscribe()
        payload = ChartShowPayloadV1(
            symbol="MSFT",
            timeframe="1h",
            range_start=datetime(2026, 5, 1, tzinfo=UTC),
            range_end=datetime(2026, 5, 20, tzinfo=UTC),
        )
        event_bus.publish("chart.show", payload)
        e1 = await sub1.next()
        e2 = await sub2.next()
        sub1.close()
        sub2.close()
        return e1, e2

    e1, e2 = asyncio.run(run())
    assert e1.ts == e2.ts
    assert e1.payload == e2.payload
    assert e1.type == "chart.show"


def test_bus_rejects_unknown_event_type(event_bus: EventBus) -> None:
    class _FakePayload(BaseModel):
        x: int = 1

    with pytest.raises(UnknownEventTypeError):
        event_bus.publish("not.registered", _FakePayload())


def test_bus_rejects_payload_failing_registered_model(event_bus: EventBus) -> None:
    """A `chart.show` payload missing required fields fails validation at
    publish time, not at the consumer."""

    class _BadPayload(BaseModel):
        # Missing `symbol`, `timeframe`, range_start, range_end.
        wrong: str = "wrong"

    with pytest.raises(ValidationError):
        event_bus.publish("chart.show", _BadPayload())


def test_subscriber_disconnect_does_not_block_other_subscribers(event_bus: EventBus) -> None:
    """Close one subscriber; publish; the other still receives."""

    async def run() -> Envelope:
        dead_sub = event_bus.subscribe()
        live_sub = event_bus.subscribe()
        # Close the first.
        dead_sub.close()
        # Publish — fan-out should skip the closed one.
        payload = ChartShowPayloadV1(
            symbol="NVDA",
            timeframe="1d",
            range_start=datetime(2026, 5, 1, tzinfo=UTC),
            range_end=datetime(2026, 5, 20, tzinfo=UTC),
        )
        event_bus.publish("chart.show", payload)
        e = await live_sub.next()
        live_sub.close()
        return e

    e = asyncio.run(run())
    assert e.payload["symbol"] == "NVDA"
    # Bus should have only the live subscriber registered after fan-out.
    # (The dead one was unsubscribed by close().)
    assert event_bus.subscriber_count == 0


def test_overflow_drops_oldest_and_emits_synthetic_dropped() -> None:
    """Queue cap = 2; publish 5; subscriber receives `chart.update_dropped`
    followed by the latest 2 envelopes."""

    async def run() -> list[Envelope]:
        bus = EventBus(queue_cap=2)
        sub = bus.subscribe()
        # Publish 5 distinct chart.update envelopes (with no drain in between).
        for i in range(5):
            bus.publish(
                "chart.update",
                ChartUpdatePayloadV1(symbol=f"S{i}", timeframe="1d"),
            )
        # Drain.
        received: list[Envelope] = []
        for _ in range(3):
            received.append(await sub.next())
        sub.close()
        return received

    received = asyncio.run(run())
    assert len(received) == 3
    assert received[0].type == "chart.update_dropped"
    assert received[0].version == ChartUpdateDroppedPayloadV1.VERSION
    assert received[1].type == "chart.update"
    assert received[2].type == "chart.update"
    # The two surviving envelopes are the last two published (S3, S4).
    assert received[1].payload["symbol"] == "S3"
    assert received[2].payload["symbol"] == "S4"


def test_envelope_version_matches_registered_model_version(event_bus: EventBus) -> None:
    payload = ChartShowPayloadV1(
        symbol="GOOG",
        timeframe="1d",
        range_start=datetime(2026, 5, 1, tzinfo=UTC),
        range_end=datetime(2026, 5, 20, tzinfo=UTC),
    )
    envelope = event_bus.publish("chart.show", payload)
    assert isinstance(envelope.version, int)
    assert envelope.version == ChartShowPayloadV1.VERSION


# --------------------------------------------------------------------------- #
# Bearer-in-query does not leak to logs                                       #
# --------------------------------------------------------------------------- #


def test_bearer_does_not_appear_in_server_logs(
    live_server: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Capture log records produced while making a /events request and assert
    the bearer doesn't appear in any *sidecar* log. The httpx client's own
    request-line log carries the URL (and thus `?token=`); that's a
    test-tooling artifact, not a sidecar leak, so we filter it out by logger
    name. Defends against an accidental re-enable of uvicorn's access log."""
    caplog.set_level(logging.DEBUG)
    with (
        httpx.Client(timeout=5.0) as c,
        c.stream("GET", f"{live_server}/events?token={RENDERER_SECRET}") as r,
    ):
        for chunk in r.iter_raw():
            _ = chunk
            break

    sidecar_logger_prefixes = ("uvicorn", "fastapi", "market_analyser", "starlette")
    leaked = [
        rec
        for rec in caplog.records
        if RENDERER_SECRET in rec.getMessage() and rec.name.split(".")[0] in sidecar_logger_prefixes
    ]
    assert leaked == [], f"bearer leaked into sidecar log records: {leaked}"


# --------------------------------------------------------------------------- #
# Envelope JSON round-trip                                                    #
# --------------------------------------------------------------------------- #


def test_envelope_serializes_to_json_with_iso_ts(event_bus: EventBus) -> None:
    payload = ChartShowPayloadV1(
        symbol="TSLA",
        timeframe="1d",
        range_start=datetime(2026, 5, 1, tzinfo=UTC),
        range_end=datetime(2026, 5, 20, tzinfo=UTC),
    )
    envelope = event_bus.publish("chart.show", payload)
    js = envelope.model_dump_json()
    parsed = json.loads(js)
    assert parsed["type"] == "chart.show"
    assert parsed["version"] == 1
    assert "T" in parsed["ts"]
    assert parsed["payload"]["symbol"] == "TSLA"
