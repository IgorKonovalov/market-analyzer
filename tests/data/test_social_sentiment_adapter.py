"""Plan 0108 phase 1 — offline tests for the X/social crowd-sentiment adapter.

A committed LunarCrush topic capture (`lunarcrush_BTC_topic.json`) plus inline payloads
drive `SocialSentimentAdapter` through a `ResilientHttpClient` whose transport seam
(`_perform_request`) is monkeypatched, so the suite never touches the network. The key
comes from a `SecretsStore` backed by an injected environ (pinning the
`MARKET_ANALYSER_LUNARCRUSH_API_KEY` override name); the single live call is isolated
behind `@pytest.mark.network` and self-skips without a key.

Pins the phase-1 done-when: (a) score sign/label for a bullish vs a bearish snapshot,
(b) **no-key → inert honest-empty** (no request issued, no exception), (c) resilient-path
failure / rate-limit / non-JSON → empty (no fabrication), (d) sample size surfaced via the
breakdown counts — plus secret hygiene (key in the Authorization header, never the URL)
and a provider-level routing check that `get_sentiment(source="x")` reaches the adapter.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data.adapters import social_sentiment
from market_analyser.data.adapters.social_sentiment import SocialSentimentAdapter, social_label
from market_analyser.data.default_provider import DefaultMarketDataProvider
from market_analyser.persistence.secrets import SecretsStore

_FIXTURES = Path(__file__).parent / "fixtures"
_BTC_BYTES = (_FIXTURES / "lunarcrush_BTC_topic.json").read_bytes()

_FROZEN_NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)

_KEY_ENV = "MARKET_ANALYSER_LUNARCRUSH_API_KEY"
_TEST_KEY = "test-lunarcrush-key"

# Hand-computed over the committed fixture: vendor aggregate sentiment=78 maps to
# (78 - 50) / 50; the per-network polarity interaction counts sum across the four
# networks (tweet + reddit-post + youtube-video + news).
_BTC_SCORE = (78 - 50) / 50
_BTC_BREAKDOWN = {
    "positive": 620_000 + 42_000 + 31_000 + 7_000,
    "neutral": 150_000 + 18_000 + 9_000 + 4_000,
    "negative": 80_000 + 11_000 + 6_000 + 2_000,
}


def _freeze(monkeypatch: pytest.MonkeyPatch, now: datetime = _FROZEN_NOW) -> None:
    monkeypatch.setattr(social_sentiment, "_now", lambda: now)


def _store(tmp_path: Path, *, key: str | None = _TEST_KEY) -> SecretsStore:
    """A SecretsStore over an injected environ — the env-override name is the pin."""
    environ = {_KEY_ENV: key} if key is not None else {}
    return SecretsStore(tmp_path / "secrets.json", environ=environ)


def _adapter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    body: bytes = _BTC_BYTES,
    status: int = 200,
    key: str | None = _TEST_KEY,
) -> tuple[SocialSentimentAdapter, list[str], list[dict[str, str]]]:
    """Wire an adapter to a fixed transport response; return it plus the requested
    URLs and the per-request headers (for the secret-hygiene assertions)."""
    client = ResilientHttpClient(source_name="lunarcrush-test", max_retries=0)
    urls: list[str] = []
    headers_seen: list[dict[str, str]] = []

    def fake(method: str, url: str, body_: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        urls.append(url)
        headers_seen.append(dict(headers or {}))
        return HttpResponse(status_code=status, headers={}, body=body, elapsed_seconds=0.0)

    monkeypatch.setattr(client, "_perform_request", fake)
    adapter = SocialSentimentAdapter(secrets_store=_store(tmp_path, key=key), http_client=client)
    return adapter, urls, headers_seen


def _payload(sentiment: Any, *, detail: Any = None) -> bytes:
    data: dict[str, Any] = {"topic": "btc", "sentiment": sentiment}
    if detail is not None:
        data["types_sentiment_detail"] = detail
    return json.dumps({"data": data}).encode("utf-8")


# -- fixture happy path + secret hygiene ------------------------------------


def test_btc_fixture_scores_and_labels(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _freeze(monkeypatch)
    adapter, urls, headers_seen = _adapter(monkeypatch, tmp_path)

    sample = adapter.fetch_sentiment(symbol="btc", window="24h")

    # One request to the topic endpoint, symbol lower-cased into the topic path.
    assert urls == ["https://lunarcrush.com/api4/public/topic/btc/v1"]
    # Secret hygiene (ADR-0038): the key rides the Authorization header, never the URL.
    assert headers_seen == [{"Authorization": f"Bearer {_TEST_KEY}"}]
    assert _TEST_KEY not in urls[0]
    assert sample.symbol == "BTC"
    assert sample.source == "x"
    assert sample.window == "24h"
    assert sample.as_of == _FROZEN_NOW
    assert sample.score == pytest.approx(_BTC_SCORE)
    assert sample.breakdown == _BTC_BREAKDOWN
    # (d) sample size is derivable from the counts (the tool layer sums them).
    assert sum(sample.breakdown.values()) == 980_000
    assert social_label(sample.score) == "Strongly Bullish"


# -- (a) score sign ---------------------------------------------------------


def test_bearish_snapshot_scores_negative(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _freeze(monkeypatch)
    detail = {"tweet": {"positive": 100, "neutral": 300, "negative": 900}}
    adapter, _, _ = _adapter(monkeypatch, tmp_path, body=_payload(30, detail=detail))

    sample = adapter.fetch_sentiment(symbol="BTC", window="24h")

    assert sample.score == pytest.approx((30 - 50) / 50)
    assert sample.score < 0
    assert sample.breakdown == {"positive": 100, "neutral": 300, "negative": 900}
    assert social_label(sample.score) == "Bearish"


def test_score_maps_the_vendor_scale_extremes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _freeze(monkeypatch)
    for sentiment, expected in ((100, 1.0), (0, -1.0), (50, 0.0)):
        adapter, _, _ = _adapter(monkeypatch, tmp_path, body=_payload(sentiment))
        assert adapter.fetch_sentiment(symbol="BTC", window="24h").score == pytest.approx(expected)


# -- (b) no key → inert honest-empty ----------------------------------------


def _assert_neutral_empty(sample: Any) -> None:
    assert sample.symbol == "BTC"
    assert sample.source == "x"
    assert sample.score == 0.0
    assert sample.breakdown == {"positive": 0, "negative": 0, "neutral": 0}


def test_no_key_is_inert_and_honest_empty(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _freeze(monkeypatch)
    adapter, urls, _ = _adapter(monkeypatch, tmp_path, key=None)

    sample = adapter.fetch_sentiment(symbol="BTC", window="24h")

    _assert_neutral_empty(sample)
    assert urls == []  # inert: no request was issued


def test_no_store_is_inert_and_honest_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    # The default-construction path DefaultMarketDataProvider uses in offline tests:
    # no store wired at all → inert, no network reachable.
    _freeze(monkeypatch)
    client = ResilientHttpClient(source_name="lunarcrush-test", max_retries=0)
    calls: list[str] = []

    def fake(method: str, url: str, body_: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        calls.append(url)
        return HttpResponse(status_code=200, headers={}, body=_BTC_BYTES, elapsed_seconds=0.0)

    monkeypatch.setattr(client, "_perform_request", fake)
    adapter = SocialSentimentAdapter(http_client=client)

    _assert_neutral_empty(adapter.fetch_sentiment(symbol="BTC", window="24h"))
    assert calls == []


def test_empty_env_value_reads_as_absent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _freeze(monkeypatch)
    adapter, urls, _ = _adapter(monkeypatch, tmp_path, key="")

    _assert_neutral_empty(adapter.fetch_sentiment(symbol="BTC", window="24h"))
    assert urls == []


# -- (c) resilient-path degrade ---------------------------------------------


def test_rate_limit_degrades_to_honest_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _freeze(monkeypatch)
    # HTTP 429 → RATELIMIT; with no retries left the client raises, and the adapter
    # degrades to a neutral empty rather than surfacing the exception (ADR-0019).
    adapter, _, _ = _adapter(monkeypatch, tmp_path, body=b'{"error":"rate limit"}', status=429)

    _assert_neutral_empty(adapter.fetch_sentiment(symbol="BTC", window="24h"))


def test_transport_failure_degrades_to_honest_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _freeze(monkeypatch)
    adapter, _, _ = _adapter(monkeypatch, tmp_path, body=b"boom", status=500)

    _assert_neutral_empty(adapter.fetch_sentiment(symbol="BTC", window="24h"))


def test_non_json_body_degrades_to_honest_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # expect_json treats a non-JSON 200 as a transient hiccup, which exhausts to
    # ResilientHttpError → honest empty.
    _freeze(monkeypatch)
    adapter, _, _ = _adapter(monkeypatch, tmp_path, body=b"<html>maintenance</html>", status=200)

    _assert_neutral_empty(adapter.fetch_sentiment(symbol="BTC", window="24h"))


@pytest.mark.parametrize(
    "body",
    [
        b"{}",  # no data object
        b'{"data": null}',
        b'{"data": []}',
        b'{"data": {"topic": "btc"}}',  # no sentiment field
        b'{"data": {"sentiment": "bullish"}}',  # non-numeric aggregate
        b'{"data": {"sentiment": true}}',  # bool is not a score
        b'{"data": {"sentiment": 250}}',  # outside the 0..100 vendor scale
        b'{"data": {"sentiment": -5}}',
    ],
)
def test_shape_broken_payload_degrades_to_honest_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, body: bytes
) -> None:
    _freeze(monkeypatch)
    adapter, _, _ = _adapter(monkeypatch, tmp_path, body=body)

    _assert_neutral_empty(adapter.fetch_sentiment(symbol="BTC", window="24h"))


def test_missing_detail_keeps_score_with_zero_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A usable aggregate without the per-network detail keeps the score and reports
    # zero counts (what could be read) — honest, not fabricated.
    _freeze(monkeypatch)
    adapter, _, _ = _adapter(monkeypatch, tmp_path, body=_payload(78))

    sample = adapter.fetch_sentiment(symbol="BTC", window="24h")

    assert sample.score == pytest.approx(_BTC_SCORE)
    assert sample.breakdown == {"positive": 0, "negative": 0, "neutral": 0}


def test_garbage_detail_entries_contribute_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _freeze(monkeypatch)
    detail = {
        "tweet": {"positive": 10, "neutral": 5, "negative": 2},
        "reddit-post": "broken",
        "news": {"positive": "many", "neutral": -3, "negative": True},
    }
    adapter, _, _ = _adapter(monkeypatch, tmp_path, body=_payload(60, detail=detail))

    sample = adapter.fetch_sentiment(symbol="BTC", window="24h")

    assert sample.breakdown == {"positive": 10, "neutral": 5, "negative": 2}


# -- window validation ------------------------------------------------------


def test_unsupported_window_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    adapter, _, _ = _adapter(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="unsupported window"):
        adapter.fetch_sentiment(symbol="BTC", window="2h")


# -- label ladder -----------------------------------------------------------


def test_social_label_threshold_boundaries() -> None:
    assert social_label(1.0) == "Strongly Bullish"
    assert social_label(0.5) == "Strongly Bullish"
    assert social_label(0.49) == "Bullish"
    assert social_label(0.15) == "Bullish"
    assert social_label(0.14) == "Neutral"
    assert social_label(0.0) == "Neutral"
    assert social_label(-0.14) == "Neutral"
    assert social_label(-0.15) == "Bearish"
    assert social_label(-0.49) == "Bearish"
    assert social_label(-0.5) == "Strongly Bearish"
    assert social_label(-1.0) == "Strongly Bearish"


# -- provider routing -------------------------------------------------------


def test_provider_routes_x_source_to_the_adapter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _freeze(monkeypatch)
    adapter, _, _ = _adapter(monkeypatch, tmp_path)
    provider = DefaultMarketDataProvider(social=adapter)

    sample = provider.get_sentiment(symbol="BTC", window="24h", source="x")

    assert sample.source == "x"
    assert sample.score == pytest.approx(_BTC_SCORE)
    assert sample.breakdown == _BTC_BREAKDOWN


def test_unwired_provider_x_source_is_honest_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    # A default-constructed provider (offline tests everywhere) carries the inert
    # unkeyed adapter: `source="x"` answers honest-empty without touching the network.
    _freeze(monkeypatch)
    provider = DefaultMarketDataProvider()

    _assert_neutral_empty(provider.get_sentiment(symbol="BTC", window="24h", source="x"))


# -- live smoke -------------------------------------------------------------


@pytest.mark.network
def test_live_fetch_returns_valid_reading(tmp_path: Path) -> None:
    # Keyed live call — self-skips unless the real environment carries the key
    # (the phase-3 human smoke is the authoritative live check; this is a dev aid).
    store = SecretsStore(tmp_path / "secrets.json")
    if not store.get("lunarcrush_api_key"):
        pytest.skip("no lunarcrush_api_key configured")

    sample = SocialSentimentAdapter(secrets_store=store).fetch_sentiment(symbol="BTC", window="24h")

    assert -1.0 <= sample.score <= 1.0
    assert sample.source == "x"
    assert sum(sample.breakdown.values()) >= 0
