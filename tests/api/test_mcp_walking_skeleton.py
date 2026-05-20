"""Plan 0006 phase 1 done-when: the MCP walking skeleton round-trips.

Covers:
- `ping(message="hi")` over the real Streamable HTTP transport against a live
  uvicorn-backed FastAPI app on an ephemeral loopback port.
- `/mcp` returns 401 without a bearer and with a wrong bearer, and the response
  body does not leak the expected secret.
- `mcp-secret.json` is created with mode 0600 on POSIX (skipped on Windows).
- Cross-tenant escalation is blocked: renderer bearer cannot authenticate
  against `/mcp`, and the MCP bearer cannot authenticate against `/ohlcv`.
"""

from __future__ import annotations

import asyncio
import socket
import stat
import sys
import threading
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from market_analyser.api.app import create_app
from market_analyser.api.mcp_secret import load_or_generate_mcp_secret
from market_analyser.data.types import (
    Bar,
    NewsItem,
    Quote,
    ScreenerRow,
    SentimentSample,
    SymbolInfo,
)

RENDERER_SECRET = "renderer-test-secret"


class _FakeProvider:
    """Minimal MarketDataProvider stub. Cross-tenant tests only hit /ohlcv with the
    wrong bearer, so they never call into this; the body is never invoked."""

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        raise AssertionError("provider should not be reached in cross-tenant tests")

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
def app(mcp_secret: str) -> FastAPI:
    return create_app(
        secret=RENDERER_SECRET,
        mcp_secret=mcp_secret,
        provider=_FakeProvider(),
    )


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.fixture
def live_server(app: FastAPI) -> Iterator[str]:
    """Run the FastAPI app under uvicorn on an ephemeral loopback port.

    The MCP Streamable HTTP transport needs a real HTTP server (chunked bodies
    and POST with the right Accept headers), so we spin up uvicorn in a thread
    rather than relying on httpx's ASGI transport.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(app, log_level="error", access_log=False)
    server = uvicorn.Server(config)

    def _run() -> None:
        asyncio.run(server.serve(sockets=[sock]))

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=2)
        raise RuntimeError("uvicorn server failed to start within 5s")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@asynccontextmanager
async def _mcp_session(url: str, bearer: str) -> AsyncIterator[ClientSession]:
    async with (
        httpx.AsyncClient(
            headers={"Authorization": f"Bearer {bearer}"},
            timeout=httpx.Timeout(30.0),
        ) as http_client,
        streamable_http_client(
            f"{url}/mcp",
            http_client=http_client,
        ) as (read_stream, write_stream, _get_session_id),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        yield session


def test_ping_roundtrip_via_streamable_http(live_server: str, mcp_secret: str) -> None:
    """An MCP client with the MCP bearer can call ping and get the echo back."""

    async def _run() -> str:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool("ping", {"message": "hi"})
            assert result.content, "tool returned no content"
            block = result.content[0]
            text_attr = getattr(block, "text", None)
            assert isinstance(text_attr, str), f"unexpected content block: {block!r}"
            return text_attr

    echoed = asyncio.run(_run())
    assert echoed == "hi"


def test_mcp_no_bearer_returns_401(client: TestClient) -> None:
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert response.status_code == 401


def test_mcp_wrong_bearer_returns_401_without_leaking_secret(
    client: TestClient, mcp_secret: str
) -> None:
    wrong = "definitely-not-the-real-secret"
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"Authorization": f"Bearer {wrong}"},
    )
    assert response.status_code == 401
    assert mcp_secret not in response.text


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file mode bits don't apply on Windows")
def test_mcp_secret_file_mode_is_0600(mcp_secret_path: Path, mcp_secret: str) -> None:
    assert mcp_secret_path.exists()
    mode_bits = stat.S_IMODE(mcp_secret_path.stat().st_mode)
    assert mode_bits == 0o600, f"expected 0600, got {oct(mode_bits)}"


def test_renderer_bearer_does_not_authenticate_mcp(client: TestClient) -> None:
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        headers={"Authorization": f"Bearer {RENDERER_SECRET}"},
    )
    assert response.status_code == 401


def test_mcp_bearer_does_not_authenticate_renderer(client: TestClient, mcp_secret: str) -> None:
    response = client.get(
        "/ohlcv",
        params={
            "symbol": "AAPL",
            "timeframe": "1d",
            "start": "2026-04-01T00:00:00+00:00",
            "end": "2026-05-01T00:00:00+00:00",
        },
        headers={"Authorization": f"Bearer {mcp_secret}"},
    )
    assert response.status_code == 401


def test_load_or_generate_is_idempotent(mcp_secret_path: Path) -> None:
    """Calling the loader twice returns the same secret — the file is the source of truth."""
    first = load_or_generate_mcp_secret(mcp_secret_path)
    second = load_or_generate_mcp_secret(mcp_secret_path)
    assert first == second
    assert len(first) == 64  # 32 bytes hex-encoded
