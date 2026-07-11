"""Plan 0007 phase 3 done-when: MCP `show_chart` / `update_chart` /
`highlight_pattern` tools.

Each tool:
- Validates inputs at the MCP boundary (Pydantic + explicit guards) — invalid
  shape surfaces as an MCP-level error to the agent, not a 500.
- Publishes exactly one envelope of the matching type.
- Returns `{event_published: True, type: ..., version: 1}` to the agent.

`highlight_pattern` additionally persists a row to the annotations table so
re-opening Electron after a session still shows the marker.

These tests reuse Plan 0006's `_mcp_session` pattern: real uvicorn on an
ephemeral port + MCP `streamable_http_client` + `ClientSession`. That way the
Streamable HTTP transport's full request/response shape is exercised the way
Claude Code would.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import ValidationError

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
from market_analyser.events import (
    ChartHighlightPayloadV1,
    ChartShowPayloadV1,
    ChartTrendlinesPayloadV1,
    ChartUpdatePayloadV1,
    Envelope,
    EventBus,
    Marker,
    OverlaySpec,
    TrendlineSpec,
    TrendPoint,
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
def live_server(app: FastAPI) -> Iterator[str]:
    """Run the FastAPI app under uvicorn on an ephemeral loopback port."""
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


def _drain_subscriber(event_bus: EventBus) -> list[Envelope]:
    """Subscribe BEFORE the test calls a tool, then drain the queue after.

    Because the bus is sync, publishing while a subscriber's queue has room
    enqueues immediately — there's no need to await. Returns whatever the
    subscriber's queue holds at call time."""
    sub = event_bus.subscribe()
    return sub, lambda: _drain_queue(sub)  # type: ignore[return-value]


def _drain_queue(sub: object) -> list[Envelope]:
    import asyncio as _asyncio

    items: list[Envelope] = []
    queue = sub.queue  # type: ignore[attr-defined]
    while True:
        try:
            items.append(queue.get_nowait())
        except _asyncio.QueueEmpty:
            break
    return items


# --------------------------------------------------------------------------- #
# show_chart                                                                  #
# --------------------------------------------------------------------------- #


def test_show_chart_publishes_chart_show_v1(
    live_server: str, mcp_secret: str, event_bus: EventBus
) -> None:
    """Subscribe to the bus from the test thread, call `show_chart` via MCP,
    then drain. Exactly one `chart.show v1` envelope with payload that
    matches the supplied args (modulo serialization)."""
    sub = event_bus.subscribe()

    async def _run() -> dict[str, object]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool(
                "show_chart",
                {
                    "symbol": "AAPL",
                    "timeframe": "1d",
                    "range_start": "2026-04-20T00:00:00+00:00",
                    "range_end": "2026-05-20T00:00:00+00:00",
                    "overlays": [{"kind": "ema", "period": 20}],
                },
            )
            assert not result.isError, f"show_chart errored: {result.content}"
            assert result.structuredContent is not None
            return dict(result.structuredContent)

    ack = asyncio.run(_run())
    assert ack == {
        "event_published": True,
        "type": "chart.show",
        "version": ChartShowPayloadV1.VERSION,
    }

    queued = _drain_queue(sub)
    assert len(queued) == 1, f"expected exactly one envelope, got {len(queued)}"
    env = queued[0]
    assert env.type == "chart.show"
    assert env.version == 1
    payload = env.payload
    assert payload["symbol"] == "AAPL"
    assert payload["timeframe"] == "1d"
    assert payload["range_start"].startswith("2026-04-20")
    assert payload["range_end"].startswith("2026-05-20")
    assert payload["overlays"] == [{"kind": "ema", "period": 20}]


def test_show_chart_publishes_price_line_overlay(
    live_server: str, mcp_secret: str, event_bus: EventBus
) -> None:
    """The new `price_line` overlay kind (Plan 0047) round-trips through
    `show_chart` onto `chart.show v1`: the S/R level the agent pushes from
    `analyze_symbol` reaches the bus intact, and an indicator overlay alongside it
    is byte-unchanged (`exclude_none` drops the price_line-only fields)."""
    sub = event_bus.subscribe()

    async def _run() -> None:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool(
                "show_chart",
                {
                    "symbol": "BTC-USD",
                    "timeframe": "1d",
                    "range_start": "2026-04-20T00:00:00+00:00",
                    "range_end": "2026-05-20T00:00:00+00:00",
                    "overlays": [
                        {"kind": "ema", "period": 20},
                        {
                            "kind": "price_line",
                            "price": 61335.75,
                            "label": "R1",
                            "role": "resistance",
                        },
                    ],
                },
            )
            assert not result.isError, f"show_chart errored: {result.content}"

    asyncio.run(_run())

    queued = _drain_queue(sub)
    assert len(queued) == 1
    env = queued[0]
    assert env.type == "chart.show"
    # Indicator overlay keeps its exact two-field wire shape; the price_line
    # carries price/label/role and drops the (unset) `period`.
    assert env.payload["overlays"] == [
        {"kind": "ema", "period": 20},
        {"kind": "price_line", "price": 61335.75, "label": "R1", "role": "resistance"},
    ]


def test_show_chart_publishes_supertrend_overlay(
    live_server: str, mcp_secret: str, event_bus: EventBus
) -> None:
    """The new `supertrend` overlay kind (Plan 0049) round-trips through
    `show_chart` onto `chart.show v1`, carrying its period + ATR multiplier; an
    `ema` overlay alongside is byte-unchanged (`exclude_none` drops the unset
    `multiplier`)."""
    sub = event_bus.subscribe()

    async def _run() -> None:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool(
                "show_chart",
                {
                    "symbol": "BTC-USD",
                    "timeframe": "1d",
                    "range_start": "2026-04-20T00:00:00+00:00",
                    "range_end": "2026-05-20T00:00:00+00:00",
                    "overlays": [
                        {"kind": "ema", "period": 20},
                        {"kind": "supertrend", "period": 10, "multiplier": 3.0},
                    ],
                },
            )
            assert not result.isError, f"show_chart errored: {result.content}"

    asyncio.run(_run())

    env = _drain_queue(sub)[0]
    assert env.type == "chart.show"
    assert env.payload["overlays"] == [
        {"kind": "ema", "period": 20},
        {"kind": "supertrend", "period": 10, "multiplier": 3.0},
    ]


# --------------------------------------------------------------------------- #
# update_chart                                                                #
# --------------------------------------------------------------------------- #


def test_update_chart_omits_unset_fields_from_payload(
    live_server: str, mcp_secret: str, event_bus: EventBus
) -> None:
    """No range/focus fields supplied → payload contains only `symbol`,
    `timeframe`, and `overlays` (no nulls). Done-when explicitly disallows
    `range_start: null` / `range_end: null` on the wire."""
    sub = event_bus.subscribe()

    async def _run() -> dict[str, object]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool(
                "update_chart",
                {
                    "symbol": "AAPL",
                    "timeframe": "1d",
                    "overlays": [{"kind": "ema", "period": 50}],
                },
            )
            assert not result.isError, f"update_chart errored: {result.content}"
            assert result.structuredContent is not None
            return dict(result.structuredContent)

    ack = asyncio.run(_run())
    assert ack == {
        "event_published": True,
        "type": "chart.update",
        "version": ChartUpdatePayloadV1.VERSION,
    }

    queued = _drain_queue(sub)
    assert len(queued) == 1
    env = queued[0]
    assert env.type == "chart.update"
    payload_keys = set(env.payload.keys())
    assert payload_keys == {"symbol", "timeframe", "overlays"}, (
        f"unset fields should not appear; got keys={payload_keys}"
    )


def test_update_chart_carries_supplied_range(
    live_server: str, mcp_secret: str, event_bus: EventBus
) -> None:
    """Sanity check the opposite direction: supplied range fields DO appear."""
    sub = event_bus.subscribe()

    async def _run() -> None:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool(
                "update_chart",
                {
                    "symbol": "AAPL",
                    "timeframe": "1d",
                    "range_start": "2026-05-10T00:00:00+00:00",
                    "range_end": "2026-05-20T00:00:00+00:00",
                },
            )
            assert not result.isError

    asyncio.run(_run())
    queued = _drain_queue(sub)
    assert len(queued) == 1
    env = queued[0]
    assert env.type == "chart.update"
    assert "range_start" in env.payload
    assert "range_end" in env.payload
    assert env.payload["range_start"].startswith("2026-05-10")
    assert env.payload["range_end"].startswith("2026-05-20")


# --------------------------------------------------------------------------- #
# highlight_pattern                                                           #
# --------------------------------------------------------------------------- #


def test_highlight_pattern_publishes_event_and_persists_annotation(
    live_server: str,
    mcp_secret: str,
    event_bus: EventBus,
    annotations_repo: AnnotationsRepository,
) -> None:
    """`highlight_pattern` does BOTH: publishes one `chart.highlight v1`
    envelope AND inserts an annotations row visible via
    `AnnotationsRepository.list_for(...)`."""
    sub = event_bus.subscribe()

    async def _run() -> dict[str, object]:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool(
                "highlight_pattern",
                {
                    "symbol": "AAPL",
                    "timeframe": "1d",
                    "event_ts": "2026-05-15T00:00:00+00:00",
                    "kind": "bullish_marker",
                    "label": "hammer at support",
                },
            )
            assert not result.isError, f"highlight_pattern errored: {result.content}"
            assert result.structuredContent is not None
            return dict(result.structuredContent)

    ack = asyncio.run(_run())
    assert ack == {
        "event_published": True,
        "type": "chart.highlight",
        "version": ChartHighlightPayloadV1.VERSION,
    }

    # Live event published.
    queued = _drain_queue(sub)
    assert len(queued) == 1
    env = queued[0]
    assert env.type == "chart.highlight"
    assert env.payload["symbol"] == "AAPL"
    assert env.payload["timeframe"] == "1d"
    assert len(env.payload["markers"]) == 1
    marker = env.payload["markers"][0]
    assert marker["kind"] == "bullish_marker"
    assert marker["label"] == "hammer at support"
    assert marker["event_ts"].startswith("2026-05-15")

    # Annotation persisted.
    listed = annotations_repo.list_for(
        symbol="AAPL",
        timeframe="1d",
        start=datetime(2026, 5, 1, tzinfo=UTC),
        end=datetime(2026, 5, 31, tzinfo=UTC),
    )
    assert len(listed) == 1
    assert listed[0].kind == "bullish_marker"
    assert listed[0].label == "hammer at support"


# --------------------------------------------------------------------------- #
# Input validation — each invalid input must surface as an MCP error          #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        # show_chart rejections
        (
            "show_chart",
            {
                "symbol": "AAPL",
                "timeframe": "5m",  # not in SUPPORTED_TIMEFRAMES
                "range_start": "2026-04-20T00:00:00+00:00",
                "range_end": "2026-05-20T00:00:00+00:00",
            },
        ),
        (
            "show_chart",
            {
                "symbol": "",  # empty
                "timeframe": "1d",
                "range_start": "2026-04-20T00:00:00+00:00",
                "range_end": "2026-05-20T00:00:00+00:00",
            },
        ),
        (
            "show_chart",
            {
                "symbol": "AAPL",
                "timeframe": "1d",
                "range_start": "2026-05-20T00:00:00+00:00",  # > range_end
                "range_end": "2026-04-20T00:00:00+00:00",
            },
        ),
        (
            "show_chart",
            {
                "symbol": "AAPL",
                "timeframe": "1d",
                "range_start": "2026-04-20T00:00:00+00:00",
                "range_end": "2026-05-20T00:00:00+00:00",
                "overlays": [{"kind": "unknown"}],  # not in literal set
            },
        ),
        (
            "show_chart",
            {
                "symbol": "AAPL",
                "timeframe": "1d",
                "range_start": "2026-04-20T00:00:00+00:00",
                "range_end": "2026-05-20T00:00:00+00:00",
                # price_line without the required `label` — the cross-field
                # validator rejects it (a labelless line is useless on the chart).
                "overlays": [{"kind": "price_line", "price": 61335.75}],
            },
        ),
        (
            "show_chart",
            {
                "symbol": "AAPL",
                "timeframe": "1d",
                "range_start": "2026-04-20T00:00:00+00:00",
                "range_end": "2026-05-20T00:00:00+00:00",
                # supertrend is an indicator kind — it must reject price_line-only
                # fields (the validator keeps the families disjoint).
                "overlays": [{"kind": "supertrend", "period": 10, "price": 100.0}],
            },
        ),
        # update_chart rejections
        (
            "update_chart",
            {"symbol": "AAPL", "timeframe": "5m"},
        ),
        (
            "update_chart",
            {"symbol": "", "timeframe": "1d"},
        ),
        (
            "update_chart",
            {
                "symbol": "AAPL",
                "timeframe": "1d",
                "overlays": [{"kind": "unknown"}],
            },
        ),
        # highlight_pattern rejections
        (
            "highlight_pattern",
            {
                "symbol": "AAPL",
                "timeframe": "5m",
                "event_ts": "2026-05-15T00:00:00+00:00",
                "kind": "bullish_marker",
            },
        ),
        (
            "highlight_pattern",
            {
                "symbol": "",
                "timeframe": "1d",
                "event_ts": "2026-05-15T00:00:00+00:00",
                "kind": "bullish_marker",
            },
        ),
    ],
)
def test_tool_rejects_invalid_input_with_mcp_error(
    live_server: str, mcp_secret: str, tool: str, args: dict[str, object]
) -> None:
    """Each invalid input must surface as an MCP-level error (isError=True),
    not as a 500. The agent should see a graceful rejection."""

    async def _run() -> bool:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool(tool, args)
            return result.isError

    is_error = asyncio.run(_run())
    assert is_error, f"{tool}({args}) should have surfaced an MCP error"


# --------------------------------------------------------------------------- #
# Plan 0025 ph3: the widened timeframe set propagates to the chart validators  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("timeframe", ["15m", "4h", "1w"])
@pytest.mark.parametrize("tool", ["show_chart", "update_chart"])
def test_chart_tools_accept_new_timeframes(
    live_server: str, mcp_secret: str, tool: str, timeframe: str
) -> None:
    """show_chart / update_chart validate via the shared `_require_supported_timeframe`,
    so widening SUPPORTED_TIMEFRAMES must make them accept 15m / 4h / 1w. Pinned
    here rather than trusting the shared import (Plan 0025 ph3 done-when)."""
    args: dict[str, object] = {"symbol": "AAPL", "timeframe": timeframe}
    if tool == "show_chart":
        args |= {
            "range_start": "2026-04-20T00:00:00+00:00",
            "range_end": "2026-05-20T00:00:00+00:00",
        }

    async def _run() -> bool:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool(tool, args)
            return result.isError

    assert asyncio.run(_run()) is False, f"{tool} should accept timeframe {timeframe}"


# --------------------------------------------------------------------------- #
# Regression: pre-existing Plan-0006 tools still work                         #
# --------------------------------------------------------------------------- #


def test_get_ohlcv_still_reachable_after_show_tools_landed(
    live_server: str, mcp_secret: str
) -> None:
    """Smoke regression: the three tools added in this phase coexist with
    Plan 0006's tools. The full Plan-0006 suite runs as part of the broader
    `tests/api/test_mcp_tools.py` file — this is a one-line probe."""

    async def _run() -> bool:
        async with _mcp_session(live_server, mcp_secret) as session:
            result = await session.call_tool(
                "get_ohlcv",
                {
                    "symbol": "AAPL",
                    "timeframe": "1d",
                    "start": "2024-04-01T00:00:00+00:00",
                    "end": "2024-05-01T00:00:00+00:00",
                },
            )
            return not result.isError

    assert asyncio.run(_run()) is True


# --------------------------------------------------------------------------- #
# Plan 0049 ph2: extended chart.highlight Marker schema (ADR-0045)            #
# Pure-pydantic round-trips — no live server needed.                          #
# --------------------------------------------------------------------------- #


def _wire(marker: Marker) -> dict[str, object]:
    """The marker as it lands on the bus wire (the bus dumps payloads with
    `mode="json", exclude_none=True`)."""
    return marker.model_dump(mode="json", exclude_none=True)


def test_marker_neutral_pattern_without_span_round_trips() -> None:
    """A neutral, named, point pattern (a doji) round-trips on `chart.highlight v1`
    without a validation error and keeps a clean wire (no null span keys)."""
    marker = Marker(
        event_ts=datetime(2026, 5, 15, tzinfo=UTC),
        kind="neutral_marker",
        pattern="doji",
        strength=0.9,
    )
    payload = ChartHighlightPayloadV1(symbol="AAPL", timeframe="1d", markers=[marker])
    reparsed = ChartHighlightPayloadV1.model_validate(payload.model_dump())
    assert reparsed.markers[0] == marker
    wire = _wire(marker)
    assert wire == {
        "event_ts": "2026-05-15T00:00:00Z",
        "kind": "neutral_marker",
        "pattern": "doji",
        "strength": 0.9,
    }
    assert "span_start_ts" not in wire and "span_end_ts" not in wire


def test_marker_with_span_round_trips() -> None:
    """A multi-bar pattern carrying a span round-trips intact."""
    marker = Marker(
        event_ts=datetime(2026, 5, 15, tzinfo=UTC),
        kind="bullish_marker",
        pattern="morning_star",
        span_start_ts=datetime(2026, 5, 13, tzinfo=UTC),
        span_end_ts=datetime(2026, 5, 15, tzinfo=UTC),
        strength=0.7,
    )
    reparsed = Marker.model_validate(marker.model_dump())
    assert reparsed == marker


def test_legacy_point_marker_still_valid_and_wire_unchanged() -> None:
    """An existing `bullish_marker` with only a label (no new fields) still
    validates and serialises to exactly its old wire shape — the new optional
    fields are dropped by `exclude_none`."""
    marker = Marker(
        event_ts=datetime(2026, 5, 15, tzinfo=UTC),
        kind="bullish_marker",
        label="hammer at support",
    )
    assert _wire(marker) == {
        "event_ts": "2026-05-15T00:00:00Z",
        "kind": "bullish_marker",
        "label": "hammer at support",
    }


def test_marker_rejects_reversed_span() -> None:
    """A span with `span_end_ts < span_start_ts` is rejected by the validator."""
    with pytest.raises(ValidationError, match="span_end_ts must be >="):
        Marker(
            event_ts=datetime(2026, 5, 15, tzinfo=UTC),
            kind="bearish_marker",
            span_start_ts=datetime(2026, 5, 15, tzinfo=UTC),
            span_end_ts=datetime(2026, 5, 13, tzinfo=UTC),
        )


def test_marker_rejects_half_span() -> None:
    """A span endpoint without its partner is rejected (both-or-neither)."""
    with pytest.raises(ValidationError, match="both span_start_ts and span_end_ts"):
        Marker(
            event_ts=datetime(2026, 5, 15, tzinfo=UTC),
            kind="bullish_marker",
            span_start_ts=datetime(2026, 5, 13, tzinfo=UTC),
        )


# --------------------------------------------------------------------------- #
# Plan 0049 ph5: the supertrend OverlaySpec kind                              #
# Pure-pydantic round-trips — no live server needed.                          #
# --------------------------------------------------------------------------- #


def test_supertrend_overlay_round_trips_with_multiplier() -> None:
    """A `supertrend` overlay carries period + multiplier and serialises to
    exactly those fields under the bus's `exclude_none` dump."""
    overlay = OverlaySpec(kind="supertrend", period=10, multiplier=3.0)
    assert OverlaySpec.model_validate(overlay.model_dump()) == overlay
    assert overlay.model_dump(mode="json", exclude_none=True) == {
        "kind": "supertrend",
        "period": 10,
        "multiplier": 3.0,
    }


def test_ema_overlay_wire_unchanged_by_multiplier_field() -> None:
    """Adding `multiplier` to the model leaves an `ema` overlay byte-unchanged on
    the wire — `exclude_none` drops the unset multiplier."""
    assert OverlaySpec(kind="ema", period=20).model_dump(mode="json", exclude_none=True) == {
        "kind": "ema",
        "period": 20,
    }


def test_supertrend_overlay_rejects_price_line_fields() -> None:
    """`supertrend` is an indicator kind: the validator still rejects the
    `price_line`-only fields on it (the families stay disjoint)."""
    for bad in ({"price": 100.0}, {"label": "x"}, {"role": "support"}):
        with pytest.raises(ValidationError, match="does not accept price/label/role"):
            OverlaySpec(kind="supertrend", period=10, **bad)


# --------------------------------------------------------------------------- #
# Plan 0073 ph3: the ichimoku OverlaySpec kind                                 #
# Pure-pydantic round-trips — no live server needed.                          #
# --------------------------------------------------------------------------- #


def test_ichimoku_overlay_round_trips_with_custom_periods() -> None:
    """An `ichimoku` overlay carries its four period fields and serialises to
    exactly the fields that were set (unset periods drop under `exclude_none`)."""
    overlay = OverlaySpec(kind="ichimoku", conversion=20, base=60, span_b=120)
    assert OverlaySpec.model_validate(overlay.model_dump()) == overlay
    assert overlay.model_dump(mode="json", exclude_none=True) == {
        "kind": "ichimoku",
        "conversion": 20,
        "base": 60,
        "span_b": 120,
    }


def test_ichimoku_overlay_bare_uses_defaults_on_the_wire() -> None:
    """A bare `ichimoku` overlay carries only `{kind}` — absent periods mean the
    renderer applies the classic 9/26/52/26 defaults."""
    assert OverlaySpec(kind="ichimoku").model_dump(mode="json", exclude_none=True) == {
        "kind": "ichimoku",
    }


def test_ema_overlay_wire_unchanged_by_ichimoku_fields() -> None:
    """Adding the ichimoku period fields to the model leaves an `ema` overlay
    byte-unchanged on the wire — `exclude_none` drops them."""
    assert OverlaySpec(kind="ema", period=20).model_dump(mode="json", exclude_none=True) == {
        "kind": "ema",
        "period": 20,
    }


def test_ichimoku_overlay_rejects_price_line_fields() -> None:
    """`ichimoku` is an indicator kind: the validator rejects the `price_line`-only
    fields on it (the families stay disjoint)."""
    for bad in ({"price": 100.0}, {"label": "x"}, {"role": "support"}):
        with pytest.raises(ValidationError, match="does not accept price/label/role"):
            OverlaySpec(kind="ichimoku", **bad)


# --------------------------------------------------------------------------- #
# Plan 0064 ph2: TrendlineSpec moves to the dedicated `chart.trendlines v1`     #
# event; the `trendlines` field is REMOVED from chart.show/chart.update         #
# (ADR-0059). Pure-pydantic round-trips — no live server needed.                #
# --------------------------------------------------------------------------- #


def test_chart_show_and_update_no_longer_declare_trendlines() -> None:
    """The `trendlines` field is gone from `chart.show`/`chart.update` — with
    `extra="forbid"`, passing it is now a validation error, and it never appears
    on the wire (ADR-0059: trendlines own their channel now)."""
    assert "trendlines" not in ChartShowPayloadV1.model_fields
    assert "trendlines" not in ChartUpdatePayloadV1.model_fields

    spec = TrendlineSpec(
        points=[
            TrendPoint(ts=datetime(2026, 4, 25, tzinfo=UTC), price=99.0),
            TrendPoint(ts=datetime(2026, 5, 5, tzinfo=UTC), price=100.0),
        ],
        style="dashed",
    )
    with pytest.raises(ValidationError):
        ChartShowPayloadV1(
            symbol="AAPL",
            timeframe="1d",
            range_start=datetime(2026, 4, 20, tzinfo=UTC),
            range_end=datetime(2026, 5, 20, tzinfo=UTC),
            trendlines=[spec],  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        ChartUpdatePayloadV1(symbol="AAPL", timeframe="1d", trendlines=[spec])  # type: ignore[call-arg]

    show = ChartShowPayloadV1(
        symbol="AAPL",
        timeframe="1d",
        range_start=datetime(2026, 4, 20, tzinfo=UTC),
        range_end=datetime(2026, 5, 20, tzinfo=UTC),
        overlays=[OverlaySpec(kind="ema", period=20)],
    )
    assert show.model_dump(mode="json", exclude_none=True) == {
        "symbol": "AAPL",
        "timeframe": "1d",
        "range_start": "2026-04-20T00:00:00Z",
        "range_end": "2026-05-20T00:00:00Z",
        "overlays": [{"kind": "ema", "period": 20}],
    }


def test_trendline_spec_round_trips_on_chart_trendlines() -> None:
    """A two-anchor dashed neckline rides the dedicated `chart.trendlines v1`
    payload intact; the payload carries symbol/timeframe/trendlines only (no
    range), version 1, and keeps a clean wire (`exclude_none` drops unset
    `label`)."""
    spec = TrendlineSpec(
        points=[
            TrendPoint(ts=datetime(2026, 4, 25, tzinfo=UTC), price=99.0),
            TrendPoint(ts=datetime(2026, 5, 5, tzinfo=UTC), price=100.0),
        ],
        role="neckline",
        style="dashed",
        pattern="head_shoulders",
    )
    payload = ChartTrendlinesPayloadV1(symbol="AAPL", timeframe="1d", trendlines=[spec])
    assert ChartTrendlinesPayloadV1.VERSION == 1
    reparsed = ChartTrendlinesPayloadV1.model_validate(payload.model_dump())
    assert reparsed.trendlines[0] == spec
    assert payload.model_dump(mode="json", exclude_none=True) == {
        "symbol": "AAPL",
        "timeframe": "1d",
        "trendlines": [
            {
                "points": [
                    {"ts": "2026-04-25T00:00:00Z", "price": 99.0},
                    {"ts": "2026-05-05T00:00:00Z", "price": 100.0},
                ],
                "role": "neckline",
                "style": "dashed",
                "pattern": "head_shoulders",
            }
        ],
    }


def test_trendline_spec_rejects_fewer_than_two_points() -> None:
    """A one-anchor (or empty) 'line' is undrawable — the validator rejects it."""
    single = [TrendPoint(ts=datetime(2026, 4, 25, tzinfo=UTC), price=99.0)]
    with pytest.raises(ValidationError, match="at least 2 points"):
        TrendlineSpec(points=single)
    with pytest.raises(ValidationError, match="at least 2 points"):
        TrendlineSpec(points=[])
