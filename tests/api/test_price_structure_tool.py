"""Done-when for Plan 0109 phase 4: the unified `price_structure(kind=…)` tool (ADR-0104).

The four retired single-symbol reads (`fibonacci_levels`, `pivot_points`, `anchored_vwap`,
`market_structure`) fold into one tool with a `kind` discriminator. All four returned the
same `{result, partial_reason, scanned_at}` layout, so — like the six watchlist scanners
in phase 1 — the fold flattens into `{kind, result, partial_reason, scanned_at}` (the
mode-union in the `result` field), NOT a `result.result` double-nest. Each mode section
reproduces its predecessor tool's assertions against `_price_structure_response`, and the
per-mode `result` equals the unchanged pure compute on the same bars (byte-equivalence).
One live-MCP-server test covers registration + transport under the new name.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel

from market_analyser.analysis.levels import pivot_points
from market_analyser.analysis.structure import market_structure as compute_market_structure
from market_analyser.analysis.types import AnchoredVwapValue, FibonacciLevels, MarketStructure
from market_analyser.api.app import create_app
from market_analyser.api.mcp_secret import load_or_generate_mcp_secret
from market_analyser.api.mcp_tools.price_structure import (
    AnchoredVwapOpts,
    FibonacciOpts,
    PivotsOpts,
    PriceStructureResponse,
    _price_structure_response,
)
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
_END = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _result_of[M: BaseModel](resp: PriceStructureResponse, cls: type[M]) -> M:
    """Narrow the discriminated `result` to the mode's concrete geometry model — the
    envelope's `result` is fixed by `kind`, so a populated result is always `cls`."""

    assert resp.result is not None
    assert isinstance(resp.result, cls)
    return resp.result


def _swing_bars(symbol: str, n: int = 80) -> list[Bar]:
    """A triangle-wave path (period 20, 90<->140) — repeated clear swing highs/lows so
    the dominant-swing auto-anchor and the HH/HL structure both have real pivots."""

    bars: list[Bar] = []
    for i in range(n):
        phase = i % 20
        v = 90.0 + (phase * 5.0 if phase <= 10 else (20 - phase) * 5.0)
        bars.append(
            Bar(
                symbol=symbol,
                timeframe="1d",
                event_ts=_END - timedelta(days=n - 1 - i),
                open=v,
                high=v + 0.5,
                low=v - 0.5,
                close=v,
                volume=1000.0,
                source="fixture",
            )
        )
    return bars


def _flat_bars(symbol: str, n: int = 40) -> list[Bar]:
    """A dead-flat band: no strict swing pivots, so no dominant swing to anchor to."""

    return [
        Bar(
            symbol=symbol,
            timeframe="1d",
            event_ts=_END - timedelta(days=n - 1 - i),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=1000.0,
            source="fixture",
        )
        for i in range(n)
    ]


class _SeededProvider:
    """Returns canned bars keyed by `(symbol, timeframe)`, filtered to the window and
    truncated at `as_of`. Every other Protocol method raises."""

    def __init__(self, bars_by_key: Mapping[tuple[str, str], Sequence[Bar]]) -> None:
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


# --------------------------------------------------------------------------- #
# kind="fibonacci" (was fibonacci_levels)                                       #
# --------------------------------------------------------------------------- #


def test_fibonacci_auto_anchors_on_populated_symbol() -> None:
    provider = _SeededProvider({("A", "1d"): _swing_bars("A")})
    resp = asyncio.run(
        _price_structure_response(
            provider=provider, symbol="A", timeframe="1d", kind="fibonacci", as_of=None
        )
    )
    assert resp.kind == "fibonacci"
    assert resp.partial_reason is None
    fib = _result_of(resp, FibonacciLevels)
    assert fib.kind == "retracement"
    assert set(fib.levels) == {"0.236", "0.382", "0.5", "0.618", "0.786"}
    assert resp.scanned_at.tzinfo is not None


def test_fibonacci_extension_projects_off_last_close() -> None:
    provider = _SeededProvider({("A", "1d"): _swing_bars("A")})
    resp = asyncio.run(
        _price_structure_response(
            provider=provider,
            symbol="A",
            timeframe="1d",
            kind="fibonacci",
            fibonacci=FibonacciOpts(kind="extension"),
            as_of=None,
        )
    )
    fib = _result_of(resp, FibonacciLevels)
    assert fib.kind == "extension"
    assert set(fib.levels) == {"1.272", "1.618", "2.0", "2.618"}


def test_fibonacci_no_bars() -> None:
    resp = asyncio.run(
        _price_structure_response(
            provider=_SeededProvider({}), symbol="A", timeframe="1d", kind="fibonacci", as_of=None
        )
    )
    assert resp.result is None
    assert resp.partial_reason == "no_bars"


def test_fibonacci_no_swing_on_flat_series() -> None:
    provider = _SeededProvider({("A", "1d"): _flat_bars("A")})
    resp = asyncio.run(
        _price_structure_response(
            provider=provider, symbol="A", timeframe="1d", kind="fibonacci", as_of=None
        )
    )
    assert resp.result is None
    assert resp.partial_reason == "no_swing"


# --------------------------------------------------------------------------- #
# kind="market_structure" (was market_structure)                                #
# --------------------------------------------------------------------------- #


def test_market_structure_on_populated_symbol() -> None:
    bars = _swing_bars("A")
    provider = _SeededProvider({("A", "1d"): bars})
    resp = asyncio.run(
        _price_structure_response(
            provider=provider, symbol="A", timeframe="1d", kind="market_structure", as_of=None
        )
    )
    assert resp.partial_reason is None
    ms = _result_of(resp, MarketStructure)
    assert ms.structural_trend in {"up", "down", "range"}
    assert ms == compute_market_structure(bars)  # byte-equivalent to the pure read


def test_market_structure_no_bars() -> None:
    resp = asyncio.run(
        _price_structure_response(
            provider=_SeededProvider({}),
            symbol="A",
            timeframe="1d",
            kind="market_structure",
            as_of=None,
        )
    )
    assert resp.result is None
    assert resp.partial_reason == "no_bars"


def test_market_structure_as_of_is_trailing() -> None:
    bars = _swing_bars("A")
    provider = _SeededProvider({("A", "1d"): bars})
    as_of = bars[50].event_ts
    resp = asyncio.run(
        _price_structure_response(
            provider=provider, symbol="A", timeframe="1d", kind="market_structure", as_of=as_of
        )
    )
    ms = _result_of(resp, MarketStructure)
    truncated = [b for b in bars if b.event_ts <= as_of]
    assert ms == compute_market_structure(truncated)  # no future bar leaks in
    assert all(e.bar_index < len(truncated) for e in ms.events)


# --------------------------------------------------------------------------- #
# kind="pivots" (was pivot_points)                                              #
# --------------------------------------------------------------------------- #


def test_pivots_matches_pure_read() -> None:
    bars = _swing_bars("A")
    provider = _SeededProvider({("A", "1d"): bars})
    for method in ("floor", "camarilla", "woodie"):
        resp = asyncio.run(
            _price_structure_response(
                provider=provider,
                symbol="A",
                timeframe="1d",
                kind="pivots",
                pivots=PivotsOpts(method=method),
                as_of=None,
            )
        )
        assert resp.partial_reason is None
        assert resp.result is not None
        assert resp.result == pivot_points(bars, method)  # byte-equivalent to the pure read


def test_pivots_no_bars() -> None:
    resp = asyncio.run(
        _price_structure_response(
            provider=_SeededProvider({}), symbol="A", timeframe="1d", kind="pivots", as_of=None
        )
    )
    assert resp.result is None
    assert resp.partial_reason == "no_bars"


# --------------------------------------------------------------------------- #
# kind="anchored_vwap" (was anchored_vwap)                                      #
# --------------------------------------------------------------------------- #


def test_anchored_vwap_auto_anchor_on_populated_symbol() -> None:
    provider = _SeededProvider({("A", "1d"): _swing_bars("A")})
    resp = asyncio.run(
        _price_structure_response(
            provider=provider, symbol="A", timeframe="1d", kind="anchored_vwap", as_of=None
        )
    )
    assert resp.partial_reason is None
    av = _result_of(resp, AnchoredVwapValue)
    assert av.value is not None  # volume is positive → a defined value
    assert 0 <= av.anchor_index < 80


def test_anchored_vwap_explicit_anchor() -> None:
    bars = _swing_bars("A")
    provider = _SeededProvider({("A", "1d"): bars})
    resp = asyncio.run(
        _price_structure_response(
            provider=provider,
            symbol="A",
            timeframe="1d",
            kind="anchored_vwap",
            anchored_vwap=AnchoredVwapOpts(anchor_index=0),
            as_of=None,
        )
    )
    av = _result_of(resp, AnchoredVwapValue)
    assert av.anchor_index == 0
    assert av.anchor_ts == bars[0].event_ts


def test_anchored_vwap_out_of_range_anchor_raises() -> None:
    provider = _SeededProvider({("A", "1d"): _swing_bars("A")})
    with pytest.raises(ValueError, match="out of range"):
        asyncio.run(
            _price_structure_response(
                provider=provider,
                symbol="A",
                timeframe="1d",
                kind="anchored_vwap",
                anchored_vwap=AnchoredVwapOpts(anchor_index=999),
                as_of=None,
            )
        )


def test_anchored_vwap_no_bars() -> None:
    resp = asyncio.run(
        _price_structure_response(
            provider=_SeededProvider({}),
            symbol="A",
            timeframe="1d",
            kind="anchored_vwap",
            as_of=None,
        )
    )
    assert resp.result is None
    assert resp.partial_reason == "no_bars"


# --------------------------------------------------------------------------- #
# Boundary validation + live MCP server registration                            #
# --------------------------------------------------------------------------- #


def test_price_structure_boundary_validation() -> None:
    provider = _SeededProvider({})
    for symbol, timeframe in (("", "1d"), ("A", "5m")):
        with pytest.raises(ValueError):
            asyncio.run(
                _price_structure_response(
                    provider=provider,
                    symbol=symbol,
                    timeframe=timeframe,
                    kind="pivots",
                    as_of=None,
                )
            )


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
    provider = _SeededProvider({("A", "1d"): _swing_bars("A")})
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


def test_price_structure_registered_and_callable(live_server: str, mcp_secret: str) -> None:
    """`price_structure` is registered under its documented name; the four retired names
    are gone; a per-mode call returns the discriminated `{kind, result, …}` envelope."""

    async def _run() -> None:
        async with _mcp_session(live_server, mcp_secret) as session:
            listed = {t.name for t in (await session.list_tools()).tools}
            assert "price_structure" in listed
            assert listed.isdisjoint(
                {"fibonacci_levels", "pivot_points", "anchored_vwap", "market_structure"}
            )

            fib = await session.call_tool(
                "price_structure",
                {"symbol": "A", "timeframe": "1d", "kind": "fibonacci"},
            )
            assert not fib.isError, f"tool errored: {fib.content}"
            sc = fib.structuredContent
            assert sc is not None
            assert sc["kind"] == "fibonacci"
            assert sc["result"] is not None
            assert sc["result"]["kind"] == "retracement"  # the fib grid kind, under result
            assert sc["scanned_at"]

    asyncio.run(_run())
