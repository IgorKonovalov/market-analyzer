"""Phase-1 done-when for Plan 0021: the `multi_timeframe_analysis` MCP tool.

The body is factored into `_multi_timeframe_response` so the per-timeframe fetch,
`as_of` truncation, and missing-timeframe paths run on a single event loop. One
live-MCP-server test covers registration + transport. A `_SeededProvider` returns
canned per-(symbol, timeframe) bars honouring the window + `as_of` truncation and
satisfies the full `MarketDataProvider` Protocol so it can also back a real app.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from market_analyser.analysis.snapshot import condition_snapshot
from market_analyser.analysis.types import Trend
from market_analyser.api.app import create_app
from market_analyser.api.mcp_secret import load_or_generate_mcp_secret
from market_analyser.api.mcp_tools.multi_timeframe_analysis import (
    _multi_timeframe_response,
)
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


def _series(symbol: str, timeframe: str, *, up: bool = True, n: int = 80) -> list[Bar]:
    """A daily-spaced series ending today, rising (`up`) or falling, long enough
    that the EMA-50 / ADX trend legs are defined. Daily spacing keeps every bar
    inside even the tightest default fetch window used by the tested timeframes."""

    end = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    bars: list[Bar] = []
    for i in range(n):
        wobble = 1.5 if i % 3 == 0 else -1.0
        base = (100.0 + 0.8 * i + wobble) if up else (200.0 - 0.8 * i + wobble)
        bars.append(
            Bar(
                symbol=symbol,
                timeframe=timeframe,
                event_ts=end - timedelta(days=n - 1 - i),
                open=base,
                high=base + 1.5,
                low=base - 1.5,
                close=base + 0.6,
                volume=1_000_000.0,
                source="fixture",
            )
        )
    return bars


class _SeededProvider:
    """Returns canned bars keyed by `(symbol, timeframe)`, filtered to the
    requested window and truncated to `event_ts <= as_of` (the anti-lookahead
    replay the real provider gives via the cache). Every other Protocol method
    raises."""

    def __init__(self, bars_by_key: dict[tuple[str, str], Sequence[Bar]]) -> None:
        self._by_key = {k: list(v) for k, v in bars_by_key.items()}

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        bars = [b for b in self._by_key.get((symbol, timeframe), []) if start <= b.event_ts <= end]
        if as_of is not None:
            bars = [b for b in bars if b.event_ts <= as_of]
        return bars

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


# --------------------------------------------------------------------------- #
# Tool body — alignment over fetched bars                                       #
# --------------------------------------------------------------------------- #


def test_all_aligned_up() -> None:
    provider = _SeededProvider(
        {
            ("AAPL", "1w"): _series("AAPL", "1w", up=True),
            ("AAPL", "1d"): _series("AAPL", "1d", up=True),
            ("AAPL", "1h"): _series("AAPL", "1h", up=True),
        }
    )
    resp = asyncio.run(
        _multi_timeframe_response(
            provider=provider, symbol="AAPL", timeframes=["1w", "1d", "1h"], as_of=None
        )
    )
    al = resp.alignment
    assert al.symbol == "AAPL"
    assert [v.timeframe for v in al.timeframes] == ["1w", "1d", "1h"]
    assert all(v.snapshot is not None and v.snapshot.trend == Trend.UP for v in al.timeframes)
    assert al.dominant_trend == Trend.UP
    assert al.agreement == 1.0
    assert resp.analyzed_at.tzinfo is not None  # UTC-aware provenance stamp


def test_one_timeframe_disagrees() -> None:
    provider = _SeededProvider(
        {
            ("AAPL", "1w"): _series("AAPL", "1w", up=True),
            ("AAPL", "1d"): _series("AAPL", "1d", up=True),
            ("AAPL", "1h"): _series("AAPL", "1h", up=False),
        }
    )
    resp = asyncio.run(
        _multi_timeframe_response(
            provider=provider, symbol="AAPL", timeframes=["1w", "1d", "1h"], as_of=None
        )
    )
    al = resp.alignment
    assert al.dominant_trend == Trend.UP
    assert abs(al.agreement - (2.0 / 3.0)) < 1e-9
    dissenters = [
        v.timeframe
        for v in al.timeframes
        if v.snapshot is not None and v.snapshot.trend != Trend.UP
    ]
    assert dissenters == ["1h"]


def test_as_of_replay_truncates_per_timeframe() -> None:
    bars = _series("AAPL", "1d", up=True)
    provider = _SeededProvider({("AAPL", "1d"): bars})
    as_of = bars[50].event_ts

    resp = asyncio.run(
        _multi_timeframe_response(provider=provider, symbol="AAPL", timeframes=["1d"], as_of=as_of)
    )
    view = resp.alignment.timeframes[0]
    assert view.snapshot is not None
    assert view.snapshot.as_of == as_of
    # Equals a direct snapshot on the truncated series; differs from the full one.
    assert view.snapshot == condition_snapshot(bars[:51], "1d")
    assert view.snapshot.indicators["rsi"] != condition_snapshot(bars, "1d").indicators["rsi"]


def test_missing_timeframe_surfaces_null_not_crash() -> None:
    provider = _SeededProvider({("AAPL", "1d"): _series("AAPL", "1d", up=True)})
    # "1h" has no cached bars at all.
    resp = asyncio.run(
        _multi_timeframe_response(
            provider=provider, symbol="AAPL", timeframes=["1d", "1h"], as_of=None
        )
    )
    by_tf = {v.timeframe: v for v in resp.alignment.timeframes}
    assert by_tf["1h"].snapshot is None
    assert by_tf["1d"].snapshot is not None
    assert resp.alignment.dominant_trend == Trend.UP
    assert resp.alignment.agreement == 1.0  # the available timeframe agrees with itself


# --------------------------------------------------------------------------- #
# Boundary validation                                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("symbol", "timeframes"),
    [
        ("", ["1d"]),  # empty symbol
        ("AAPL", []),  # empty timeframe list
        ("AAPL", ["1d", "5m"]),  # unsupported timeframe in the ladder
    ],
)
def test_boundary_validation_rejects_bad_input(symbol: str, timeframes: list[str]) -> None:
    provider = _SeededProvider({("AAPL", "1d"): _series("AAPL", "1d", up=True)})
    with pytest.raises(ValueError):
        asyncio.run(
            _multi_timeframe_response(
                provider=provider, symbol=symbol, timeframes=timeframes, as_of=None
            )
        )


# --------------------------------------------------------------------------- #
# Live MCP server: registration + transport                                    #
# --------------------------------------------------------------------------- #


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
def app(mcp_secret: str, annotations_repo: AnnotationsRepository) -> FastAPI:
    provider = _SeededProvider(
        {
            ("AAPL", "1d"): _series("AAPL", "1d", up=True),
            ("AAPL", "1h"): _series("AAPL", "1h", up=True),
        }
    )
    return create_app(
        secret=RENDERER_SECRET,
        mcp_secret=mcp_secret,
        provider=provider,
        annotations_repository=annotations_repo,
    )


@pytest.fixture
def live_server(app: FastAPI) -> Iterator[str]:
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
        streamable_http_client(f"{url}/mcp", http_client=http_client) as (read, write, _sid),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


def test_multi_timeframe_via_mcp_returns_alignment(live_server: str, mcp_secret: str) -> None:
    """The tool is registered and reachable over the real MCP transport, and a
    seeded cache yields an alignment with the documented shape."""

    async def _run() -> dict[str, object]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool(
                "multi_timeframe_analysis", {"symbol": "AAPL", "timeframes": ["1d", "1h"]}
            )
            assert not result.isError, f"multi_timeframe_analysis errored: {result.content}"
            assert result.structuredContent is not None
            return dict(result.structuredContent)

    payload = asyncio.run(_run())
    assert payload["analyzed_at"]
    alignment = payload["alignment"]
    assert isinstance(alignment, dict)
    assert {"symbol", "timeframes", "dominant_trend", "agreement"} <= set(alignment)
    assert alignment["dominant_trend"] == "up"
    assert alignment["agreement"] == 1.0


def test_multi_timeframe_defaults_to_full_ladder_when_omitted(
    live_server: str, mcp_secret: str
) -> None:
    """Omitting `timeframes` applies the default weekly/daily/4h/1h/15m ladder
    (Plan 0025 unblocked the non-{1d,1h} cadences). The app fixture caches only
    1d/1h, so the other cadences come back as honest null snapshots — but the
    ladder itself is still applied across all five, in cadence-descending order."""

    async def _run() -> dict[str, object]:
        async with _mcp_session(live_server, mcp_secret) as session:
            # No `timeframes` key -> the tool's default ladder path fires.
            result = await session.call_tool("multi_timeframe_analysis", {"symbol": "AAPL"})
            assert not result.isError, f"multi_timeframe_analysis errored: {result.content}"
            assert result.structuredContent is not None
            return dict(result.structuredContent)

    payload = asyncio.run(_run())
    alignment = payload["alignment"]
    assert isinstance(alignment, dict)
    views = alignment["timeframes"]
    assert isinstance(views, list)
    assert [v["timeframe"] for v in views] == ["1w", "1d", "4h", "1h", "15m"]
    by_tf = {v["timeframe"]: v for v in views}
    # The two cached cadences resolve to real snapshots; the uncached ones are null.
    assert by_tf["1d"]["snapshot"] is not None
    assert by_tf["1h"]["snapshot"] is not None
    assert by_tf["1w"]["snapshot"] is None
    assert by_tf["4h"]["snapshot"] is None
    assert by_tf["15m"]["snapshot"] is None
    assert alignment["dominant_trend"] == "up"  # the two available cadences agree
