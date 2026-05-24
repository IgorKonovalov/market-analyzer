"""Plan 0011 — offline tests for the crypto Fear & Greed adapter.

The committed capture (`alternative_me_fng_response.json`) and inline variants
drive `CryptoFearGreedAdapter` through a `ResilientHttpClient` whose transport
seam (`_perform_request`) is monkeypatched, so the suite never touches the
network. The single live call is isolated in `@pytest.mark.network` (deselected
in CI, runnable with `uv run pytest -m network`).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data.adapters.crypto_fear_greed import (
    CryptoFearGreedAdapter,
    CryptoFearGreedError,
)
from market_analyser.data.default_provider import DefaultMarketDataProvider

_FIXTURES = Path(__file__).parent / "fixtures"
_FIXTURE_BYTES = (_FIXTURES / "alternative_me_fng_response.json").read_bytes()

# Upstream timestamp in the captured fixture; the adapter converts it to UTC.
_FIXTURE_TS = 1715212800

_CANONICAL_LABELS = ("Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed")


def _body(
    *, value: str = "55", classification: str = "Greed", timestamp: str = "1715212800"
) -> bytes:
    return json.dumps(
        {
            "name": "Fear and Greed Index",
            "data": [
                {
                    "value": value,
                    "value_classification": classification,
                    "timestamp": timestamp,
                    "time_until_update": "60000",
                },
            ],
            "metadata": {"error": None},
        },
    ).encode("utf-8")


def _adapter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    body: bytes = _FIXTURE_BYTES,
    cache_ttl_seconds: float = 0.0,
) -> tuple[CryptoFearGreedAdapter, ResilientHttpClient]:
    """Return an adapter wired to fixed `body` bytes plus the client (for stats)."""
    client = ResilientHttpClient(
        source_name="fng-test",
        cache_ttl_seconds=cache_ttl_seconds,
        max_retries=0,
    )

    def fake(method: str, url: str, body_: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        return HttpResponse(status_code=200, headers={}, body=body, elapsed_seconds=0.0)

    monkeypatch.setattr(client, "_perform_request", fake)
    return CryptoFearGreedAdapter(http_client=client), client


def test_fetch_current_parses_every_field(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch)

    sample = adapter.fetch_current()

    assert sample.value == 55
    assert isinstance(sample.value, int)
    assert sample.classification == "Greed"
    assert sample.published_at == datetime.fromtimestamp(_FIXTURE_TS, tz=UTC)
    assert sample.source == "alternative.me-fng"
    assert sample.market == "crypto"
    assert sample.window == "current"


def test_value_out_of_range_raises_at_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    # 105 is out of [0, 100]: must raise at parse time, never silently truncate.
    adapter, _ = _adapter(monkeypatch, body=_body(value="105"))

    with pytest.raises(ValidationError):
        adapter.fetch_current()


@pytest.mark.parametrize("label", _CANONICAL_LABELS)
def test_canonical_labels_all_parse(monkeypatch: pytest.MonkeyPatch, label: str) -> None:
    adapter, _ = _adapter(monkeypatch, body=_body(classification=label))

    sample = adapter.fetch_current()

    assert sample.classification == label


def test_unknown_label_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch, body=_body(classification="Confusion"))

    with pytest.raises(ValidationError):
        adapter.fetch_current()


def test_missing_data_raises_adapter_error(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch, body=json.dumps({"data": []}).encode("utf-8"))

    with pytest.raises(CryptoFearGreedError):
        adapter.fetch_current()


def test_second_call_within_ttl_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, client = _adapter(monkeypatch, cache_ttl_seconds=300.0)

    first = adapter.fetch_current()
    second = adapter.fetch_current()

    assert first == second
    stats = client.stats()
    assert stats.requests == 1
    assert stats.cache_hits == 1


def test_provider_get_market_sentiment_matches_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch)
    provider = DefaultMarketDataProvider(crypto_fng=adapter)

    via_provider = provider.get_market_sentiment(market="crypto")
    via_adapter = adapter.fetch_current()

    assert via_provider.model_dump() == via_adapter.model_dump()


def test_provider_rejects_equity_market(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch)
    provider = DefaultMarketDataProvider(crypto_fng=adapter)

    with pytest.raises(NotImplementedError, match="equity"):
        provider.get_market_sentiment(market="equity")  # type: ignore[arg-type]


def test_provider_rejects_as_of(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch)
    provider = DefaultMarketDataProvider(crypto_fng=adapter)

    with pytest.raises(ValueError, match="as_of"):
        provider.get_market_sentiment(market="crypto", as_of=datetime(2026, 1, 1, tzinfo=UTC))


@pytest.mark.network
def test_live_fetch_returns_valid_reading() -> None:
    sample = CryptoFearGreedAdapter().fetch_current()

    assert 0 <= sample.value <= 100
    assert sample.classification in _CANONICAL_LABELS
    # Rough directional sanity with a 5-point fuzz tolerance: only the extreme
    # ends are pinned, so day-to-day band drift never makes this flaky.
    if sample.value <= 20:
        assert sample.classification == "Extreme Fear"
    if sample.value >= 80:
        assert sample.classification == "Extreme Greed"
