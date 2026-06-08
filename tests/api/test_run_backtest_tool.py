"""Plan 0008 phase 4: the `run_backtest` MCP tool end-to-end.

Uses a real uvicorn loopback server so the Streamable HTTP transport's
chunked-body + POST-Accept semantics are exercised the same way Claude
Desktop would. Mirrors the pattern from `test_mcp_tools.py` / `test_show_tools.py`.

Done-when covered:
- happy path: tool returns {run_id, status:"complete", summary{...5 fields...}}
  with summary.trade_count matching the engine's closed-trade count.
- bus side-effect: exactly one `run.completed v1` envelope per success.
- disk + SQLite side-effects: spec.json + result.json + equity_curve.csv on
  disk; BacktestRunsRepository.get(run_id) returns a matching summary.
- unknown strategy: MCP-level error, message names the unknown id.
- invalid params (period=1 < 2): MCP-level error from the strategy boundary.
- unknown timeframe ("5m"): rejected at MCP input validation.
- determinism end-to-end: two identical calls produce identical results on
  disk (excluding run_id/started_at/finished_at).
- no event on failure: zero envelopes published when the tool raises.
"""

from __future__ import annotations

import asyncio
import json
import math
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
from market_analyser.events import Envelope, EventBus
from market_analyser.persistence.annotations_repository import AnnotationsRepository
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.repositories.backtest_runs import (
    BacktestRunsRepository,
)

RENDERER_SECRET = "renderer-test-secret"


def _bars(symbol: str = "AAPL", n: int = 80) -> list[Bar]:
    """Build a deterministic bar series with enough movement to produce signals
    for RSI(period=14)."""
    start = datetime(2026, 1, 1, tzinfo=UTC)
    bars: list[Bar] = []
    price = 100.0
    for i in range(n):
        # Alternate up/down moves with longer trends so RSI crosses 30/70.
        if i % 20 < 10:
            price += 1.0
        else:
            price -= 0.8
        bars.append(
            Bar(
                symbol=symbol,
                timeframe="1d",
                event_ts=start + timedelta(days=i),
                open=price,
                high=price + 0.5,
                low=price - 0.5,
                close=price,
                volume=1_000_000.0,
                source="test",
            ),
        )
    return bars


class _BarsProvider:
    """Returns a deterministic bar list regardless of (symbol, range)."""

    def __init__(self, bars: Sequence[Bar]) -> None:
        self.bars = list(bars)
        self.calls: list[dict[str, object]] = []

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        self.calls.append({"symbol": symbol, "timeframe": timeframe})
        return self.bars

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
def runs_dir(tmp_path: Path) -> Path:
    d = tmp_path / "runs"
    d.mkdir()
    return d


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
    # `shared_engine` typed as object because make_session_factory wants an
    # Engine; runtime is fine.
    return BacktestRunsRepository(make_session_factory(shared_engine))  # type: ignore[arg-type]


@pytest.fixture
def event_bus() -> EventBus:
    return EventBus()


@pytest.fixture
def app(
    mcp_secret: str,
    annotations_repo: AnnotationsRepository,
    repo: BacktestRunsRepository,
    runs_dir: Path,
    event_bus: EventBus,
) -> FastAPI:
    provider = _BarsProvider(bars=_bars())
    return create_app(
        secret=RENDERER_SECRET,
        mcp_secret=mcp_secret,
        provider=provider,
        annotations_repository=annotations_repo,
        backtest_runs_repository=repo,
        runs_dir=runs_dir,
        event_bus=event_bus,
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
        streamable_http_client(
            f"{url}/mcp",
            http_client=http_client,
        ) as (read_stream, write_stream, _get_session_id),
        ClientSession(read_stream, write_stream) as session,
    ):
        await session.initialize()
        yield session


async def _collect_bus_envelopes(
    bus: EventBus,
    *,
    timeout_s: float = 0.5,
) -> list[Envelope]:
    """Drain everything currently sitting in a fresh bus subscription's queue."""
    sub = bus.subscribe()
    out: list[Envelope] = []
    try:
        while True:
            try:
                envelope = await asyncio.wait_for(sub.next(), timeout=timeout_s)
            except TimeoutError:
                break
            out.append(envelope)
    finally:
        sub.close()
    return out


def _params_default() -> dict[str, object]:
    return {
        "strategy_id": "rsi",
        "symbol": "AAPL",
        "timeframe": "1d",
        "range_start": "2026-01-01T00:00:00+00:00",
        "range_end": "2026-04-30T00:00:00+00:00",
        "params": {"period": 14, "oversold": 30, "overbought": 70},
    }


def test_happy_path_returns_run_id_status_and_5_field_summary(
    live_server: str, mcp_secret: str
) -> None:
    async def _run() -> dict[str, object]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool("run_backtest", _params_default())
            assert not result.isError, f"run_backtest errored: {result.content}"
            assert result.structuredContent is not None
            return dict(result.structuredContent)

    payload = asyncio.run(_run())
    assert isinstance(payload["run_id"], str)
    assert len(payload["run_id"]) == 32  # uuid4 hex
    assert payload["status"] == "complete"
    summary = payload["summary"]
    assert isinstance(summary, dict)
    assert set(summary.keys()) == {
        "total_return",
        "sharpe",
        "max_drawdown",
        "win_rate",
        "trade_count",
    }
    assert isinstance(summary["trade_count"], int)
    assert isinstance(summary["total_return"], float)
    assert isinstance(summary["sharpe"], float)
    assert isinstance(summary["max_drawdown"], float)
    assert isinstance(summary["win_rate"], float)


def test_happy_path_persists_three_files_and_indexes_row(
    live_server: str,
    mcp_secret: str,
    runs_dir: Path,
    repo: BacktestRunsRepository,
) -> None:
    async def _run() -> str:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool("run_backtest", _params_default())
            assert not result.isError
            assert result.structuredContent is not None
            return str(result.structuredContent["run_id"])

    run_id = asyncio.run(_run())
    artifact_dir = runs_dir / run_id
    assert artifact_dir.is_dir()
    assert (artifact_dir / "spec.json").is_file()
    assert (artifact_dir / "result.json").is_file()
    assert (artifact_dir / "equity_curve.csv").is_file()
    summary = repo.get(run_id)
    assert summary is not None
    assert summary.run_id == run_id
    assert summary.strategy_id == "rsi"


def test_happy_path_publishes_exactly_one_run_completed_envelope(
    live_server: str, mcp_secret: str, event_bus: EventBus
) -> None:
    """Bus side-effect: one (and only one) `run.completed v1` per success."""

    async def _run() -> tuple[str, list[Envelope]]:
        sub = event_bus.subscribe()
        try:
            async with _mcp_session(live_server, mcp_secret) as session:
                result = await session.call_tool("run_backtest", _params_default())
                assert not result.isError
                assert result.structuredContent is not None
                run_id = str(result.structuredContent["run_id"])
            envelopes: list[Envelope] = []
            try:
                while True:
                    env = await asyncio.wait_for(sub.next(), timeout=0.5)
                    envelopes.append(env)
            except TimeoutError:
                pass
            return run_id, envelopes
        finally:
            sub.close()

    run_id, envelopes = asyncio.run(_run())
    run_completed = [e for e in envelopes if e.type == "run.completed"]
    assert len(run_completed) == 1, (
        f"expected exactly one run.completed envelope, got {len(run_completed)}: {envelopes}"
    )
    envelope = run_completed[0]
    assert envelope.version == 1
    assert envelope.payload == {
        "kind": "backtest",
        "run_id": run_id,
        "artifact_path": run_id,
    }


def test_summary_trade_count_matches_metrics_trade_count(
    live_server: str, mcp_secret: str, runs_dir: Path
) -> None:
    """summary.trade_count must equal BacktestMetrics.trade_count (closed
    trades only), not raw trade list length."""

    async def _run() -> tuple[int, dict[str, object]]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool("run_backtest", _params_default())
            assert not result.isError
            assert result.structuredContent is not None
            summary = dict(result.structuredContent["summary"])
            return summary["trade_count"], summary

    summary_count, _ = asyncio.run(_run())
    # Cross-check by reading the persisted result.json
    artifact_dirs = list(runs_dir.iterdir())
    assert len(artifact_dirs) == 1
    result_json = json.loads((artifact_dirs[0] / "result.json").read_text(encoding="utf-8"))
    assert summary_count == result_json["metrics"]["trade_count"]


def test_unknown_strategy_returns_mcp_error_with_id(live_server: str, mcp_secret: str) -> None:
    async def _run() -> tuple[bool, str]:
        async with _mcp_session(live_server, mcp_secret) as session:
            params = _params_default()
            params["strategy_id"] = "not_a_strategy"
            try:
                result = await session.call_tool("run_backtest", params)
            except Exception as exc:
                return True, str(exc)
            if result.isError:
                return True, "\n".join(str(c) for c in (result.content or []))
            return False, ""

    errored, message = asyncio.run(_run())
    assert errored, "unknown strategy must surface an MCP error"
    assert "not_a_strategy" in message


def test_invalid_params_returns_mcp_error_from_strategy_boundary(
    live_server: str, mcp_secret: str
) -> None:
    """params={'period': 1, ...} violates RSI's `period >= 2` — must surface
    the pydantic validation message."""

    async def _run() -> tuple[bool, str]:
        async with _mcp_session(live_server, mcp_secret) as session:
            params = _params_default()
            params["params"] = {"period": 1, "oversold": 30, "overbought": 70}
            try:
                result = await session.call_tool("run_backtest", params)
            except Exception as exc:
                return True, str(exc)
            if result.isError:
                return True, "\n".join(str(c) for c in (result.content or []))
            return False, ""

    errored, message = asyncio.run(_run())
    assert errored, "invalid params must surface an MCP error"
    assert "period" in message.lower()


@pytest.mark.parametrize("timeframe", ["5m", "1m"])
def test_unsupported_timeframe_returns_mcp_error_at_boundary(
    live_server: str, mcp_secret: str, timeframe: str
) -> None:
    """An unsupported timeframe is rejected at the MCP boundary by the
    BACKTEST_TIMEFRAME enum. `1m` is in the set explicitly: it used to be allowed
    (Plan 0050 phase 4 drops it — there is no 1-minute data timeframe)."""

    async def _run() -> bool:
        async with _mcp_session(live_server, mcp_secret) as session:
            params = _params_default()
            params["timeframe"] = timeframe
            try:
                result = await session.call_tool("run_backtest", params)
            except Exception:
                return True
            return bool(result.isError)

    errored = asyncio.run(_run())
    assert errored, f"timeframe={timeframe!r} must surface an MCP error"


def test_4h_timeframe_runs_end_to_end_with_finite_metrics(
    live_server: str, mcp_secret: str
) -> None:
    """A backtest on a Plan-0025 timeframe (`4h`) runs end-to-end and returns
    finite metrics — the metrics table now annualizes it (Plan 0050 phase 1)."""

    async def _run() -> dict[str, object]:
        async with _mcp_session(live_server, mcp_secret) as session:
            params = _params_default()
            params["timeframe"] = "4h"
            result = await session.call_tool("run_backtest", params)
            assert not result.isError, f"4h run errored: {result.content}"
            assert result.structuredContent is not None
            return dict(result.structuredContent)

    payload = asyncio.run(_run())
    summary = payload["summary"]
    assert isinstance(summary, dict)
    assert math.isfinite(float(summary["sharpe"]))  # type: ignore[arg-type]
    assert math.isfinite(float(summary["total_return"]))  # type: ignore[arg-type]


def test_backtest_timeframe_enum_is_the_annualizable_registry_subset() -> None:
    """The backtest tools accept exactly the timeframes the metrics layer can
    annualize, and that set is a subset of the data registry's SUPPORTED_TIMEFRAMES
    (Plan 0050 phases 4 + 4.5). The two views cannot drift: a data timeframe that
    is fetch/chart-only but not annualizable (e.g. `1mo` after phase 4.5) is
    correctly excluded from backtests, since _calc_metrics would otherwise raise.
    All three tools share the BACKTEST_TIMEFRAME alias, so this guards them all."""
    from typing import get_args

    from market_analyser.annotations.types import SUPPORTED_TIMEFRAMES
    from market_analyser.api.mcp_tools.run_backtest import BACKTEST_TIMEFRAME
    from market_analyser.backtest.metrics import _TIMEFRAME_BARS_PER_YEAR

    backtest_set = set(get_args(BACKTEST_TIMEFRAME))
    # Backtestable iff annualizable: the enum equals the metrics table's keys.
    assert backtest_set == set(_TIMEFRAME_BARS_PER_YEAR)
    # And every backtestable timeframe is a real data timeframe.
    assert backtest_set <= set(SUPPORTED_TIMEFRAMES)


def test_determinism_two_identical_calls_produce_identical_persisted_results(
    live_server: str, mcp_secret: str, runs_dir: Path
) -> None:
    """Plan §192: two consecutive calls with identical inputs produce two
    BacktestResult records on disk equal after stripping run_id/timestamps."""

    async def _run() -> tuple[str, str]:
        async with _mcp_session(live_server, mcp_secret) as session:
            r1 = await session.call_tool("run_backtest", _params_default())
            r2 = await session.call_tool("run_backtest", _params_default())
            assert not r1.isError and not r2.isError
            assert r1.structuredContent is not None
            assert r2.structuredContent is not None
            return (
                str(r1.structuredContent["run_id"]),
                str(r2.structuredContent["run_id"]),
            )

    id1, id2 = asyncio.run(_run())
    assert id1 != id2, "run_ids must differ (uuid4 hex)"

    def _strip(d: dict[str, object]) -> dict[str, object]:
        out = dict(d)
        for k in ("run_id", "started_at", "finished_at"):
            out.pop(k, None)
        return out

    r1_dict = _strip(json.loads((runs_dir / id1 / "result.json").read_text(encoding="utf-8")))
    r2_dict = _strip(json.loads((runs_dir / id2 / "result.json").read_text(encoding="utf-8")))
    assert r1_dict == r2_dict


def test_no_envelope_published_on_failure(
    live_server: str, mcp_secret: str, event_bus: EventBus
) -> None:
    """An invalid-params call must not publish a `run.completed` envelope."""

    async def _run() -> list[Envelope]:
        sub = event_bus.subscribe()
        try:
            async with _mcp_session(live_server, mcp_secret) as session:
                params = _params_default()
                params["params"] = {"period": 1}  # invalid: below RSI's period>=2
                with pytest.raises(Exception):
                    res = await session.call_tool("run_backtest", params)
                    if not res.isError:
                        raise AssertionError("expected MCP error but got success")
                    raise RuntimeError(f"MCP error: {res.content}")
            envelopes: list[Envelope] = []
            try:
                while True:
                    env = await asyncio.wait_for(sub.next(), timeout=0.3)
                    envelopes.append(env)
            except TimeoutError:
                pass
            return envelopes
        finally:
            sub.close()

    envelopes = asyncio.run(_run())
    run_completed = [e for e in envelopes if e.type == "run.completed"]
    assert run_completed == [], (
        f"expected zero run.completed envelopes on failure, got: {run_completed}"
    )
