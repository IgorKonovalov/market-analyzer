"""Plan 0024 phase 1: Yahoo symbol-search adapter + provider method.

The search request flows through a `ResilientHttpClient` whose transport seam
(`_perform_request`) is monkeypatched to return a recorded fixture — the same
offline seam the OHLCV adapter tests use — so the real
`YahooAdapter.search` → `_fetch_yahoo_search` → client path is exercised without
the network. Covers the phase-1 done-when: canonical-pair resolution, field
mapping with name fallback, zero-match → `[]`, the `as_of` rejection, and
upstream-failure → typed error.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data.adapters.yahoo import YahooAdapter
from market_analyser.data.default_provider import DefaultMarketDataProvider
from market_analyser.data.errors import RateLimitedError, UpstreamUnavailableError
from market_analyser.data.types import SymbolInfo

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "yahoo"


def _client_returning(payload: bytes, *, status: int = 200) -> ResilientHttpClient:
    client = ResilientHttpClient(source_name="yahoo-search-test")
    client._perform_request = (  # type: ignore[method-assign]
        lambda method, url, body, headers, *, proxy: HttpResponse(
            status_code=status, headers={}, body=payload, elapsed_seconds=0.0
        )
    )
    return client


def _adapter_returning(payload: bytes, *, status: int = 200) -> YahooAdapter:
    return YahooAdapter(http_client=_client_returning(payload, status=status))


def _btc_adapter() -> YahooAdapter:
    payload = (_FIXTURE_DIR / "search_btc.json").read_bytes()
    return _adapter_returning(payload)


def test_search_returns_canonical_fetchable_pair() -> None:
    """The first/included result for `BTC` is the canonical `BTC-USD` pair that
    get_ohlcv can fetch (not a bare `BTC`), with a name and a crypto type."""
    results = _btc_adapter().search("BTC")

    assert results, "expected a non-empty result list"
    assert all(isinstance(r, SymbolInfo) for r in results)
    btc = next(r for r in results if r.symbol == "BTC-USD")
    assert btc.name == "Bitcoin USD"
    assert "crypto" in btc.quote_type.lower()


def test_search_maps_all_yahoo_fields() -> None:
    by_symbol = {r.symbol: r for r in _btc_adapter().search("BTC")}

    futures = by_symbol["BTC=F"]
    assert futures.name == "Bitcoin Futures,Mar-2026"  # shortname (no longname present)
    assert futures.exchange == "CME"  # exchDisp
    assert futures.quote_type == "Futures"  # typeDisp

    etf = by_symbol["GBTC"]
    assert etf.name == "Grayscale Bitcoin Trust ETF (BTC)"  # longname wins over shortname
    assert etf.exchange == "NASDAQ"  # exchDisp wins over raw exchange "NGM"


def test_search_quote_missing_name_falls_back_to_symbol() -> None:
    # The fixture's BTCB-USD quote carries neither longname nor shortname.
    by_symbol = {r.symbol: r for r in _btc_adapter().search("BTC")}
    assert by_symbol["BTCB-USD"].name == "BTCB-USD"


def test_search_preserves_upstream_order() -> None:
    symbols = [r.symbol for r in _btc_adapter().search("BTC")]
    assert symbols == ["BTC-USD", "BTC=F", "GBTC", "IBIT", "BTCB-USD"]


def test_search_is_byte_stable_across_calls() -> None:
    """Determinism: no set iteration / no re-sorting, so repeated calls on the
    same fixture serialise identically."""
    adapter = _btc_adapter()
    first = [r.model_dump() for r in adapter.search("BTC")]
    second = [r.model_dump() for r in adapter.search("BTC")]
    assert first == second


def test_search_empty_quotes_returns_empty_list() -> None:
    """A zero-match search is not an error — distinct from UnknownSymbolError."""
    payload = json.dumps({"quotes": [], "news": []}).encode("utf-8")
    assert _adapter_returning(payload).search("zzzznomatch") == []


def test_search_blank_query_short_circuits_without_network() -> None:
    client = ResilientHttpClient(source_name="yahoo-search-test")

    def boom(*_args: Any, **_kwargs: Any) -> HttpResponse:
        raise AssertionError("blank query must not hit the network")

    client._perform_request = boom  # type: ignore[method-assign]
    assert YahooAdapter(http_client=client).search("   ") == []


def test_search_skips_quote_without_symbol() -> None:
    payload = json.dumps(
        {"quotes": [{"shortname": "no symbol here"}, {"symbol": "ETH-USD"}]}
    ).encode("utf-8")
    results = _adapter_returning(payload).search("eth")
    assert [r.symbol for r in results] == ["ETH-USD"]


def test_search_rate_limit_raises_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    adapter = _adapter_returning(b"rate limited", status=429)
    with pytest.raises(RateLimitedError):
        adapter.search("BTC")


def test_search_upstream_5xx_raises_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    adapter = _adapter_returning(b"server error", status=503)
    with pytest.raises(UpstreamUnavailableError):
        adapter.search("BTC")


def test_provider_search_symbols_dispatches_to_adapter() -> None:
    provider = DefaultMarketDataProvider(yahoo=_btc_adapter())
    results = provider.search_symbols("BTC")
    assert any(r.symbol == "BTC-USD" for r in results)


def test_provider_search_symbols_rejects_as_of() -> None:
    provider = DefaultMarketDataProvider(yahoo=_btc_adapter())
    with pytest.raises(ValueError, match="as_of"):
        provider.search_symbols("BTC", as_of=datetime(2026, 1, 1, tzinfo=UTC))
