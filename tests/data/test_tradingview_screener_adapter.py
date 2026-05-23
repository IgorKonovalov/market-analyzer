"""Plan 0009 phase 2 — offline tests for the TradingView screener adapter.

The adapter's HTTP is driven through a `ResilientHttpClient` whose transport seam
is monkeypatched to return a captured scanner response
(`fixtures/tradingview_screener_response.json`), so the suite never touches the
network. Filter validation happens before any HTTP and is tested directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data.adapters.tradingview_screener import (
    ScreenerFilterError,
    TradingViewScreenerAdapter,
)
from market_analyser.data.default_provider import DefaultMarketDataProvider

_FIXTURE = Path(__file__).parent / "fixtures" / "tradingview_screener_response.json"


def _fixture_adapter(monkeypatch: pytest.MonkeyPatch) -> TradingViewScreenerAdapter:
    payload = _FIXTURE.read_bytes()
    client = ResilientHttpClient(source_name="tv-test")

    def fake(method: str, url: str, body: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        return HttpResponse(status_code=200, headers={}, body=payload, elapsed_seconds=0.0)

    monkeypatch.setattr(client, "_perform_request", fake)
    return TradingViewScreenerAdapter(http_client=client)


def test_adapter_parses_fixture_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _fixture_adapter(monkeypatch)

    rows = adapter.query({"RSI": {"lt": 30}}, market="america", limit=50)

    assert len(rows) == 8
    first = rows[0]
    assert first.symbol == "JPM/PK"
    assert first.fields["name"] == "JPM/PK"
    assert first.fields["close"] == 18.32
    assert first.fields["change"] == 0
    assert first.fields["volume"] == 442760
    assert first.fields["market_cap_basic"] == 820948738674
    assert first.fields["RSI"] == 26.506497189335448
    assert first.fields["exchange"] == "NYSE"


def test_adapter_rejects_unknown_filter_field() -> None:
    adapter = TradingViewScreenerAdapter()
    with pytest.raises(ScreenerFilterError) as excinfo:
        adapter.query({"unknown_field": 5})
    assert "unknown_field" in str(excinfo.value)


def test_adapter_rejects_over_limit() -> None:
    adapter = TradingViewScreenerAdapter()
    with pytest.raises(ScreenerFilterError) as excinfo:
        adapter.query({"RSI": {"lt": 30}}, limit=10_000)
    assert "500" in str(excinfo.value)  # the documented cap is named in the error


def test_adapter_rejects_unknown_market() -> None:
    adapter = TradingViewScreenerAdapter()
    with pytest.raises(ScreenerFilterError):
        adapter.query({"RSI": {"lt": 30}}, market="not_a_market")


def test_adapter_rejects_unknown_operator() -> None:
    adapter = TradingViewScreenerAdapter()
    with pytest.raises(ScreenerFilterError) as excinfo:
        adapter.query({"RSI": {"between": 30}})
    assert "between" in str(excinfo.value)


def test_provider_get_screener_delegates_to_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _fixture_adapter(monkeypatch)
    provider = DefaultMarketDataProvider(screener=adapter)

    rows = provider.get_screener({"RSI": {"lt": 30}})

    assert len(rows) == 8
    assert rows[0].symbol == "JPM/PK"


def test_provider_get_screener_rejects_as_of(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _fixture_adapter(monkeypatch)
    provider = DefaultMarketDataProvider(screener=adapter)

    with pytest.raises(ValueError, match="as_of"):
        provider.get_screener({"RSI": {"lt": 30}}, as_of=datetime(2026, 1, 1, tzinfo=UTC))
