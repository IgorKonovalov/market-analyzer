"""Plan 0022 — offline tests for the CoinGecko macro-context adapter.

The committed `/global` capture (`coingecko_global.json`) plus an inline
`/simple/price` body drive `CoinGeckoAdapter` through a `ResilientHttpClient`
whose transport seam (`_perform_request`) is monkeypatched and dispatches by URL,
so the suite never touches the network. The single live call is isolated in
`@pytest.mark.network` (deselected in CI, runnable with `uv run pytest -m network`).

These tests pin Plan 0022 phase 1's done-when: field-by-field parse, the
`regime` "condition not advice" vocabulary invariant, the determinism of the
classification across each label, `as_of` rejection, and the typed-error
translation inherited from `ResilientHttpClient`.
"""

from __future__ import annotations

import json
import typing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data.adapters.coingecko import (
    CoinGeckoAdapter,
    CoinGeckoError,
    classify_crypto_regime,
)
from market_analyser.data.default_provider import DefaultMarketDataProvider
from market_analyser.data.errors import RateLimitedError, UpstreamUnavailableError
from market_analyser.data.types import CryptoRegime, MacroContext

_FIXTURES = Path(__file__).parent / "fixtures"
_GLOBAL_BYTES = (_FIXTURES / "coingecko_global.json").read_bytes()

# Snapshot timestamp in the committed fixture; the adapter converts it to UTC.
_FIXTURE_UPDATED_AT = 1716544000

# Action/recommendation tokens the neutral regime vocabulary must never contain
# (ADR-0027's named guard set). Note "risk_off_structure" legitimately *describes*
# a risk-off condition — the forbidden set is advice verbs/grades, not the word
# "risk" — so the ADR pins these four exactly.
_ADVICE_TOKENS = ("buy", "sell", "favorable", "opportunity")


def _price_body(*, usd: float = 65789.47, usd_24h_change: float = 3.2) -> bytes:
    return json.dumps({"bitcoin": {"usd": usd, "usd_24h_change": usd_24h_change}}).encode("utf-8")


def _adapter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    global_body: bytes = _GLOBAL_BYTES,
    price_body: bytes | None = None,
    cache_ttl_seconds: float = 0.0,
) -> tuple[CoinGeckoAdapter, ResilientHttpClient]:
    """Adapter wired to fixed bodies, dispatched by URL. Returns the client too
    (for stats). `/global` → `global_body`; `/simple/price` → `price_body`."""
    body_for_price = price_body if price_body is not None else _price_body()
    client = ResilientHttpClient(
        source_name="coingecko-test",
        cache_ttl_seconds=cache_ttl_seconds,
        max_retries=0,
        backoff_initial_seconds=0.0,
    )

    def fake(method: str, url: str, body_: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        payload = body_for_price if "simple/price" in url else global_body
        return HttpResponse(status_code=200, headers={}, body=payload, elapsed_seconds=0.0)

    monkeypatch.setattr(client, "_perform_request", fake)
    return CoinGeckoAdapter(http_client=client), client


# --- Done-when 1: offline fixture parse, field-by-field --------------------


def test_fetch_macro_context_parses_every_field(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch)

    macro = adapter.fetch_macro_context()

    assert macro.market == "crypto"
    assert macro.btc_price == 65789.47
    assert macro.btc_change_24h == 3.2
    assert macro.btc_dominance_pct == 52.3
    assert macro.total_market_cap_usd == 2500000000000.0
    assert macro.total_market_cap_change_24h == 1.5
    assert macro.regime == "btc_led"  # BTC outperforming the market (+1.7pp)
    assert macro.as_of == datetime.fromtimestamp(_FIXTURE_UPDATED_AT, tz=UTC)
    assert macro.source == "coingecko"


def test_shape_broken_global_payload_raises_adapter_error(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch, global_body=json.dumps({"data": {}}).encode("utf-8"))

    with pytest.raises(CoinGeckoError):
        adapter.fetch_macro_context()


def test_shape_broken_price_payload_raises_adapter_error(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch, price_body=json.dumps({}).encode("utf-8"))

    with pytest.raises(CoinGeckoError):
        adapter.fetch_macro_context()


def test_out_of_range_dominance_raises_at_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    # Dominance of 150% is impossible; the model rejects it at the boundary
    # rather than silently passing it through.
    broken = json.loads(_GLOBAL_BYTES)
    broken["data"]["market_cap_percentage"]["btc"] = 150.0
    adapter, _ = _adapter(monkeypatch, global_body=json.dumps(broken).encode("utf-8"))

    with pytest.raises(ValueError):  # pydantic ValidationError (subclass)
        adapter.fetch_macro_context()


# --- Done-when 2: regime is a condition, not advice (type-level guard) -----


def test_regime_vocabulary_contains_no_action_token() -> None:
    vocabulary = typing.get_args(CryptoRegime)
    assert set(vocabulary) == {"btc_led", "alt_structure", "risk_off_structure", "neutral"}
    for label in vocabulary:
        for token in _ADVICE_TOKENS:
            assert token not in label, f"regime label {label!r} smuggles in advice token {token!r}"


# --- Done-when 3: regime is deterministic and locks the ADR-0027 mapping ---

# (btc_change_24h, total_market_cap_change_24h) -> expected label. One scenario
# per label, chosen to sit clearly inside each branch.
_REGIME_CASES: tuple[tuple[float, float, CryptoRegime], ...] = (
    (3.2, 1.5, "btc_led"),  # BTC outperforms the market by > 1pp
    (-1.0, 2.0, "alt_structure"),  # BTC underperforms a flat-to-rising market by > 1pp
    (-9.0, -8.0, "risk_off_structure"),  # broad contraction dominates regardless of BTC
    (1.2, 1.0, "neutral"),  # within the dominance-trend deadband, not contracting
)


@pytest.mark.parametrize(("btc_change", "total_change", "expected"), _REGIME_CASES)
def test_regime_classification_is_deterministic(
    btc_change: float, total_change: float, expected: CryptoRegime
) -> None:
    first = classify_crypto_regime(
        btc_change_24h=btc_change, total_market_cap_change_24h=total_change
    )
    second = classify_crypto_regime(
        btc_change_24h=btc_change, total_market_cap_change_24h=total_change
    )
    assert first == second == expected


def test_risk_off_takes_priority_over_dominance_trend() -> None:
    # Even with BTC strongly outperforming, a materially contracting market is
    # risk_off_structure first (ADR-0027 table priority).
    assert (
        classify_crypto_regime(btc_change_24h=2.0, total_market_cap_change_24h=-7.0)
        == "risk_off_structure"
    )


# --- Done-when 4: as_of rejection ------------------------------------------


def test_provider_rejects_as_of(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch)
    provider = DefaultMarketDataProvider(coingecko=adapter)

    with pytest.raises(ValueError, match="as_of"):
        provider.get_macro_context(as_of=datetime(2026, 1, 1, tzinfo=UTC))


def test_provider_get_macro_context_matches_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch)
    provider = DefaultMarketDataProvider(coingecko=adapter)

    via_provider = provider.get_macro_context(market="crypto")
    via_adapter = adapter.fetch_macro_context()

    assert via_provider.model_dump() == via_adapter.model_dump()


# --- Done-when 5: resilience inheritance -----------------------------------


def test_transient_failure_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transient (HTTP 500) attempt is retried per ResilientHttpClient, then
    succeeds — the macro read still parses and a retry is recorded."""
    client = ResilientHttpClient(
        source_name="coingecko-retry",
        max_retries=2,
        backoff_initial_seconds=0.0,
    )
    calls = {"n": 0}

    def fake(method: str, url: str, body_: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        calls["n"] += 1
        if calls["n"] == 1:  # first physical attempt fails transiently
            return HttpResponse(status_code=500, headers={}, body=b"oops", elapsed_seconds=0.0)
        payload = _price_body() if "simple/price" in url else _GLOBAL_BYTES
        return HttpResponse(status_code=200, headers={}, body=payload, elapsed_seconds=0.0)

    monkeypatch.setattr(client, "_perform_request", fake)
    adapter = CoinGeckoAdapter(http_client=client)

    macro = adapter.fetch_macro_context()

    assert macro.regime == "btc_led"
    assert client.stats().retries >= 1


def test_hard_failure_surfaces_typed_upstream_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """An exhausting 503 surfaces a typed `UpstreamUnavailableError`, not a raw
    `ResilientHttpError`."""
    client = ResilientHttpClient(
        source_name="coingecko-down",
        max_retries=1,
        backoff_initial_seconds=0.0,
    )

    def fake(method: str, url: str, body_: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        return HttpResponse(status_code=503, headers={}, body=b"down", elapsed_seconds=0.0)

    monkeypatch.setattr(client, "_perform_request", fake)
    adapter = CoinGeckoAdapter(http_client=client)

    with pytest.raises(UpstreamUnavailableError):
        adapter.fetch_macro_context()


def test_rate_limit_surfaces_typed_rate_limited_error(monkeypatch: pytest.MonkeyPatch) -> None:
    client = ResilientHttpClient(
        source_name="coingecko-429",
        max_retries=0,
        backoff_initial_seconds=0.0,
    )

    def fake(method: str, url: str, body_: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        return HttpResponse(
            status_code=429, headers={"Retry-After": "30"}, body=b"slow down", elapsed_seconds=0.0
        )

    monkeypatch.setattr(client, "_perform_request", fake)
    adapter = CoinGeckoAdapter(http_client=client)

    with pytest.raises(RateLimitedError) as excinfo:
        adapter.fetch_macro_context()
    assert excinfo.value.retry_after_seconds == 30


def test_second_call_within_ttl_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, client = _adapter(monkeypatch, cache_ttl_seconds=60.0)

    first = adapter.fetch_macro_context()
    second = adapter.fetch_macro_context()

    assert first == second
    stats = client.stats()
    # Two endpoints fetched once each on the first call; both served from cache
    # on the second call.
    assert stats.requests == 2
    assert stats.cache_hits == 2


@pytest.mark.network
def test_live_fetch_returns_valid_macro_context() -> None:
    macro: MacroContext = CoinGeckoAdapter().fetch_macro_context()

    assert macro.market == "crypto"
    assert macro.btc_price > 0
    assert 0 <= macro.btc_dominance_pct <= 100
    assert macro.total_market_cap_usd > 0
    assert macro.regime in typing.get_args(CryptoRegime)
    assert macro.source == "coingecko"
