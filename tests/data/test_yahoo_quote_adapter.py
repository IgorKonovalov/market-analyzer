"""Plan 0019 phase 1: Yahoo live-quote adapter + provider wiring.

The quote request flows through a `ResilientHttpClient` whose transport seam
(`_perform_request`) is monkeypatched to return a recorded chart-`meta` fixture —
the same offline seam the OHLCV and search adapter tests use — so the real
`YahooQuoteAdapter.get_quote` → `_fetch_yahoo_quote` → client path is exercised
without the network. Covers the phase-1 done-when: field-by-field parse, the
`change_pct` derivation (proven distinct from Yahoo's own field), crypto and
after-hours `market_state`, `as_of` rejection, provider parity, blank-symbol
short-circuit, unknown-symbol typing, and upstream-failure typing.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data.adapters.yahoo_quote import YahooQuoteAdapter
from market_analyser.data.default_provider import DefaultMarketDataProvider
from market_analyser.data.errors import (
    RateLimitedError,
    UnknownSymbolError,
    UpstreamUnavailableError,
)

_FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _client_returning(payload: bytes, *, status: int = 200) -> ResilientHttpClient:
    client = ResilientHttpClient(source_name="yahoo-quote-test")
    client._perform_request = (  # type: ignore[method-assign]
        lambda method, url, body, headers, *, proxy: HttpResponse(
            status_code=status, headers={}, body=payload, elapsed_seconds=0.0
        )
    )
    return client


def _adapter_for(fixture: str, *, status: int = 200) -> YahooQuoteAdapter:
    payload = (_FIXTURE_DIR / fixture).read_bytes()
    return YahooQuoteAdapter(http_client=_client_returning(payload, status=status))


def test_regular_session_quote_parses_all_fields() -> None:
    quote = _adapter_for("yahoo_quote_aapl.json").get_quote("AAPL")

    assert quote.symbol == "AAPL"
    assert quote.price == 189.95
    assert quote.previous_close == 191.0
    assert quote.day_high == 190.12
    assert quote.day_low == 188.0
    assert quote.week52_high == 199.62
    assert quote.week52_low == 164.08
    assert quote.volume == 48000000.0
    assert quote.currency == "USD"
    assert quote.market_state == "REGULAR"
    assert quote.source == "yahoo"
    assert quote.as_of == datetime.fromtimestamp(1716544800, tz=UTC)


def test_change_pct_is_derived_from_previous_close_not_yahoos_field() -> None:
    """The fixture carries a bogus `regularMarketChangePercent: 99.0`; the adapter
    must ignore it and derive change_pct from `previous_close` instead (Plan 0019
    risk decision)."""
    quote = _adapter_for("yahoo_quote_aapl.json").get_quote("AAPL")

    expected = (189.95 / 191.0 - 1.0) * 100.0
    assert quote.change_pct == pytest.approx(expected)
    assert quote.change_pct != pytest.approx(99.0)
    assert quote.change_pct is not None and quote.change_pct < 0  # price below prev close


def test_crypto_quote_parses_and_sets_market_state() -> None:
    quote = _adapter_for("yahoo_quote_btc.json").get_quote("BTC-USD")

    assert quote.symbol == "BTC-USD"
    assert quote.price == 68250.5
    assert quote.currency == "USD"
    assert quote.market_state == "REGULAR"
    assert quote.change_pct == pytest.approx((68250.5 / 67500.0 - 1.0) * 100.0)


def test_after_hours_quote_folds_postpost_to_post() -> None:
    """Yahoo's extended-session `POSTPOST` collapses onto the plan's `POST`."""
    quote = _adapter_for("yahoo_quote_msft_afterhours.json").get_quote("MSFT")

    assert quote.market_state == "POST"
    assert quote.price == 430.16  # regularMarketPrice — not the postMarketPrice
    assert quote.previous_close == 427.0


@pytest.mark.parametrize(
    ("yahoo_state", "expected"),
    [
        ("REGULAR", "REGULAR"),
        ("PRE", "PRE"),
        ("PREPRE", "PRE"),
        ("POST", "POST"),
        ("POSTPOST", "POST"),
        ("CLOSED", "CLOSED"),
        ("UNKNOWN", ""),
    ],
)
def test_market_state_normalisation(yahoo_state: str, expected: str) -> None:
    payload = json.dumps(
        {
            "chart": {
                "result": [{"meta": {"regularMarketPrice": 10.0, "marketState": yahoo_state}}],
                "error": None,
            }
        }
    ).encode("utf-8")
    quote = YahooQuoteAdapter(http_client=_client_returning(payload)).get_quote("X")
    assert quote.market_state == expected


def test_blank_symbol_short_circuits_without_network() -> None:
    def boom(_symbol: str) -> dict[str, Any]:
        raise AssertionError("blank symbol must not hit the network")

    adapter = YahooQuoteAdapter(fetcher=boom)
    with pytest.raises(ValueError, match="non-empty"):
        adapter.get_quote("   ")


def test_unknown_symbol_null_result_raises_typed_error() -> None:
    payload = json.dumps({"chart": {"result": None, "error": {"code": "Not Found"}}}).encode(
        "utf-8"
    )
    adapter = YahooQuoteAdapter(http_client=_client_returning(payload))
    with pytest.raises(UnknownSymbolError) as excinfo:
        adapter.get_quote("nope")
    assert excinfo.value.symbol == "NOPE"


def test_meta_without_price_raises_unknown_symbol() -> None:
    payload = json.dumps(
        {"chart": {"result": [{"meta": {"currency": "USD"}}], "error": None}}
    ).encode("utf-8")
    adapter = YahooQuoteAdapter(http_client=_client_returning(payload))
    with pytest.raises(UnknownSymbolError):
        adapter.get_quote("AAPL")


def test_rate_limit_raises_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    adapter = _adapter_for("yahoo_quote_aapl.json", status=429)
    with pytest.raises(RateLimitedError):
        adapter.get_quote("AAPL")


def test_upstream_5xx_raises_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    adapter = _adapter_for("yahoo_quote_aapl.json", status=503)
    with pytest.raises(UpstreamUnavailableError):
        adapter.get_quote("AAPL")


def test_provider_get_quote_matches_direct_adapter_call() -> None:
    """Provider parity: `DefaultMarketDataProvider().get_quote` returns the same
    Quote as the direct adapter call on the same mocked client."""
    direct = _adapter_for("yahoo_quote_aapl.json").get_quote("AAPL")
    provider = DefaultMarketDataProvider(yahoo_quote=_adapter_for("yahoo_quote_aapl.json"))
    assert provider.get_quote("AAPL").model_dump() == direct.model_dump()


def test_provider_get_quote_rejects_as_of() -> None:
    provider = DefaultMarketDataProvider(yahoo_quote=_adapter_for("yahoo_quote_aapl.json"))
    with pytest.raises(ValueError, match="as_of"):
        provider.get_quote("AAPL", as_of=datetime(2026, 1, 1, tzinfo=UTC))
