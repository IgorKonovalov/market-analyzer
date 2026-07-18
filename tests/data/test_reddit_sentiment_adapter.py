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

import base64
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data.adapters import reddit_sentiment
from market_analyser.data.adapters.reddit_sentiment import RedditSentimentAdapter, reddit_label
from market_analyser.data.default_provider import DefaultMarketDataProvider
from market_analyser.persistence.secrets import SecretsStore

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


# -- keyed OAuth path (Plan 0111 / ADR-0105) --------------------------------

_TOKEN_MARKER = "api/v1/access_token"
_OAUTH_HOST = "https://oauth.reddit.com/r/"
_EXPECTED_BASIC = base64.b64encode(b"cid:csecret").decode("ascii")


class _Dispatch:
    """A transport seam that dispatches on URL: token URL → the token queue, anything
    else → the search queue. Records every call (method, url, headers, body). A queued
    `BaseException` is raised; an `HttpResponse` is returned."""

    def __init__(self, *, tokens: list[Any], searches: list[Any]) -> None:
        self._tokens = list(tokens)
        self._searches = list(searches)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self, method: str, url: str, body: Any, headers: Any, *, proxy: Any
    ) -> HttpResponse:
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers or {}), "body": body}
        )
        queue = self._tokens if _TOKEN_MARKER in url else self._searches
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item  # type: ignore[no-any-return]

    def token_calls(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if _TOKEN_MARKER in c["url"]]

    def search_calls(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if _TOKEN_MARKER not in c["url"]]


def _token_ok(access_token: str = "tok-abc", expires_in: int = 3600) -> HttpResponse:
    body = json.dumps(
        {"access_token": access_token, "token_type": "bearer", "expires_in": expires_in}
    ).encode("utf-8")
    return HttpResponse(status_code=200, headers={}, body=body, elapsed_seconds=0.0)


def _search_ok(posts: list[dict[str, Any]]) -> HttpResponse:
    return HttpResponse(status_code=200, headers={}, body=_listing(posts), elapsed_seconds=0.0)


def _http_status(status: int, body: bytes = b'{"message":"err"}') -> HttpResponse:
    return HttpResponse(status_code=status, headers={}, body=body, elapsed_seconds=0.0)


def _store(
    tmp_path: Path, *, cid: str | None = "cid", csecret: str | None = "csecret"
) -> SecretsStore:
    env: dict[str, str] = {}
    if cid is not None:
        env["MARKET_ANALYSER_REDDIT_CLIENT_ID"] = cid
    if csecret is not None:
        env["MARKET_ANALYSER_REDDIT_CLIENT_SECRET"] = csecret
    return SecretsStore(tmp_path / "secrets.json", environ=env)


def _keyed_adapter(
    monkeypatch: pytest.MonkeyPatch,
    dispatch: _Dispatch,
    store: SecretsStore,
    *,
    search_cache_ttl: float = 0.0,
) -> RedditSentimentAdapter:
    """Wire both the search and token clients to one dispatching transport."""
    search = ResilientHttpClient(
        source_name="reddit-test", max_retries=0, cache_ttl_seconds=search_cache_ttl
    )
    token = ResilientHttpClient(
        source_name="reddit-token-test", max_retries=0, cache_ttl_seconds=0.0
    )
    monkeypatch.setattr(search, "_perform_request", dispatch)
    monkeypatch.setattr(token, "_perform_request", dispatch)
    return RedditSentimentAdapter(http_client=search, secrets_store=store, token_http_client=token)


def test_keyed_path_mints_token_then_bearer_searches_oauth_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _freeze(monkeypatch)
    posts = [
        _post("bullish breakout, buying calls", score=10),
        _post("long and strong, accumulate", score=10),
    ]
    dispatch = _Dispatch(tokens=[_token_ok("tok-abc")], searches=[_search_ok(posts)])
    adapter = _keyed_adapter(monkeypatch, dispatch, _store(tmp_path))

    sample = adapter.fetch_sentiment(symbol="BTC", window="24h")

    # Exactly one token POST then one bearer GET, in that order.
    token_calls = dispatch.token_calls()
    search_calls = dispatch.search_calls()
    assert len(token_calls) == 1
    assert len(search_calls) == 1
    # Token POST: HTTP Basic + the form body, to the token endpoint.
    assert token_calls[0]["method"] == "POST"
    assert token_calls[0]["headers"]["Authorization"] == f"Basic {_EXPECTED_BASIC}"
    assert token_calls[0]["body"] == b"grant_type=client_credentials"
    # Search GET: bearer header, oauth host, identical search params as the keyless path.
    assert search_calls[0]["method"] == "GET"
    assert search_calls[0]["headers"]["Authorization"] == "bearer tok-abc"
    assert search_calls[0]["url"].startswith(
        f"{_OAUTH_HOST}CryptoCurrency+Bitcoin+wallstreetbets+stocks+investing/search"
    )
    assert "q=BTC" in search_calls[0]["url"]
    assert "restrict_sr=1" in search_calls[0]["url"]
    # And it scored the returned listing.
    assert sample.source == "reddit"
    assert sample.score > 0
    assert sample.breakdown == {"positive": 2, "negative": 0, "neutral": 0}


def test_no_keys_uses_keyless_path_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _freeze(monkeypatch)
    dispatch = _Dispatch(tokens=[], searches=[_search_ok([_post("bullish moon", score=10)])])
    adapter = _keyed_adapter(monkeypatch, dispatch, _store(tmp_path, cid=None, csecret=None))

    sample = adapter.fetch_sentiment(symbol="BTC", window="24h")

    # No token minted; the single request is the keyless www host with search.json.
    assert dispatch.token_calls() == []
    assert len(dispatch.search_calls()) == 1
    assert dispatch.search_calls()[0]["url"].startswith(
        "https://www.reddit.com/r/CryptoCurrency+Bitcoin+wallstreetbets+stocks+investing/search.json"
    )
    assert "Authorization" not in dispatch.search_calls()[0]["headers"]
    assert sample.score == 1.0


def test_single_key_present_still_keyless(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _freeze(monkeypatch)
    dispatch = _Dispatch(tokens=[], searches=[_search_ok([_post("bullish moon", score=10)])])
    # Only the id is set; the gate requires BOTH keys, so this stays keyless.
    adapter = _keyed_adapter(monkeypatch, dispatch, _store(tmp_path, csecret=None))

    adapter.fetch_sentiment(symbol="BTC", window="24h")

    assert dispatch.token_calls() == []
    assert dispatch.search_calls()[0]["url"].startswith("https://www.reddit.com/")


def test_expired_token_triggers_reauth(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clock = {"now": _FROZEN_NOW}
    monkeypatch.setattr(reddit_sentiment, "_now", lambda: clock["now"])
    posts = [_post("bullish moon", score=10)]
    dispatch = _Dispatch(
        tokens=[_token_ok("tok-1", expires_in=3600), _token_ok("tok-2", expires_in=3600)],
        searches=[_search_ok(posts), _search_ok(posts)],
    )
    adapter = _keyed_adapter(monkeypatch, dispatch, _store(tmp_path))

    adapter.fetch_sentiment(symbol="BTC", window="24h")  # mints tok-1 (expires now+3540)
    # Advance past the margin-adjusted expiry so the cached bearer is stale.
    clock["now"] = _FROZEN_NOW + timedelta(seconds=3600)
    adapter.fetch_sentiment(symbol="BTC", window="24h")  # must re-auth

    assert len(dispatch.token_calls()) == 2
    # The second search rode the refreshed bearer.
    assert dispatch.search_calls()[1]["headers"]["Authorization"] == "bearer tok-2"


def test_cached_token_reused_within_expiry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _freeze(monkeypatch)
    posts = [_post("bullish moon", score=10)]
    dispatch = _Dispatch(
        tokens=[_token_ok("tok-1", expires_in=3600)],
        searches=[_search_ok(posts), _search_ok(posts)],
    )
    adapter = _keyed_adapter(monkeypatch, dispatch, _store(tmp_path))

    adapter.fetch_sentiment(symbol="BTC", window="24h")
    adapter.fetch_sentiment(symbol="BTC", window="24h")  # same frozen time → token still valid

    assert len(dispatch.token_calls()) == 1  # minted once, reused
    assert len(dispatch.search_calls()) == 2


def test_search_401_refreshes_token_once_and_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _freeze(monkeypatch)
    posts = [_post("bullish moon", score=10)]
    dispatch = _Dispatch(
        tokens=[_token_ok("tok-1"), _token_ok("tok-2")],
        searches=[_http_status(401), _search_ok(posts)],
    )
    adapter = _keyed_adapter(monkeypatch, dispatch, _store(tmp_path))

    sample = adapter.fetch_sentiment(symbol="BTC", window="24h")

    assert len(dispatch.token_calls()) == 2  # exactly one refresh
    assert len(dispatch.search_calls()) == 2  # original + one retry
    assert dispatch.search_calls()[1]["headers"]["Authorization"] == "bearer tok-2"
    assert sample.score == 1.0


def test_persistent_401_after_one_refresh_degrades_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _freeze(monkeypatch)
    dispatch = _Dispatch(
        tokens=[_token_ok("tok-1"), _token_ok("tok-2")],
        searches=[_http_status(401), _http_status(401)],
    )
    adapter = _keyed_adapter(monkeypatch, dispatch, _store(tmp_path))

    _assert_neutral_empty(adapter.fetch_sentiment(symbol="BTC", window="24h"))
    # Refresh happens once; the retry's 401 is not chased further.
    assert len(dispatch.token_calls()) == 2
    assert len(dispatch.search_calls()) == 2


def test_token_failure_degrades_to_honest_empty_without_searching(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _freeze(monkeypatch)
    dispatch = _Dispatch(tokens=[_http_status(500)], searches=[])
    adapter = _keyed_adapter(monkeypatch, dispatch, _store(tmp_path))

    _assert_neutral_empty(adapter.fetch_sentiment(symbol="BTC", window="24h"))
    assert len(dispatch.token_calls()) == 1
    assert dispatch.search_calls() == []  # never reached the search


def test_secrets_never_enter_cache_keys_or_logs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    _freeze(monkeypatch)
    # A search failure exercises the failure-log path; a caching search client lets us
    # inspect the cache keys. The bearer and the basic credential must appear in neither.
    dispatch = _Dispatch(tokens=[_token_ok("tok-secret-abc")], searches=[_http_status(500)])
    adapter = _keyed_adapter(monkeypatch, dispatch, _store(tmp_path), search_cache_ttl=300.0)

    with caplog.at_level(logging.WARNING):
        adapter.fetch_sentiment(symbol="BTC", window="24h")

    # No cache entry keyed on the bearer or the basic credential.
    for key in adapter._http._cache:  # asserting an internal invariant
        assert "tok-secret-abc" not in key
        assert _EXPECTED_BASIC not in key
    # The path-only failure log carries neither the bearer nor the credential.
    assert "tok-secret-abc" not in caplog.text
    assert _EXPECTED_BASIC not in caplog.text


def test_provider_injects_secrets_store_into_reddit_adapter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _freeze(monkeypatch)
    posts = [_post("bullish moon", score=10)]
    dispatch = _Dispatch(tokens=[_token_ok("tok-abc")], searches=[_search_ok(posts)])
    adapter = _keyed_adapter(monkeypatch, dispatch, _store(tmp_path))
    # The provider hands the store to the adapter it default-constructs; here we pass the
    # pre-wired adapter, but the routing + keyed dispatch is what this pins end-to-end.
    provider = DefaultMarketDataProvider(reddit=adapter)

    sample = provider.get_sentiment(symbol="BTC", window="24h", source="reddit")

    assert sample.source == "reddit"
    assert len(dispatch.token_calls()) == 1
    assert dispatch.search_calls()[0]["headers"]["Authorization"] == "bearer tok-abc"
