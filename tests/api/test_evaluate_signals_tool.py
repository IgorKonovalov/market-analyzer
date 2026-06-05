"""Plan 0026 phase 2: the `evaluate_signals` MCP tool.

The body is factored into `_evaluate_signals_response` so the validation /
fetch / publish paths run on a single event loop with an injectable `now`
(deterministic, no live server). One live-MCP-server section covers registration
+ transport + the strict-input-schema rejection of `as_of`/`range_end` and the
no-persistence guarantee.

Done-when covered:
- happy path: body returns a SignalEvaluation whose fields match the phase-1
  core run on the same bars (and asserts the concrete values, not just equality).
- bus side-effect: exactly one `signal.evaluated v1` envelope per success,
  carrying that SignalEvaluation inline; nothing published on any failure.
- unknown strategy_id / bad timeframe / invalid params: ValueError at the
  boundary, zero envelopes.
- as_of / range_end keys are IGNORED (not rejected) at the MCP boundary, and
  cannot change the now-read result — the anti-lookahead safety property
  (amended 2026-06-05; FastMCP does not enforce extra="forbid").
- the tool persists nothing (no runs/ artifact, no SQLite row).
- the registered toolset still lists the pre-existing tools.
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
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import ValidationError

from market_analyser.api.app import create_app
from market_analyser.api.mcp_secret import load_or_generate_mcp_secret
from market_analyser.api.mcp_tools.evaluate_signals import _evaluate_signals_response
from market_analyser.backtest import evaluate_signals as evaluate_signals_core
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
from market_analyser.events import Envelope, EventBus, SignalEvaluatedPayloadV1
from market_analyser.persistence.annotations_repository import AnnotationsRepository
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repositories.backtest_runs import (
    BacktestRunsRepository,
)
from market_analyser.strategies import rsi

RENDERER_SECRET = "renderer-test-secret"
_FIXED_NOW = datetime(2030, 1, 1, tzinfo=UTC)
_RANGE_START = datetime(2026, 1, 1, tzinfo=UTC)
_DEFAULT_PARAMS: dict[str, Any] = {"period": 14, "oversold": 40.0, "overbought": 60.0}


def _declining_bars(symbol: str = "TEST", n: int = 15) -> list[Bar]:
    """A strictly declining daily series: RSI is undefined until the last bar
    (period 14), where a pure decline yields RSI 0 (below oversold 40) on the
    first computable bar — a fresh ENTER_LONG on the last closed bar."""

    out: list[Bar] = []
    for i in range(n):
        price = 100.0 - i
        out.append(
            Bar(
                symbol=symbol,
                timeframe="1d",
                event_ts=_RANGE_START + timedelta(days=i),
                open=price,
                high=price,
                low=price,
                close=price,
                volume=0.0,
                source="fixture",
            )
        )
    return out


class _SeededProvider:
    """Returns its canned bars for the seeded symbol (honouring `as_of`
    truncation); every other Protocol method raises. Deliberately does NOT
    implement the `SupportsBackfill` capability, so `create_app` builds no
    coordinator and the tool exercises the plain provider-fetch fallback."""

    def __init__(self, bars: Sequence[Bar]) -> None:
        self._bars = list(bars)
        self._symbol = bars[0].symbol if bars else ""

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        if symbol != self._symbol:
            return []
        bars = self._bars
        if as_of is not None:
            bars = [b for b in bars if b.event_ts <= as_of]
        return list(bars)

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


async def _drain(bus: EventBus, *, timeout_s: float = 0.3) -> list[Envelope]:
    """Drain everything currently queued in a fresh subscription."""
    sub = bus.subscribe()
    out: list[Envelope] = []
    try:
        while True:
            try:
                out.append(await asyncio.wait_for(sub.next(), timeout=timeout_s))
            except TimeoutError:
                break
    finally:
        sub.close()
    return out


# --------------------------------------------------------------------------- #
# Tool body                                                                    #
# --------------------------------------------------------------------------- #


def test_happy_path_matches_phase1_core_and_reports_fresh_long() -> None:
    provider = _SeededProvider(_declining_bars())
    bus = EventBus()

    evaluation = asyncio.run(
        _evaluate_signals_response(
            provider=provider,
            coordinator=None,
            event_bus=bus,
            strategy_id="rsi",
            symbol="TEST",
            timeframe="1d",
            range_start=_RANGE_START,
            params=dict(_DEFAULT_PARAMS),
            now=_FIXED_NOW,
        )
    )

    # Equality with the phase-1 core over the same bars + now (the tool is a thin
    # wire around it) ...
    expected = evaluate_signals_core(
        rsi, provider._bars, rsi.Params(**_DEFAULT_PARAMS), now=_FIXED_NOW
    )
    assert evaluation == expected

    # ... and the concrete values, so this is not just two equal computations.
    assert evaluation.strategy_id == "rsi"
    assert evaluation.symbol == "TEST"
    assert evaluation.timeframe == "1d"
    assert evaluation.current_position == "long"
    assert evaluation.last_signal is not None
    assert evaluation.last_signal.kind.value == "enter_long"
    assert evaluation.last_signal.bar_index == 14
    assert evaluation.fresh_signal is True
    assert evaluation.bars_since_last_signal == 0
    assert evaluation.closed_bar_count == 15
    assert evaluation.latest_bar_excluded_as_forming is False


def test_publishes_exactly_one_signal_evaluated_envelope_inline() -> None:
    provider = _SeededProvider(_declining_bars())
    bus = EventBus()

    async def _run() -> tuple[object, list[Envelope]]:
        sub = bus.subscribe()
        try:
            evaluation = await _evaluate_signals_response(
                provider=provider,
                coordinator=None,
                event_bus=bus,
                strategy_id="rsi",
                symbol="TEST",
                timeframe="1d",
                range_start=_RANGE_START,
                params=dict(_DEFAULT_PARAMS),
                now=_FIXED_NOW,
            )
            envelopes: list[Envelope] = []
            try:
                while True:
                    envelopes.append(await asyncio.wait_for(sub.next(), timeout=0.3))
            except TimeoutError:
                pass
            return evaluation, envelopes
        finally:
            sub.close()

    evaluation, envelopes = asyncio.run(_run())
    evaluated = [e for e in envelopes if e.type == "signal.evaluated"]
    assert len(evaluated) == 1, f"expected exactly one signal.evaluated, got {envelopes}"
    envelope = evaluated[0]
    assert envelope.version == 1
    # The full evaluation rides inline — compare against the same wire transform
    # the bus applies (mode=json, exclude_none).
    expected_payload = SignalEvaluatedPayloadV1(evaluation=evaluation).model_dump(  # type: ignore[arg-type]
        mode="json", exclude_none=True
    )
    assert envelope.payload == expected_payload
    assert envelope.payload["evaluation"]["current_position"] == "long"
    assert envelope.payload["evaluation"]["fresh_signal"] is True


def test_unknown_strategy_raises_and_publishes_nothing() -> None:
    provider = _SeededProvider(_declining_bars())
    bus = EventBus()

    async def _run() -> list[Envelope]:
        with pytest.raises(ValueError, match="unknown strategy_id"):
            await _evaluate_signals_response(
                provider=provider,
                coordinator=None,
                event_bus=bus,
                strategy_id="not_a_strategy",
                symbol="TEST",
                timeframe="1d",
                range_start=_RANGE_START,
                params=dict(_DEFAULT_PARAMS),
                now=_FIXED_NOW,
            )
        return await _drain(bus)

    assert asyncio.run(_run()) == []


def test_timeframe_not_in_strategy_meta_raises_naming_supported_set() -> None:
    provider = _SeededProvider(_declining_bars())
    bus = EventBus()

    async def _run() -> list[Envelope]:
        # 15m is a globally-supported timeframe but NOT in rsi.META.timeframes
        # (("1h", "1d")) — the per-strategy check rejects it.
        with pytest.raises(ValueError, match=r"not supported by strategy 'rsi'.*1h.*1d"):
            await _evaluate_signals_response(
                provider=provider,
                coordinator=None,
                event_bus=bus,
                strategy_id="rsi",
                symbol="TEST",
                timeframe="15m",
                range_start=_RANGE_START,
                params=dict(_DEFAULT_PARAMS),
                now=_FIXED_NOW,
            )
        return await _drain(bus)

    assert asyncio.run(_run()) == []


def test_globally_unsupported_timeframe_raises() -> None:
    provider = _SeededProvider(_declining_bars())
    bus = EventBus()
    with pytest.raises(ValueError, match="not supported"):
        asyncio.run(
            _evaluate_signals_response(
                provider=provider,
                coordinator=None,
                event_bus=bus,
                strategy_id="rsi",
                symbol="TEST",
                timeframe="5m",  # not in the registry at all
                range_start=_RANGE_START,
                params=dict(_DEFAULT_PARAMS),
                now=_FIXED_NOW,
            )
        )


@pytest.mark.parametrize(
    "bad_params",
    [
        {"period": 1, "oversold": 40.0, "overbought": 60.0},  # period < 2 (out of range)
        {"period": 14, "oversold": 40.0, "overbought": 60.0, "bogus": 5},  # extra key (forbid)
    ],
)
def test_invalid_params_raise_at_boundary_and_publish_nothing(
    bad_params: dict[str, object],
) -> None:
    provider = _SeededProvider(_declining_bars())
    bus = EventBus()

    async def _run() -> list[Envelope]:
        # Both cases (period<2, extra key under extra="forbid") raise pydantic's
        # ValidationError from strategy_module.Params(**params) at the boundary.
        with pytest.raises(ValidationError):
            await _evaluate_signals_response(
                provider=provider,
                coordinator=None,
                event_bus=bus,
                strategy_id="rsi",
                symbol="TEST",
                timeframe="1d",
                range_start=_RANGE_START,
                params=bad_params,
                now=_FIXED_NOW,
            )
        return await _drain(bus)

    assert asyncio.run(_run()) == []


# --------------------------------------------------------------------------- #
# Live MCP server: registration + transport + strict input + no persistence    #
# --------------------------------------------------------------------------- #


@pytest.fixture
def mcp_secret(tmp_path: Path) -> str:
    return load_or_generate_mcp_secret(tmp_path / "mcp-secret.json")


@pytest.fixture
def annotations_repo() -> Iterator[AnnotationsRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield AnnotationsRepository(make_session_factory(engine))
    engine.dispose()


@pytest.fixture
def shared_engine() -> Iterator[object]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def repo(shared_engine: object) -> BacktestRunsRepository:
    return BacktestRunsRepository(make_session_factory(shared_engine))  # type: ignore[arg-type]


@pytest.fixture
def runs_dir(tmp_path: Path) -> Path:
    d = tmp_path / "runs"
    d.mkdir()
    return d


@pytest.fixture
def app(
    mcp_secret: str,
    annotations_repo: AnnotationsRepository,
    repo: BacktestRunsRepository,
    runs_dir: Path,
) -> FastAPI:
    return create_app(
        secret=RENDERER_SECRET,
        mcp_secret=mcp_secret,
        provider=_SeededProvider(_declining_bars()),
        annotations_repository=annotations_repo,
        backtest_runs_repository=repo,
        runs_dir=runs_dir,
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


def _call_args(**overrides: object) -> dict[str, object]:
    args: dict[str, object] = {
        "strategy_id": "rsi",
        "symbol": "TEST",
        "timeframe": "1d",
        "range_start": "2026-01-01T00:00:00+00:00",
        "params": {"period": 14, "oversold": 40, "overbought": 60},
    }
    args.update(overrides)
    return args


def test_live_happy_path_returns_evaluation(live_server: str, mcp_secret: str) -> None:
    async def _run() -> dict[str, object]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool("evaluate_signals", _call_args())
            assert not result.isError, f"evaluate_signals errored: {result.content}"
            assert result.structuredContent is not None
            return dict(result.structuredContent)

    payload = asyncio.run(_run())
    assert payload["strategy_id"] == "rsi"
    assert payload["symbol"] == "TEST"
    assert payload["current_position"] in {"flat", "long"}
    assert isinstance(payload["fresh_signal"], bool)
    assert "evaluated_through_ts" in payload
    assert "latest_bar_excluded_as_forming" in payload


def test_live_toolset_lists_evaluate_signals_and_preexisting(
    live_server: str, mcp_secret: str
) -> None:
    async def _run() -> set[str]:
        async with _mcp_session(live_server, mcp_secret) as session:
            tools = await session.list_tools()
            return {t.name for t in tools.tools}

    names = asyncio.run(_run())
    assert "evaluate_signals" in names
    # No regression to the Plan 0017 hub: pre-existing tools still registered.
    assert {"get_ohlcv", "run_backtest", "analyze_symbol"} <= names


@pytest.mark.parametrize("extra_key", ["as_of", "range_end"])
def test_live_extra_key_is_ignored_now_read_unaffected(
    live_server: str, mcp_secret: str, extra_key: str
) -> None:
    """Anti-lookahead safety property (Plan 0026, amended 2026-06-05).

    FastMCP's generated input model does not enforce ``extra="forbid"`` (its
    ``ArgModelBase`` omits it, no per-tool hook), so a stray ``as_of`` /
    ``range_end`` key is *ignored*, not rejected. The guarantee that actually
    matters is unaffected: the read is structurally a now-read (the tool declares
    no such param and always uses ``datetime.now(UTC)``), so the key cannot
    influence the result. We pass a PAST date that *would* truncate the series to
    too-few bars if it were honoured (flipping the result to flat/no-signal), and
    assert the evaluation is identical to the no-key call — proving it is ignored.
    """

    async def _run() -> tuple[dict[str, object], dict[str, object]]:
        async with _mcp_session(live_server, mcp_secret) as session:
            base = await session.call_tool("evaluate_signals", _call_args())
            assert not base.isError, f"baseline call errored: {base.content}"
            with_key = await session.call_tool(
                "evaluate_signals", _call_args(**{extra_key: "2026-01-05T00:00:00+00:00"})
            )
            assert not with_key.isError, f"{extra_key} call errored: {with_key.content}"
            assert base.structuredContent is not None
            assert with_key.structuredContent is not None
            return dict(base.structuredContent), dict(with_key.structuredContent)

    base_payload, with_key_payload = asyncio.run(_run())
    assert with_key_payload == base_payload, (
        f"a {extra_key!r} key must be ignored (the read is always a now-read); "
        f"it changed the evaluation: {base_payload} != {with_key_payload}"
    )


def test_live_persists_nothing(
    live_server: str, mcp_secret: str, runs_dir: Path, repo: BacktestRunsRepository
) -> None:
    async def _run() -> None:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool("evaluate_signals", _call_args())
            assert not result.isError

    asyncio.run(_run())
    assert list(runs_dir.iterdir()) == [], "evaluate_signals must not write a runs/ artifact"
    assert repo.list(limit=10) == [], "evaluate_signals must not index a backtest row"
