"""Plan 0070 phase 1 done-when: introspection reads the live surfaces correctly.

The records are asserted against the fully-wired server / app — not a fixture —
so a forgotten `register_*` call, a renamed field, or a dropped route reddens
here, the same way `tests/api/test_mcp_tools.py` guards the toolset.
"""

from __future__ import annotations

from pathlib import Path

from market_analyser.apiref.introspect import (
    introspect_events,
    introspect_routes,
    introspect_tools,
)
from market_analyser.apiref.wiring import build_wired_app, build_wired_mcp_server
from market_analyser.events import TYPE_REGISTRY
from tests.api.test_mcp_tools import EXPECTED_FULL_TOOLSET


def test_introspect_tools_matches_full_toolset(tmp_path: Path) -> None:
    server = build_wired_mcp_server(tmp_path / "runs")
    tools = introspect_tools(server)
    names = {tool.name for tool in tools}
    assert names == EXPECTED_FULL_TOOLSET, (
        f"missing: {sorted(EXPECTED_FULL_TOOLSET - names)}; "
        f"unexpected: {sorted(names - EXPECTED_FULL_TOOLSET)}"
    )
    # Every record carries a non-empty description and an ordered param tuple.
    for tool in tools:
        assert tool.description.strip(), f"{tool.name} has an empty description"
        assert isinstance(tool.params, tuple)
    # Deterministic alphabetical order (the renderer relies on it).
    assert [tool.name for tool in tools] == sorted(names)


def test_forecast_tool_record_has_expected_shape(tmp_path: Path) -> None:
    server = build_wired_mcp_server(tmp_path / "runs")
    tools = {tool.name: tool for tool in introspect_tools(server)}
    forecast = tools["forecast"]

    param_names = {param.name for param in forecast.params}
    assert {"symbol", "timeframe", "range_start", "range_end", "horizons"} <= param_names
    symbol = next(param for param in forecast.params if param.name == "symbol")
    assert symbol.required is True
    assert symbol.type_str == "string"

    # Return shape comes from the tool's real output schema.
    assert forecast.return_shape == "MultiHorizonForecastResult"
    assert forecast.source_module == "market_analyser.api.mcp_tools.forecast"
    assert forecast.source_path == "src/market_analyser/api/mcp_tools/forecast.py"


def test_introspect_routes_present_and_sorted(tmp_path: Path) -> None:
    app = build_wired_app(tmp_path / "runs")
    routes = introspect_routes(app)
    keys = {(route.path, route.method) for route in routes}

    assert ("/ohlcv", "GET") in keys
    assert ("/healthz", "GET") in keys
    assert ("/backtests/{run_id}", "GET") in keys
    assert ("/scan_patterns", "POST") in keys

    # The MCP transport is not a FastAPI route, so it is absent from this surface.
    assert not any(route.path == "/mcp" for route in routes)

    healthz = next(route for route in routes if route.path == "/healthz")
    assert "liveness" in healthz.auth
    ohlcv = next(route for route in routes if route.path == "/ohlcv")
    assert ohlcv.auth == "renderer bearer"
    assert ohlcv.response_schema == "array[Bar]"

    assert [(route.path, route.method) for route in routes] == sorted(keys)


def test_introspect_events_covers_registry(tmp_path: Path) -> None:
    events = introspect_events()
    kinds = {event.kind for event in events}

    assert kinds == set(TYPE_REGISTRY)
    assert "chart.show" in kinds
    assert "alert.triggered" in kinds

    chart_show = next(event for event in events if event.kind == "chart.show")
    assert chart_show.version == 1
    assert chart_show.source_model == "ChartShowPayloadV1"
    assert chart_show.source_path == "src/market_analyser/events/payloads.py"
    field_names = {field.name for field in chart_show.payload_fields}
    assert {"symbol", "timeframe", "range_start", "range_end"} <= field_names

    assert [event.kind for event in events] == sorted(kinds)
