"""Phase-2 done-when for Plan 0074: the `technical_read` MCP tool (ADR-0068).

Three load-bearing guarantees (the `recommend` / nondirectional-forecast wire pattern):

* the tool returns the `TechnicalRead` for the requested indicator, computed from the
  last CLOSED bar of the fetched series;
* it publishes its ``technical_read.completed v1`` envelope **exactly once** on success
  (drained from a subscription opened before the call), and **zero** envelopes on any
  failure — the empty-bars class and the input-validation (unknown indicator / all bars
  still forming) class alike;
* the tool + the pure core are **read-only** — no trade key, no order, no network-write
  path exists in either module (a source-level scan, the ADR-0029/0025 boundary the
  advisor surface and the forecast tools also carry).

Registration in `create_mcp_components` and `EXPECTED_FULL_TOOLSET` membership are pinned
by `tests/api/test_mcp_tools.py`; the core's regime→direction correctness lives in
`tests/advisor/test_technical_read.py`. This module tests the wire wiring.
"""

from __future__ import annotations

import ast
import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from market_analyser.advisor import technical_read as core_module
from market_analyser.advisor.models import TechnicalRead
from market_analyser.api.mcp_tools import technical_read as tool_module
from market_analyser.api.mcp_tools.technical_read import _technical_read_response
from market_analyser.events import Envelope, EventBus
from tests.api.test_forecast_tool import BARS, _BarsProvider

RANGE_START = datetime(2024, 1, 1, tzinfo=UTC)
# Well after the last synthetic bar (2025-…): every bar is closed relative to this.
NOW = datetime(2026, 1, 1, tzinfo=UTC)
# Before every synthetic bar: no bar has closed yet.
NOW_BEFORE_BARS = datetime(2024, 6, 1, tzinfo=UTC)


def _drain(run: Callable[[EventBus], Awaitable[object]]) -> tuple[list[Envelope], Exception | None]:
    """Open a subscription, run ``run(bus)`` (capturing any raise), then drain everything
    published — so nothing published before the drain can be missed."""

    bus = EventBus()

    async def _go() -> tuple[list[Envelope], Exception | None]:
        sub = bus.subscribe()
        try:
            error: Exception | None = None
            try:
                await run(bus)
            except Exception as exc:
                error = exc
            envelopes: list[Envelope] = []
            try:
                while True:
                    envelopes.append(await asyncio.wait_for(sub.next(), timeout=0.3))
            except TimeoutError:
                pass
            return envelopes, error
        finally:
            sub.close()

    return asyncio.run(_go())


def _call(
    provider: object,
    bus: EventBus,
    *,
    indicator_id: str = "supertrend",
    now: datetime = NOW,
) -> Awaitable[object]:
    return _technical_read_response(
        provider=provider,  # type: ignore[arg-type]  # a bar-only test double
        event_bus=bus,
        symbol="SYN",
        timeframe="1d",
        range_start=RANGE_START,
        indicator_id=indicator_id,
        now=now,
    )


def test_returns_technical_read_for_requested_indicator() -> None:
    bus = EventBus()
    read = asyncio.run(_call(_BarsProvider(BARS), bus))  # type: ignore[arg-type]
    assert isinstance(read, TechnicalRead)
    assert read.indicator_id == "supertrend"
    assert read.direction in {"long", "short", "flat"}
    # Computed from the last CLOSED bar (all are closed relative to NOW).
    assert read.as_of_bar_ts == BARS[-1].event_ts


def test_publishes_exactly_one_envelope_on_success() -> None:
    envelopes, error = _drain(lambda bus: _call(_BarsProvider(BARS), bus))
    assert error is None
    assert len(envelopes) == 1  # exactly one, not "at least one"
    assert envelopes[0].type == "technical_read.completed"
    assert envelopes[0].version == 1


def test_publishes_nothing_on_empty_bars() -> None:
    envelopes, error = _drain(lambda bus: _call(_BarsProvider([]), bus))
    assert isinstance(error, ValueError)
    assert envelopes == []  # every raise above the publish leaves the bus untouched


def test_publishes_nothing_on_unknown_indicator() -> None:
    envelopes, error = _drain(lambda bus: _call(_BarsProvider(BARS), bus, indicator_id="rsi"))
    assert isinstance(error, ValueError)
    assert "unknown indicator_id" in str(error)
    assert envelopes == []


def test_publishes_nothing_when_all_bars_still_forming() -> None:
    envelopes, error = _drain(lambda bus: _call(_BarsProvider(BARS), bus, now=NOW_BEFORE_BARS))
    assert isinstance(error, ValueError)
    assert envelopes == []


def test_technical_read_tool_and_core_are_read_only() -> None:
    """No trade-permissioned secret, no order placement, no network-write path exists in
    the tool module or the pure core — source-level, so a future 'just submit it'
    accretion fails here before it ships (the ADR-0029/0025 advisory boundary)."""

    sources = [Path(tool_module.__file__), Path(core_module.__file__)]

    forbidden_tokens = (
        "place_order",
        "create_order",
        "new_order",
        "submit_order",
        "x-mbx-apikey",
        "hmac",
        "api_key",
        "apikey",
        "trade_key",
        "private_key",
    )
    forbidden_imports = (
        "httpx",
        "requests",
        "urllib",
        "market_analyser.data._http",
        "market_analyser.persistence.secrets",
    )

    for source in sources:
        text = source.read_text(encoding="utf-8")
        lowered = text.lower()
        for token in forbidden_tokens:
            assert token not in lowered, f"{source.name} contains forbidden token {token!r}"
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported = [node.module or ""]
            else:
                continue
            for name in imported:
                for banned in forbidden_imports:
                    assert not name.startswith(banned), (
                        f"{source.name} imports {name!r} — a read-only advisory tool "
                        "must not reach the network or any secret store"
                    )
