"""Reddit crowd-sentiment adapter — Plan 0103 + Plan 0111 (ADR-0098, ADR-0105, ADR-0019, ADR-0007).

Reddit exposes a keyless search JSON endpoint. This adapter queries **one fixed
multi-subreddit crowd group** (`r/CryptoCurrency+Bitcoin+wallstreetbets+stocks+investing`)
for a symbol in a single request, scores each post with a transparent bullish/bearish
**keyword lexicon**, weights each post's polarity by its upvote score, and aggregates to a
signed -1..+1 reading. Unlike the authenticated Reddit API it needs no key, matching the
project's keyless-first posture (ADR-0069); the `category` routing an earlier draft carried
is dropped (Plan 0103 amendment) in favour of the single group — one request is the lightest
footprint under Reddit's aggressive public rate-limit, and Reddit's own search relevance
surfaces on-topic posts for a crypto *or* an equity ticker.

**Keyed OAuth path (Plan 0111 / ADR-0105).** Reddit's anti-bot wall 403-blocks all keyless
JSON from some networks, so when both `reddit_client_id` and `reddit_client_secret` secrets
are configured the adapter climbs over it via the official app-only OAuth surface: it mints a
`client_credentials` bearer (cached in-process with an expiry margin) and issues the *same*
search against `oauth.reddit.com` instead of `www.reddit.com`. Scoring, subreddit group,
window filter, and upvote weighting are byte-identical between the two paths — only the
transport changes. Absent either key the keyless path above runs untouched. A 401 refreshes
the bearer exactly once and retries; any token or search failure degrades to the same
honest-empty as the keyless path (secrets ride `headers=`, and the token is fetched through a
caching-disabled client so no bearer enters the shared response cache — ADR-0105 hygiene).

The signed score is upvote-weighted:

    sum(weight_i * polarity_i) / sum(weight_i)        in [-1, 1]

where `polarity_i ∈ {-1, 0, +1}` is the sign of (bullish - bearish) keyword hits over the
post's title + body and `weight_i = max(upvotes, 1)`. "no posts" reads as 0.0 (neutral, not
unknown), exactly like the StockTwits path (Plan 0012).

Honest-degrade (ADR-0019): Reddit rate-limits hard, so a failed fetch degrades to a
**neutral empty `SentimentSample`** (score 0.0, all-zero breakdown), never an exception and
never fabricated data. All failure modes converge on `ResilientHttpError`: a 429 is
classified as a rate-limit and, once retries exhaust, raised; and because we fetch with
`expect_json=True`, a non-JSON body served in place of the listing (Reddit's block page) is
retried as a transient hiccup and raised the same way — so catching that one type covers
both. The caller reads an empty result as *possibly* a rate-limit, not silence (Plan 0103
risk note).

Conforms to `SentimentSource.fetch_sentiment` (ADR-0031) and is package-internal per
ADR-0007: downstream reaches this through `MarketDataProvider.get_sentiment(source="reddit")`,
never by importing it. `reddit_label` is the pure score→label ladder, exported so the
`sentiment` tool derives the label without `data/` importing `api/` (Plan 0103 phase 2).
"""

from __future__ import annotations

import base64
import re
from datetime import UTC, datetime
from typing import Any

from market_analyser.data._http import HttpResponse, ResilientHttpClient, ResilientHttpError
from market_analyser.data._windows import window_delta
from market_analyser.data.sources import SentimentSource
from market_analyser.data.types import SentimentSample
from market_analyser.persistence.secrets import SecretsStore

_BASE_URL = "https://www.reddit.com/r/{group}/search.json"
_SOURCE = "reddit"

# Keyed app-only OAuth path (Plan 0111 / ADR-0105). Both are the official API surface: the
# token endpoint issues a client_credentials bearer, and oauth.reddit.com serves the same
# search JSON as the keyless host but behind auth (no `.json` suffix needed there).
_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_OAUTH_SEARCH_URL = "https://oauth.reddit.com/r/{group}/search"
# Re-auth this many seconds before the token's stated expiry, so a request never rides a
# bearer that lapses in flight.
_TOKEN_EXPIRY_MARGIN_SECONDS = 60.0

# The one fixed multi-subreddit group (Plan 0103 routing decision), maintained here like
# the RSS feed catalog. Spans the retail crypto and equity venues; queried by symbol with
# `restrict_sr` so the search stays inside these subs.
_SUBREDDIT_GROUP = "CryptoCurrency+Bitcoin+wallstreetbets+stocks+investing"

# Reddit expects a descriptive User-Agent; a generic one is throttled harder (ADR-0019).
_USER_AGENT = "market-analyser/1.0 (keyless crowd-sentiment research)"

# 5-minute TTL: crowd tone is wall-clock-current but doesn't move every second.
_DEFAULT_TTL_SECONDS = 300.0
# Reddit's public endpoint is rate-limit-sensitive; don't fan out aggressively.
_DEFAULT_MAX_CONCURRENCY = 2
# One page is plenty of crowd signal and keeps the request light on the rate-limit.
_FETCH_LIMIT = 100

# Best-effort map of our window vocabulary onto Reddit's coarser `t` time filter. The
# adapter *also* filters posts by `created_utc` against the exact window (below), so `t`
# is only a fetch-narrowing hint, not the source of the window boundary.
_WINDOW_TO_REDDIT_T = {"1h": "hour", "4h": "day", "24h": "day", "7d": "week"}

# Transparent keyword lexicon (ADR-0098 — deliberately not VADER/ML for v1). Lowercase,
# matched as whole word tokens so "up" never fires inside "support". Trading/crowd slang
# spanning crypto and equities.
_BULLISH_TERMS = frozenset(
    {
        "buy",
        "buying",
        "bought",
        "long",
        "longs",
        "calls",
        "bull",
        "bullish",
        "moon",
        "mooning",
        "pump",
        "pumping",
        "breakout",
        "rally",
        "rallying",
        "green",
        "hodl",
        "accumulate",
        "accumulating",
        "undervalued",
        "bounce",
        "rocket",
        "gains",
        "surge",
        "surging",
        "rip",
        "up",
        "uptrend",
        "support",
        "strong",
        "ath",
    }
)
_BEARISH_TERMS = frozenset(
    {
        "sell",
        "selling",
        "sold",
        "short",
        "shorts",
        "puts",
        "bear",
        "bearish",
        "dump",
        "dumping",
        "crash",
        "crashing",
        "breakdown",
        "dip",
        "red",
        "rug",
        "overvalued",
        "resistance",
        "weak",
        "capitulation",
        "bleed",
        "bleeding",
        "tank",
        "tanking",
        "drop",
        "dropping",
        "down",
        "downtrend",
        "loss",
        "losses",
        "fear",
        "rekt",
        "bagholder",
    }
)

_TOKEN_RE = re.compile(r"[a-z]+")


def reddit_label(score: float) -> str:
    """Map an upvote-weighted -1..+1 score to a five-bucket crowd-tone label.

    The pure ladder shared by the adapter's tests and the `sentiment` tool's `reddit`
    handler (which derives the label at the tool layer, keeping `SentimentSample` free of a
    presentation field). Symmetric cut-points at ±0.5 (strong) and ±0.15 (lean)."""
    if score >= 0.5:
        return "Strongly Bullish"
    if score >= 0.15:
        return "Bullish"
    if score > -0.15:
        return "Neutral"
    if score > -0.5:
        return "Bearish"
    return "Strongly Bearish"


class RedditSentimentAdapter(SentimentSource):
    """Fetches Reddit posts for a symbol across the fixed crowd group and scores them."""

    def __init__(
        self,
        http_client: ResilientHttpClient | None = None,
        *,
        subreddit_group: str = _SUBREDDIT_GROUP,
        secrets_store: SecretsStore | None = None,
        token_http_client: ResilientHttpClient | None = None,
    ) -> None:
        self._http = (
            http_client
            if http_client is not None
            else ResilientHttpClient(
                source_name="reddit",
                cache_ttl_seconds=_DEFAULT_TTL_SECONDS,
                max_concurrency=_DEFAULT_MAX_CONCURRENCY,
                user_agent=_USER_AGENT,
            )
        )
        # A dedicated client for the token POST with caching disabled: a bearer must never
        # enter the shared TTL response cache (ADR-0105 hygiene). The search client above
        # keeps its 300s TTL so repeated identical searches stay light on the rate-limit,
        # keyed or not.
        self._token_http = (
            token_http_client
            if token_http_client is not None
            else ResilientHttpClient(
                source_name="reddit-oauth-token",
                cache_ttl_seconds=0.0,
                max_concurrency=_DEFAULT_MAX_CONCURRENCY,
                user_agent=_USER_AGENT,
            )
        )
        self._group = subreddit_group
        self._secrets = secrets_store
        # In-process app-only bearer cache (Plan 0111): the token and the `_now`-relative
        # wall-clock after which it must be refreshed. `None` means "mint on next keyed
        # call". Not persisted — a bearer is cheap to re-mint and short-lived.
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def fetch_sentiment(self, symbol: str, window: str = "24h") -> SentimentSample:
        """Return the upvote-weighted crowd reading for `symbol` over `window`.

        `symbol` is used as the Reddit search query (case-normalised for the echoed
        `SentimentSample.symbol`). Routes to the keyed OAuth path when both Reddit secrets
        are configured, else the keyless path — identical scoring either way. Raises
        `ValueError` for an unsupported `window`. A rate-limited or failed fetch, a non-JSON
        body, or (keyed) a token failure **degrades to a neutral empty result** (score 0.0,
        all-zero breakdown) — no exception, no fabrication (ADR-0019/0105)."""
        ticker = symbol.strip().upper()
        cutoff = (_now() - window_delta(window)).timestamp()
        params: dict[str, str | int | float] = {
            "q": ticker,
            "restrict_sr": 1,
            "sort": "new",
            "t": _WINDOW_TO_REDDIT_T[window],
            "limit": _FETCH_LIMIT,
        }
        if self._keyed_enabled():
            return self._fetch_keyed(ticker, window, cutoff, params)
        return self._fetch_keyless(ticker, window, cutoff, params)

    def _fetch_keyless(
        self, ticker: str, window: str, cutoff: float, params: dict[str, str | int | float]
    ) -> SentimentSample:
        """Today's keyless search (unchanged): one GET to www.reddit.com; a rate-limit,
        failure, or non-JSON block page degrades to the neutral empty (ADR-0019)."""
        url = _BASE_URL.format(group=self._group)
        try:
            response = self._http.get(url, params=params, expect_json=True)
        except ResilientHttpError:
            return _empty(ticker, window)
        return self._score(response.json(), ticker, window, cutoff)

    def _fetch_keyed(
        self, ticker: str, window: str, cutoff: float, params: dict[str, str | int | float]
    ) -> SentimentSample:
        """Keyed app-only OAuth search (Plan 0111): a bearer GET on oauth.reddit.com with
        the same params/scoring. A 401 triggers exactly one token refresh + retry; any
        token or search failure degrades to the same neutral empty as the keyless path."""
        url = _OAUTH_SEARCH_URL.format(group=self._group)
        bearer = self._bearer()
        if bearer is None:
            return _empty(ticker, window)
        try:
            response = self._search(url, params, bearer)
        except ResilientHttpError as err:
            if not _is_unauthorized(err):
                return _empty(ticker, window)
            refreshed = self._refresh_token()
            if refreshed is None:
                return _empty(ticker, window)
            try:
                response = self._search(url, params, refreshed)
            except ResilientHttpError:
                return _empty(ticker, window)
        return self._score(response.json(), ticker, window, cutoff)

    def _search(self, url: str, params: dict[str, str | int | float], bearer: str) -> HttpResponse:
        """One authenticated search GET. The bearer rides `headers=`, so it stays out of the
        cache key and the path-only failure log (secret hygiene, ADR-0105)."""
        return self._http.get(
            url,
            params=params,
            headers={"Authorization": f"bearer {bearer}"},
            expect_json=True,
        )

    def _keyed_enabled(self) -> bool:
        """True iff both OAuth secrets are configured — the gate ADR-0105 keys on."""
        if self._secrets is None:
            return False
        return bool(self._secrets.get("reddit_client_id")) and bool(
            self._secrets.get("reddit_client_secret")
        )

    def _bearer(self) -> str | None:
        """A valid cached bearer, or a freshly minted one when absent/expired; `None` when
        the token cannot be obtained (folds into the honest-empty degrade)."""
        if self._token is not None and _now().timestamp() < self._token_expires_at:
            return self._token
        return self._refresh_token()

    def _refresh_token(self) -> str | None:
        """Mint an app-only bearer via the client_credentials grant, cache it with an expiry
        margin, and return it — or `None` on any failure. HTTP Basic rides `headers=` and the
        grant rides the form `data=`; the token client has caching disabled so the response
        never enters the shared cache (ADR-0105 secret hygiene)."""
        client_id = self._secrets.get("reddit_client_id") if self._secrets is not None else None
        secret = self._secrets.get("reddit_client_secret") if self._secrets is not None else None
        if not client_id or not secret:
            return None
        basic = base64.b64encode(f"{client_id}:{secret}".encode()).decode("ascii")
        try:
            response = self._token_http.post(
                _TOKEN_URL,
                data=b"grant_type=client_credentials",
                headers={"Authorization": f"Basic {basic}"},
                expect_json=True,
            )
        except ResilientHttpError:
            self._token = None
            return None
        payload = response.json()
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            self._token = None
            return None
        expires_in = (
            _coerce_expires(payload.get("expires_in")) if isinstance(payload, dict) else 0.0
        )
        self._token = token
        self._token_expires_at = _now().timestamp() + expires_in - _TOKEN_EXPIRY_MARGIN_SECONDS
        return token

    def _score(self, payload: Any, ticker: str, window: str, cutoff: float) -> SentimentSample:
        """The upvote-weighted aggregation shared by both paths — identical scoring
        regardless of how the listing was fetched."""
        positive = negative = neutral = 0
        weighted_sum = 0.0
        total_weight = 0.0
        for post in _posts(payload):
            if _created_utc(post) < cutoff:
                continue
            polarity = _polarity(post)
            weight = float(max(_upvotes(post), 1))
            weighted_sum += weight * polarity
            total_weight += weight
            if polarity > 0:
                positive += 1
            elif polarity < 0:
                negative += 1
            else:
                neutral += 1

        score = weighted_sum / total_weight if total_weight > 0 else 0.0
        return SentimentSample(
            symbol=ticker,
            score=score,
            window=window,
            as_of=_now(),
            source=_SOURCE,
            breakdown={"positive": positive, "negative": negative, "neutral": neutral},
        )


def _now() -> datetime:
    """Wall-clock seam, monkeypatched by tests to freeze time (cf. stocktwits._now)."""
    return datetime.now(tz=UTC)


def _is_unauthorized(err: ResilientHttpError) -> bool:
    """True when the failed request's last response was an HTTP 401 — the signal to refresh
    the app-only bearer once and retry (Plan 0111 / ADR-0105)."""
    return err.last_response is not None and err.last_response.status_code == 401


def _coerce_expires(raw: Any) -> float:
    """Reddit's `expires_in` (seconds) as a strictly-positive float, else 0.0 — an
    unparseable/absent lifetime treats the token as already at its margin, so it is re-minted
    on the next call rather than trusted indefinitely."""
    if isinstance(raw, bool):
        return 0.0
    if isinstance(raw, (int, float)):
        return float(raw) if raw > 0 else 0.0
    if isinstance(raw, str):
        try:
            value = float(raw)
        except ValueError:
            return 0.0
        return value if value > 0 else 0.0
    return 0.0


def _empty(ticker: str, window: str) -> SentimentSample:
    """The honest-empty degrade result: neutral, zero-count, never fabricated."""
    return SentimentSample(
        symbol=ticker,
        score=0.0,
        window=window,
        as_of=_now(),
        source=_SOURCE,
        breakdown={"positive": 0, "negative": 0, "neutral": 0},
    )


def _posts(payload: Any) -> list[Any]:
    """Extract the post `data` objects from a Reddit listing, defensively — a
    shape-broken payload yields `[]` (folded into the neutral empty), never a raise."""
    data = payload.get("data") if isinstance(payload, dict) else None
    children = data.get("children") if isinstance(data, dict) else None
    if not isinstance(children, list):
        return []
    posts: list[Any] = []
    for child in children:
        inner = child.get("data") if isinstance(child, dict) else None
        if isinstance(inner, dict):
            posts.append(inner)
    return posts


def _created_utc(post: Any) -> float:
    """Post creation epoch-seconds; a missing/non-numeric value sorts as very old (0.0),
    so it falls outside any window rather than being counted with a fabricated time."""
    raw = post.get("created_utc") if isinstance(post, dict) else None
    return float(raw) if isinstance(raw, int | float) else 0.0


def _upvotes(post: Any) -> int:
    """Net upvote score (Reddit's `score`, falling back to `ups`); non-numeric → 0."""
    for key in ("score", "ups"):
        raw = post.get(key) if isinstance(post, dict) else None
        if isinstance(raw, int | float):
            return int(raw)
    return 0


def _polarity(post: Any) -> int:
    """Sign of (bullish - bearish) lexicon hits over the post's title + body: +1, 0, -1."""
    title = post.get("title") if isinstance(post, dict) else None
    body = post.get("selftext") if isinstance(post, dict) else None
    text = f"{title if isinstance(title, str) else ''} {body if isinstance(body, str) else ''}"
    tokens = _TOKEN_RE.findall(text.lower())
    bullish = sum(1 for token in tokens if token in _BULLISH_TERMS)
    bearish = sum(1 for token in tokens if token in _BEARISH_TERMS)
    if bullish > bearish:
        return 1
    if bearish > bullish:
        return -1
    return 0


__all__ = ["RedditSentimentAdapter", "reddit_label"]
