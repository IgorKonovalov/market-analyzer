"""Plan 0103 phase 1 — offline tests for the Reddit crowd-sentiment adapter.

A committed listing capture (`reddit_BTC_response.json`) plus inline payloads drive
`RedditSentimentAdapter` through a `ResilientHttpClient` whose transport seam
(`_perform_request`) is monkeypatched, so the suite never touches the network. `_now` is
frozen so the fixture's `created_utc` timestamps stay inside the window deterministically.
The single live call is isolated behind `@pytest.mark.network`.

Pins the phase-1 done-when: (a) score sign for a bullish vs a bearish post set,
(b) upvote weighting, (c) honest-empty degrade on rate-limit / failure / non-JSON body,
(d) the `reddit_label` threshold ladder — plus a provider-level routing check that
`get_sentiment(source="reddit")` reaches the adapter.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data.adapters import reddit_sentiment
from market_analyser.data.adapters.reddit_sentiment import RedditSentimentAdapter, reddit_label
from market_analyser.data.default_provider import DefaultMarketDataProvider

_FIXTURES = Path(__file__).parent / "fixtures"
_BTC_BYTES = (_FIXTURES / "reddit_BTC_response.json").read_bytes()

# The fixture's newest post is at 1780000000.0; freeze "now" 30 min later so all four
# posts sit inside a 24h window regardless of the calendar.
_FROZEN_EPOCH = 1780000000.0
_FROZEN_NOW = datetime.fromtimestamp(_FROZEN_EPOCH + 1800, tz=UTC)

# Hand-computed over the committed fixture: two bullish posts (w=500, 300), one bearish
# (w=50), one neutral (w=20). Upvote-weighted score = (500 + 300 - 50) / 870.
_BTC_BREAKDOWN = {"positive": 2, "negative": 1, "neutral": 1}
_BTC_SCORE = (500 + 300 - 50) / (500 + 300 + 50 + 20)


def _freeze(monkeypatch: pytest.MonkeyPatch, now: datetime = _FROZEN_NOW) -> None:
    monkeypatch.setattr(reddit_sentiment, "_now", lambda: now)


def _adapter(
    monkeypatch: pytest.MonkeyPatch,
    *,
    body: bytes = _BTC_BYTES,
    status: int = 200,
    max_retries: int = 0,
) -> tuple[RedditSentimentAdapter, list[str]]:
    """Wire an adapter to a fixed transport response; return it and the requested URLs."""
    client = ResilientHttpClient(source_name="reddit-test", max_retries=max_retries)
    urls: list[str] = []

    def fake(method: str, url: str, body_: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        urls.append(url)
        return HttpResponse(status_code=status, headers={}, body=body, elapsed_seconds=0.0)

    monkeypatch.setattr(client, "_perform_request", fake)
    return RedditSentimentAdapter(http_client=client), urls


def _listing(posts: list[dict[str, Any]]) -> bytes:
    children = [{"kind": "t3", "data": post} for post in posts]
    return json.dumps({"kind": "Listing", "data": {"children": children}}).encode("utf-8")


def _post(
    title: str,
    *,
    score: int,
    created_utc: float = _FROZEN_EPOCH,
    selftext: str = "",
) -> dict[str, Any]:
    return {
        "title": title,
        "selftext": selftext,
        "score": score,
        "ups": score,
        "created_utc": created_utc,
        "subreddit": "CryptoCurrency",
        "permalink": "/r/CryptoCurrency/comments/x/",
    }


# -- fixture happy path -----------------------------------------------------


def test_btc_fixture_scores_and_labels(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    adapter, urls = _adapter(monkeypatch, body=_BTC_BYTES)

    sample = adapter.fetch_sentiment(symbol="BTC", window="24h")

    # One combined request to the fixed multi-subreddit group (the routing decision),
    # the symbol carried as the search query.
    assert len(urls) == 1
    assert urls[0].startswith(
        "https://www.reddit.com/r/CryptoCurrency+Bitcoin+wallstreetbets+stocks+investing/search.json"
    )
    assert "q=BTC" in urls[0]
    assert "restrict_sr=1" in urls[0]
    assert sample.symbol == "BTC"
    assert sample.source == "reddit"
    assert sample.window == "24h"
    assert sample.breakdown == _BTC_BREAKDOWN
    assert sample.score == pytest.approx(_BTC_SCORE)
    assert sample.as_of == _FROZEN_NOW
    # Derived at the tool layer in phase 2, but pinned here via the shared helper.
    assert reddit_label(sample.score) == "Strongly Bullish"


# -- (a) score sign ---------------------------------------------------------


def test_bullish_post_set_scores_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    body = _listing(
        [
            _post("buying calls, bullish moon breakout", score=10),
            _post("long and strong, accumulate the rally", score=10),
        ]
    )
    adapter, _ = _adapter(monkeypatch, body=body)

    sample = adapter.fetch_sentiment(symbol="BTC", window="24h")

    assert sample.score > 0
    assert sample.breakdown == {"positive": 2, "negative": 0, "neutral": 0}


def test_bearish_post_set_scores_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    body = _listing(
        [
            _post("dump incoming, bearish crash", score=10),
            _post("shorting with puts, breakdown and bleed", score=10),
        ]
    )
    adapter, _ = _adapter(monkeypatch, body=body)

    sample = adapter.fetch_sentiment(symbol="BTC", window="24h")

    assert sample.score < 0
    assert sample.breakdown == {"positive": 0, "negative": 2, "neutral": 0}


# -- (b) upvote weighting ---------------------------------------------------


def test_high_upvote_post_dominates_the_aggregate(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    # One bullish post with 1000 upvotes vs one bearish post with 1 upvote: the crowd
    # weight tips the aggregate strongly bullish even though the post counts are equal.
    body = _listing(
        [
            _post("bullish breakout, buying calls", score=1000),
            _post("bearish dump, short puts", score=1),
        ]
    )
    adapter, _ = _adapter(monkeypatch, body=body)

    sample = adapter.fetch_sentiment(symbol="BTC", window="24h")

    assert sample.score == pytest.approx((1000 - 1) / 1001)
    assert sample.score > 0.5
    assert sample.breakdown == {"positive": 1, "negative": 1, "neutral": 0}


def test_weighting_flips_when_the_bear_has_the_upvotes(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    # Same two posts, upvotes swapped: now the aggregate is strongly bearish. This is the
    # counterfactual that proves the sign is driven by upvotes, not post order/count.
    body = _listing(
        [
            _post("bullish breakout, buying calls", score=1),
            _post("bearish dump, short puts", score=1000),
        ]
    )
    adapter, _ = _adapter(monkeypatch, body=body)

    sample = adapter.fetch_sentiment(symbol="BTC", window="24h")

    assert sample.score == pytest.approx((1 - 1000) / 1001)
    assert sample.score < -0.5


# -- window filtering -------------------------------------------------------


def test_window_excludes_posts_older_than_the_window(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    body = _listing(
        [
            _post("bullish moon", score=10, created_utc=_FROZEN_EPOCH),  # ~30 min old
            _post("bearish crash", score=10, created_utc=_FROZEN_EPOCH - 7200),  # ~2.5h old
        ]
    )
    adapter, _ = _adapter(monkeypatch, body=body)

    sample = adapter.fetch_sentiment(symbol="BTC", window="1h")

    # Only the recent bullish post is inside a 1h window; the older bear is dropped.
    assert sample.breakdown == {"positive": 1, "negative": 0, "neutral": 0}
    assert sample.score == 1.0


# -- neutral / empty --------------------------------------------------------


def test_no_lexicon_hits_is_neutral_not_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    body = _listing([_post("chopping sideways, waiting for a clear signal", score=10)])
    adapter, _ = _adapter(monkeypatch, body=body)

    sample = adapter.fetch_sentiment(symbol="BTC", window="24h")

    assert sample.score == 0.0
    assert sample.breakdown == {"positive": 0, "negative": 0, "neutral": 1}


def test_zero_posts_is_neutral_not_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    adapter, _ = _adapter(monkeypatch, body=_listing([]))

    sample = adapter.fetch_sentiment(symbol="BTC", window="24h")

    assert sample.score == 0.0
    assert sample.breakdown == {"positive": 0, "negative": 0, "neutral": 0}


# -- (c) honest-empty degrade -----------------------------------------------


def _assert_neutral_empty(sample: Any) -> None:
    assert sample.symbol == "BTC"
    assert sample.source == "reddit"
    assert sample.score == 0.0
    assert sample.breakdown == {"positive": 0, "negative": 0, "neutral": 0}


def test_rate_limit_degrades_to_honest_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    # HTTP 429 → RATELIMIT; with no retries left the client raises, and the adapter
    # degrades to a neutral empty rather than surfacing the exception (ADR-0019).
    adapter, _ = _adapter(monkeypatch, body=b'{"message":"rate limited"}', status=429)

    _assert_neutral_empty(adapter.fetch_sentiment(symbol="BTC", window="24h"))


def test_transport_failure_degrades_to_honest_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    adapter, _ = _adapter(monkeypatch, body=b"boom", status=500)

    _assert_neutral_empty(adapter.fetch_sentiment(symbol="BTC", window="24h"))


def test_non_json_body_degrades_to_honest_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    # Reddit sometimes serves an HTML block page with a 200; expect_json treats it as a
    # transient hiccup, which exhausts to ResilientHttpError → honest empty.
    adapter, _ = _adapter(monkeypatch, body=b"<html>blocked</html>", status=200)

    _assert_neutral_empty(adapter.fetch_sentiment(symbol="BTC", window="24h"))


# -- (d) label ladder -------------------------------------------------------


def test_reddit_label_threshold_boundaries() -> None:
    assert reddit_label(1.0) == "Strongly Bullish"
    assert reddit_label(0.5) == "Strongly Bullish"
    assert reddit_label(0.49) == "Bullish"
    assert reddit_label(0.15) == "Bullish"
    assert reddit_label(0.14) == "Neutral"
    assert reddit_label(0.0) == "Neutral"
    assert reddit_label(-0.14) == "Neutral"
    assert reddit_label(-0.15) == "Bearish"
    assert reddit_label(-0.49) == "Bearish"
    assert reddit_label(-0.5) == "Strongly Bearish"
    assert reddit_label(-1.0) == "Strongly Bearish"


# -- window validation ------------------------------------------------------


def test_unsupported_window_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter, _ = _adapter(monkeypatch, body=_BTC_BYTES)

    with pytest.raises(ValueError, match="unsupported window"):
        adapter.fetch_sentiment(symbol="BTC", window="2h")


# -- provider routing -------------------------------------------------------


def test_provider_routes_reddit_source_to_the_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    adapter, _ = _adapter(monkeypatch, body=_BTC_BYTES)
    provider = DefaultMarketDataProvider(reddit=adapter)

    sample = provider.get_sentiment(symbol="BTC", window="24h", source="reddit")

    assert sample.source == "reddit"
    assert sample.breakdown == _BTC_BREAKDOWN
    assert sample.score == pytest.approx(_BTC_SCORE)


# -- live smoke -------------------------------------------------------------


@pytest.mark.network
def test_live_fetch_returns_valid_reading() -> None:
    sample = RedditSentimentAdapter().fetch_sentiment(symbol="BTC", window="24h")

    assert -1.0 <= sample.score <= 1.0
    assert sample.source == "reddit"
    assert sum(sample.breakdown.values()) >= 0
