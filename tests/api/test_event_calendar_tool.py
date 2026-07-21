"""Done-when for Plan 0113 phase 1: the `event_calendar(category=…)` tool (ADR-0107, ADR-0104).

Drives `_event_calendar_response` (the factored body) and the registered tool
(via `FastMCP.call_tool`) over a registry of pinned providers — the FOMC seed plus a
FRED adapter wired to a spy transport. Pins: with a FRED key the macro read unions
FOMC + CPI/PCE sorted by `scheduled_at`; **without the key** it is FOMC-only with an
unconfigured note (inert); conditions-only holds at the model and serialized-wire
level; an unregistered category is a clear error.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp.server.fastmcp import FastMCP

from market_analyser.api.mcp_tools.event_calendar import (
    _event_calendar_response,
    register_event_calendar,
)
from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data.adapters.fomc_seed import FomcSeedSource
from market_analyser.data.adapters.fred_releases import FredReleasesSource
from market_analyser.data.sources import EventCalendarSource
from market_analyser.persistence.secrets import SecretsStore

_FROZEN_NOW = datetime(2026, 7, 21, 0, 0, tzinfo=UTC)
_KEY_ENV = "MARKET_ANALYSER_FRED_API_KEY"
_CPI = {"release_dates": [{"release_id": 10, "date": "2026-08-12"}]}
_PCE = {"release_dates": [{"release_id": 54, "date": "2026-07-31"}]}

_ACTION_KEYS = {"action", "signal", "side", "direction", "recommendation", "call"}


def _fred(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, key: str | None
) -> FredReleasesSource:
    client = ResilientHttpClient(source_name="fred-test", max_retries=0)

    def fake(method: str, url: str, body_: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        payload = _PCE if "release_id=54" in url else _CPI
        return HttpResponse(
            status_code=200, headers={}, body=json.dumps(payload).encode(), elapsed_seconds=0.0
        )

    monkeypatch.setattr(client, "_perform_request", fake)
    environ = {_KEY_ENV: key} if key is not None else {}
    return FredReleasesSource(
        secrets_store=SecretsStore(tmp_path / "secrets.json", environ=environ),
        http_client=client,
        clock=lambda: _FROZEN_NOW,
    )


def _registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, key: str | None
) -> dict[str, list[EventCalendarSource]]:
    return {
        "macro": [
            FomcSeedSource(clock=lambda: _FROZEN_NOW),
            _fred(monkeypatch, tmp_path, key=key),
        ],
    }


def test_macro_with_key_unions_fomc_and_fred_sorted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    registry = _registry(monkeypatch, tmp_path, key="k")

    payload = anyio.run(lambda: _event_calendar_response(registry=registry, category="macro"))

    assert payload["category"] == "macro"
    scheduled = [event["scheduled_at"] for event in payload["events"]]
    assert scheduled == sorted(scheduled)  # unioned + sorted ascending
    sources = {event["source"] for event in payload["events"]}
    assert sources == {"fomc_seed", "fred"}
    titles = {event["title"] for event in payload["events"]}
    assert "CPI release" in titles
    assert any(t.startswith("PCE release") for t in titles)
    assert "FOMC meeting (rate decision)" in titles
    # queried_at is present, ISO-8601, and there is no unconfigured note (key present).
    assert datetime.fromisoformat(payload["queried_at"]).tzinfo is not None
    assert not any("fred_api_key" in note for note in payload["notes"])


def test_macro_without_key_is_fomc_only_with_note(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    registry = _registry(monkeypatch, tmp_path, key=None)

    payload = anyio.run(lambda: _event_calendar_response(registry=registry, category="macro"))

    sources = {event["source"] for event in payload["events"]}
    assert sources == {"fomc_seed"}  # FRED inert → FOMC-only
    assert any("fred_api_key" in note for note in payload["notes"])


def test_conditions_only_no_action_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = _registry(monkeypatch, tmp_path, key="k")

    payload = anyio.run(lambda: _event_calendar_response(registry=registry, category="macro"))

    assert payload["events"]
    for event in payload["events"]:  # serialized-wire level
        assert not _ACTION_KEYS & set(event)
    # top-level payload carries no action verb either
    assert not _ACTION_KEYS & set(payload)


def test_unregistered_category_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    registry = _registry(monkeypatch, tmp_path, key="k")

    with pytest.raises(ValueError, match="not supported"):
        anyio.run(lambda: _event_calendar_response(registry=registry, category="earnings"))


def test_registered_tool_is_callable_in_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    server = FastMCP(name="test")
    register_event_calendar(server, registry=_registry(monkeypatch, tmp_path, key="k"))

    result: Any = anyio.run(server.call_tool, "event_calendar", {"params": {"category": "macro"}})
    _content, structured = result

    assert structured["category"] == "macro"
    assert structured["events"]
