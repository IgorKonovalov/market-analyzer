"""Phase-3 done-when for Plan 0077: the `forecast_volatility` / `forecast_regime` tools.

Two load-bearing guarantees:

* each tool publishes its ``*.completed v1`` envelope **exactly once** on success, strictly
  after the result is built (drained from a subscription opened before the call), and
  **zero** envelopes on failure (empty cached bars);
* both tools are **read-only** — no trade key, no order, no network write path exists in
  either tool module (a source-level scan, the same boundary `recommend` carries).

Registration in `create_mcp_components` and the `EXPECTED_FULL_TOOLSET` membership are
pinned by `tests/api/test_mcp_tools.py`; the forecasters' own correctness lives in
`tests/forecast/`. This module tests the wire wiring.
"""

from __future__ import annotations

import ast
import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path

from market_analyser.api.mcp_tools import forecast_regime as regime_tool
from market_analyser.api.mcp_tools import forecast_volatility as volatility_tool
from market_analyser.api.mcp_tools.forecast_regime import _regime_forecast_response
from market_analyser.api.mcp_tools.forecast_volatility import _volatility_forecast_response
from market_analyser.events import Envelope, EventBus
from tests.api.test_forecast_tool import BARS, _BarsProvider

RANGE_START = datetime(2024, 1, 1, tzinfo=UTC)
RANGE_END = datetime(2025, 12, 31, tzinfo=UTC)


def _drain(run: Callable[[EventBus], Awaitable[object]]) -> tuple[list[Envelope], Exception | None]:
    """Open a subscription, run ``run(bus)`` (capturing any raise so a failure path can be
    asserted), then drain everything published — the `recommend`/`forecast` test pattern,
    so nothing published before the drain can be missed."""

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


def _volatility(provider: object, bus: EventBus) -> Awaitable[object]:
    return _volatility_forecast_response(
        provider=provider,  # type: ignore[arg-type]  # a bar-only test double
        event_bus=bus,
        metric_lookup=None,
        symbol="SYN",
        timeframe="1d",
        range_start=RANGE_START,
        range_end=RANGE_END,
        horizon_bars=5,
        n_splits=4,
        seed=1729,
    )


def _regime(provider: object, bus: EventBus) -> Awaitable[object]:
    return _regime_forecast_response(
        provider=provider,  # type: ignore[arg-type]  # a bar-only test double
        event_bus=bus,
        metric_lookup=None,
        symbol="SYN",
        timeframe="1d",
        range_start=RANGE_START,
        range_end=RANGE_END,
        horizon_bars=5,
        n_splits=4,
        seed=1729,
    )


def test_volatility_tool_publishes_exactly_one_envelope_on_success() -> None:
    envelopes, error = _drain(lambda bus: _volatility(_BarsProvider(BARS), bus))
    assert error is None
    assert len(envelopes) == 1  # exactly one, not "at least one"
    assert envelopes[0].type == "volatility_forecast.completed"
    assert envelopes[0].version == 1


def test_volatility_tool_publishes_nothing_on_empty_bars() -> None:
    envelopes, error = _drain(lambda bus: _volatility(_BarsProvider([]), bus))
    assert isinstance(error, ValueError)
    assert envelopes == []  # every raise above the publish leaves the bus untouched


def test_regime_tool_publishes_exactly_one_envelope_on_success() -> None:
    envelopes, error = _drain(lambda bus: _regime(_BarsProvider(BARS), bus))
    assert error is None
    assert len(envelopes) == 1
    assert envelopes[0].type == "regime_forecast.completed"
    assert envelopes[0].version == 1


def test_regime_tool_publishes_nothing_on_empty_bars() -> None:
    envelopes, error = _drain(lambda bus: _regime(_BarsProvider([]), bus))
    assert isinstance(error, ValueError)
    assert envelopes == []


def test_nondirectional_forecast_tools_are_read_only() -> None:
    """No trade-permissioned secret, no order placement, no network write path exists in
    either tool module — source-level, so a future 'just submit it' accretion fails here
    before it ships (the ADR-0029 / ADR-0025 boundary the advisor surface also carries)."""

    sources = [Path(volatility_tool.__file__), Path(regime_tool.__file__)]

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
                        f"{source.name} imports {name!r} — a read-only forecast tool "
                        "must not reach the network or any secret store"
                    )
