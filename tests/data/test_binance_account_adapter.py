"""Binance read-only account adapter tests (Plan 0041 phase 1).

The done-when, read at the assertion level:

(a) against a fixture of Binance account responses, the adapter returns
    balances/positions with quantities and entry prices (futures flagged
    distinctly from spot — the plan's open question resolved as proposed);
(b) the read-only credential is sourced from the ADR-0038 store and never
    logged/echoed — asserted over debug logs, reprs, error strings, and the
    outbound URLs (the key travels only in the header, the secret only as
    HMAC material);
(c) a missing key yields the typed `BinanceAccountAuthError` without a single
    network attempt, not a crash;
(d) no order/write endpoint is reachable from this adapter — an AST scan
    pins "GET-only, exactly the two read URLs" at the source level.
"""

from __future__ import annotations

import ast
import hashlib
import hmac
import json
import logging
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pydantic
import pytest

from market_analyser.data._http import HttpResponse
from market_analyser.data.adapters import binance_account
from market_analyser.data.adapters.binance_account import (
    _FUTURES_POSITIONS_URL,
    _SPOT_ACCOUNT_URL,
    BinanceAccountAdapter,
    BinanceAccountAuthError,
    BinanceAccountError,
    BinanceAccountHttpClient,
)
from market_analyser.data.errors import GeoRestrictedError, RateLimitedError
from market_analyser.data.sources import AccountHoldingsSource
from market_analyser.persistence.secrets import SecretsStore

_KEY = "bnb_read_key_value_a1b2c3"
_SECRET = "bnb_read_secret_value_d4e5f6"
_CLOCK_EPOCH = 1_750_000_000.0  # 2025-06-15T15:06:40Z — fixed test instant


def _spot_payload() -> dict[str, Any]:
    """Shape of `GET /api/v3/account`: decimal-string balances, zero lines
    included (upstream lists every asset ever held)."""
    return {
        "makerCommission": 10,
        "canTrade": True,
        "updateTime": 1_719_878_400_000,
        "balances": [
            {"asset": "BTC", "free": "0.50000000", "locked": "0.00000000"},
            {"asset": "USDT", "free": "1000.00000000", "locked": "50.00000000"},
            {"asset": "ETH", "free": "0.00000000", "locked": "0.00000000"},
        ],
    }


def _futures_payload() -> list[dict[str, Any]]:
    """Shape of `GET /fapi/v2/positionRisk`: one long, one short, one flat row
    (positionRisk lists every contract ever touched; flat rows carry a zero
    entryPrice and must be dropped before the gt-0 boundary sees them)."""
    return [
        {
            "symbol": "BTCUSDT",
            "positionAmt": "0.010",
            "entryPrice": "60000.0",
            "markPrice": "61000.00000000",
            "unRealizedProfit": "10.00000000",
            "positionSide": "BOTH",
            "updateTime": 1_719_878_400_000,
        },
        {
            "symbol": "ETHUSDT",
            "positionAmt": "-0.500",
            "entryPrice": "3000.0",
            "markPrice": "2950.00000000",
            "unRealizedProfit": "25.00000000",
            "positionSide": "BOTH",
            "updateTime": 1_719_878_400_000,
        },
        {
            "symbol": "SOLUSDT",
            "positionAmt": "0",
            "entryPrice": "0.0",
            "markPrice": "0.00000000",
            "unRealizedProfit": "0.00000000",
            "positionSide": "BOTH",
            "updateTime": 0,
        },
    ]


class _FakeAccountTransport:
    """Replaces `ResilientHttpClient._perform_request` (the transport seam),
    routing by URL path to the spot / futures fixture and recording every
    outbound request's URL and headers."""

    def __init__(self, spot: Any | None = None, futures: Any | None = None) -> None:
        self._spot = spot if spot is not None else _spot_payload()
        self._futures = futures if futures is not None else _futures_payload()
        self.requests: list[tuple[str, dict[str, str]]] = []

    def __call__(
        self, method: str, url: str, body: Any, headers: Any, *, proxy: Any
    ) -> HttpResponse:
        assert method == "GET"
        self.requests.append((url, dict(headers or {})))
        path = urllib.parse.urlsplit(url).path
        if path == urllib.parse.urlsplit(_SPOT_ACCOUNT_URL).path:
            payload: Any = self._spot
        elif path == urllib.parse.urlsplit(_FUTURES_POSITIONS_URL).path:
            payload = self._futures
        else:  # pragma: no cover - a third endpoint would be a read-only breach
            raise AssertionError(f"unexpected endpoint: {url}")
        return HttpResponse(
            status_code=200,
            headers={},
            body=json.dumps(payload).encode("utf-8"),
            elapsed_seconds=0.0,
        )


class _StaticTransport:
    """Always returns one canned response, counting attempts."""

    def __init__(self, status_code: int, body: bytes) -> None:
        self._status_code = status_code
        self._body = body
        self.attempts = 0

    def __call__(
        self, method: str, url: str, body: Any, headers: Any, *, proxy: Any
    ) -> HttpResponse:
        self.attempts += 1
        return HttpResponse(
            status_code=self._status_code,
            headers={},
            body=self._body,
            elapsed_seconds=0.0,
        )


@pytest.fixture
def secrets(tmp_path: Path) -> SecretsStore:
    store = SecretsStore(tmp_path / "secrets.json", environ={})
    store.set("binance_read_api_key", _KEY)
    store.set("binance_read_api_secret", _SECRET)
    return store


def _adapter(
    monkeypatch: pytest.MonkeyPatch,
    secrets: SecretsStore,
    *,
    transport: Any | None = None,
    max_retries: int = 0,
) -> tuple[BinanceAccountAdapter, Any]:
    client = BinanceAccountHttpClient(
        source_name="binance-account-test",
        cache_ttl_seconds=0.0,
        max_retries=max_retries,
    )
    fake = transport if transport is not None else _FakeAccountTransport()
    monkeypatch.setattr(client, "_perform_request", fake)
    adapter = BinanceAccountAdapter(
        secrets_store=secrets,
        http_client=client,
        clock=lambda: _CLOCK_EPOCH,
    )
    return adapter, fake


# --- contract -----------------------------------------------------------------


def test_adapter_satisfies_account_holdings_source_protocol(
    monkeypatch: pytest.MonkeyPatch, secrets: SecretsStore
) -> None:
    adapter, _ = _adapter(monkeypatch, secrets)
    assert isinstance(adapter, AccountHoldingsSource)


# --- (a) fixture-driven balances + positions -----------------------------------


def test_fetch_returns_spot_balances_and_futures_positions(
    monkeypatch: pytest.MonkeyPatch, secrets: SecretsStore
) -> None:
    adapter, _ = _adapter(monkeypatch, secrets)
    holdings = adapter.fetch_account_holdings()

    assert holdings.venue == "binance"
    # The leg's freshness stamp is the injected query instant, UTC.
    assert holdings.as_of == datetime.fromtimestamp(_CLOCK_EPOCH, tz=UTC)

    # Spot: quantities parsed from decimal strings, the zero ETH line dropped,
    # upstream order preserved.
    assert [(b.asset, b.free, b.locked) for b in holdings.spot] == [
        ("BTC", 0.5, 0.0),
        ("USDT", 1000.0, 50.0),
    ]

    # Futures: signed quantities + entry prices, the flat SOLUSDT row dropped,
    # upstream order preserved — and the leg is a distinct type from spot.
    assert [(p.symbol, p.quantity, p.entry_price) for p in holdings.futures] == [
        ("BTCUSDT", 0.010, 60000.0),
        ("ETHUSDT", -0.500, 3000.0),
    ]
    assert holdings.futures[0].mark_price == 61000.0
    assert holdings.futures[0].unrealized_pnl_usd == 10.0
    assert holdings.futures[1].quantity < 0  # short carries its sign


def test_signed_request_carries_key_header_and_wire_exact_signature(
    monkeypatch: pytest.MonkeyPatch, secrets: SecretsStore
) -> None:
    adapter, fake = _adapter(monkeypatch, secrets)
    adapter.fetch_account_holdings()

    assert len(fake.requests) == 2  # spot + futures, nothing else
    for url, headers in fake.requests:
        assert headers.get("X-MBX-APIKEY") == _KEY
        query = urllib.parse.urlsplit(url).query
        unsigned, sep, signature = query.partition("&signature=")
        assert sep, "signed request must carry a trailing signature parameter"
        # The signature is HMAC-SHA256 of exactly the query bytes that went out.
        expected = hmac.new(
            _SECRET.encode("utf-8"), unsigned.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        assert signature == expected
        params = dict(urllib.parse.parse_qsl(unsigned))
        assert params["timestamp"] == str(int(_CLOCK_EPOCH * 1000))


# --- (b) credential never logged / echoed ---------------------------------------


def test_credential_never_logged_echoed_or_placed_in_a_url(
    monkeypatch: pytest.MonkeyPatch,
    secrets: SecretsStore,
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter, fake = _adapter(monkeypatch, secrets)
    with caplog.at_level(logging.DEBUG):
        adapter.fetch_account_holdings()

    assert _SECRET not in caplog.text
    assert _KEY not in caplog.text
    assert _SECRET not in repr(adapter)
    assert _KEY not in repr(adapter)
    for url, _headers in fake.requests:
        # The key travels only in the header; the secret only as HMAC material.
        assert _SECRET not in url
        assert _KEY not in url


def test_auth_failure_message_names_the_leg_but_never_a_credential(
    monkeypatch: pytest.MonkeyPatch, secrets: SecretsStore
) -> None:
    body = json.dumps({"code": -2015, "msg": "Invalid API-key, IP, or permissions."})
    adapter, _ = _adapter(
        monkeypatch, secrets, transport=_StaticTransport(401, body.encode("utf-8"))
    )
    with pytest.raises(BinanceAccountAuthError) as excinfo:
        adapter.fetch_account_holdings()
    message = str(excinfo.value)
    assert "spot" in message
    assert _KEY not in message
    assert _SECRET not in message


# --- (c) typed auth errors -------------------------------------------------------


def test_missing_credential_raises_typed_auth_error_without_a_network_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    empty_store = SecretsStore(tmp_path / "secrets.json", environ={})
    adapter, fake = _adapter(monkeypatch, empty_store)
    with pytest.raises(BinanceAccountAuthError):
        adapter.fetch_account_holdings()
    assert fake.requests == []


def test_key_without_secret_raises_typed_auth_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = SecretsStore(tmp_path / "secrets.json", environ={})
    store.set("binance_read_api_key", _KEY)
    adapter, fake = _adapter(monkeypatch, store)
    with pytest.raises(BinanceAccountAuthError):
        adapter.fetch_account_holdings()
    assert fake.requests == []


def test_binance_auth_body_code_on_http_400_is_an_auth_error(
    monkeypatch: pytest.MonkeyPatch, secrets: SecretsStore
) -> None:
    # A wrong secret produces HTTP 400 with body code -1022, not a 401.
    body = json.dumps({"code": -1022, "msg": "Signature for this request is not valid."})
    adapter, _ = _adapter(
        monkeypatch, secrets, transport=_StaticTransport(400, body.encode("utf-8"))
    )
    with pytest.raises(BinanceAccountAuthError):
        adapter.fetch_account_holdings()


# --- typed geo / throttle errors -------------------------------------------------


def test_451_raises_geo_restricted_after_exactly_one_attempt(
    monkeypatch: pytest.MonkeyPatch, secrets: SecretsStore
) -> None:
    transport = _StaticTransport(451, b"")
    # Retries are available; the 451 pin must refuse to use them (ADR-0052).
    adapter, _ = _adapter(monkeypatch, secrets, transport=transport, max_retries=3)
    with pytest.raises(GeoRestrictedError):
        adapter.fetch_account_holdings()
    assert transport.attempts == 1


def test_429_raises_rate_limited(monkeypatch: pytest.MonkeyPatch, secrets: SecretsStore) -> None:
    adapter, _ = _adapter(monkeypatch, secrets, transport=_StaticTransport(429, b""))
    with pytest.raises(RateLimitedError):
        adapter.fetch_account_holdings()


# --- boundary validation ----------------------------------------------------------


def test_non_numeric_balance_raises_typed_shape_error(
    monkeypatch: pytest.MonkeyPatch, secrets: SecretsStore
) -> None:
    spot = _spot_payload()
    spot["balances"][0]["free"] = "not-a-number"
    adapter, _ = _adapter(monkeypatch, secrets, transport=_FakeAccountTransport(spot=spot))
    with pytest.raises(BinanceAccountError, match="free"):
        adapter.fetch_account_holdings()


def test_negative_balance_is_rejected_at_the_model_boundary(
    monkeypatch: pytest.MonkeyPatch, secrets: SecretsStore
) -> None:
    spot = _spot_payload()
    spot["balances"][0]["free"] = "-0.5"
    adapter, _ = _adapter(monkeypatch, secrets, transport=_FakeAccountTransport(spot=spot))
    with pytest.raises(pydantic.ValidationError):
        adapter.fetch_account_holdings()


def test_non_list_positions_payload_raises_typed_shape_error(
    monkeypatch: pytest.MonkeyPatch, secrets: SecretsStore
) -> None:
    adapter, _ = _adapter(
        monkeypatch,
        secrets,
        transport=_FakeAccountTransport(futures={"not": "a list"}),
    )
    with pytest.raises(BinanceAccountError, match="futures"):
        adapter.fetch_account_holdings()


# --- (d) read-only: no write path exists in the module ----------------------------


def test_read_only_no_write_verb_and_only_the_two_read_endpoints() -> None:
    """Source-level pin: the adapter calls no HTTP write verb anywhere, and
    every Binance URL literal in the module is one of the two account-read
    endpoints — a future 'just place the order here' accretion fails this
    test before it ships (Plan 0041 done-when; ADR-0025 boundary)."""
    module_file = binance_account.__file__
    assert module_file is not None
    tree = ast.parse(Path(module_file).read_text(encoding="utf-8"))

    write_verbs = {"post", "put", "delete", "patch", "request"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in write_verbs, (
                f"binance_account.py calls HTTP write verb {node.func.attr!r}"
            )

    allowed_urls = {_SPOT_ACCOUNT_URL, _FUTURES_POSITIONS_URL}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("http")
        ):
            assert node.value in allowed_urls, (
                f"binance_account.py reaches an unexpected endpoint {node.value!r}"
            )
