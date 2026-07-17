"""X / social crowd-sentiment adapter — Plan 0108 (ADR-0103, ADR-0019, ADR-0038).

The fifth `SentimentSource`, covering the X (Twitter) crowd the news / StockTwits /
Fear & Greed / Reddit surfaces miss. Built **source-agnostically behind the seam**
(ADR-0103): the concrete provider is LunarCrush — an aggregator that ingests X and
exposes an already-aggregated sentiment score — reached over plain HTTP with the
key resolved lazily through `SecretsStore` (`lunarcrush_api_key`, ADR-0038). The
provider mapping is isolated in `_sample_from_payload`, so a later switch to the
raw X API (ADR-0103 alternative A) is an adapter rewrite behind the same Protocol,
not a redesign.

**Keyed, but inert without the key.** This source deliberately breaks the
keyless-first posture (recorded in ADR-0103); the compensating rule is that an
absent key makes the adapter **inert**: no request is issued and the result is a
neutral empty `SentimentSample` — never an exception, never fabricated data. The
same honest-empty degrade covers every failure mode on the resilient path
(ADR-0019): rate-limit (the funded tier is small — ~100 requests/day, 4/min),
transport exhaustion, and a shape-broken or non-JSON payload.

**Auth is a header, not the path (secret hygiene).** The key travels as
`Authorization: Bearer <key>` — never embedded in the URL — because the resilient
client's failure log records the URL *path* (query and headers are never logged,
ADR-0038 rule 1; cf. `alchemy_historical_price`).

**Provider mapping.** LunarCrush's topic endpoint returns a current social
snapshot: `data.sentiment` is the vendor's aggregate 0..100 bullishness, mapped
linearly to our signed score (`(sentiment - 50) / 50` in [-1, 1]); the
per-network `data.types_sentiment_detail` polarity counts (engagement-weighted
interactions, LunarCrush's own classification) are summed into the
positive/negative/neutral `breakdown`, so the consuming tool derives a sample
size exactly like the Reddit path. The vendor score is a black box relative to
the transparent in-house lexicons — recorded as an ADR-0103 negative, not hidden.
The snapshot is wall-clock-current with no window sub-selection upstream; the
requested `window` is validated against the shared vocabulary and echoed, and the
tool description discloses the snapshot semantics.

Conforms to `SentimentSource.fetch_sentiment` (ADR-0031) and is package-internal
per ADR-0007: downstream reaches this through
`MarketDataProvider.get_sentiment(source="x")`, never by importing it.
`social_label` is the pure score→label ladder, exported so the `sentiment` tool
derives the label without `data/` importing `api/` (mirrors `reddit_label`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from market_analyser.data._http import ResilientHttpClient, ResilientHttpError
from market_analyser.data._windows import window_delta
from market_analyser.data.sources import SentimentSource
from market_analyser.data.types import SentimentSample
from market_analyser.persistence.secrets import SecretsStore

_TOPIC_URL = "https://lunarcrush.com/api4/public/topic/{topic}/v1"
_SOURCE = "x"

# 15-minute TTL + no fan-out: the funded tier is ~100 requests/day at 4/min, so
# the cache absorbs repeat reads and the semaphore keeps bursts serial.
_DEFAULT_TTL_SECONDS = 900.0
_DEFAULT_MAX_CONCURRENCY = 1

# The polarity keys LunarCrush's `types_sentiment_detail` carries per network —
# identical vocabulary to our `SentimentSample.breakdown`.
_POLARITIES = ("positive", "negative", "neutral")


def social_label(score: float) -> str:
    """Map a signed -1..+1 social score to the five-bucket crowd-tone label.

    Deliberately the same ladder as `reddit_label` (symmetric cut-points at ±0.5
    and ±0.15) so agent consumers read one vocabulary across crowd sources; kept
    local so the two adapters stay independently swappable."""
    if score >= 0.5:
        return "Strongly Bullish"
    if score >= 0.15:
        return "Bullish"
    if score > -0.15:
        return "Neutral"
    if score > -0.5:
        return "Bearish"
    return "Strongly Bearish"


class SocialSentimentAdapter(SentimentSource):
    """Fetches a symbol's aggregated X/social sentiment from LunarCrush's topic
    endpoint. Inert (honest-empty, no request) until a `lunarcrush_api_key`
    secret is present."""

    def __init__(
        self,
        *,
        secrets_store: SecretsStore | None = None,
        http_client: ResilientHttpClient | None = None,
    ) -> None:
        self._secrets = secrets_store
        self._http = (
            http_client
            if http_client is not None
            else ResilientHttpClient(
                source_name="lunarcrush",
                cache_ttl_seconds=_DEFAULT_TTL_SECONDS,
                max_concurrency=_DEFAULT_MAX_CONCURRENCY,
            )
        )

    def fetch_sentiment(self, symbol: str, window: str = "24h") -> SentimentSample:
        """The vendor-aggregated social reading for `symbol`, or a neutral empty.

        Raises `ValueError` for an unsupported `window`. An absent key issues no
        request (inert); a rate-limited or failed fetch, a non-JSON body, or a
        shape-broken payload all **degrade to a neutral empty result** (score 0.0,
        all-zero breakdown) — no exception, no fabrication (ADR-0019/0103)."""
        ticker = symbol.strip().upper()
        window_delta(window)  # validate against the shared vocabulary
        key = self._secrets.get("lunarcrush_api_key") if self._secrets is not None else None
        if not key:
            return _empty(ticker, window)
        url = _TOPIC_URL.format(topic=ticker.lower())
        try:
            # Key in the Authorization header (never the URL path) so it cannot
            # reach the client's path-only failure log.
            response = self._http.get(
                url,
                headers={"Authorization": f"Bearer {key}"},
                expect_json=True,
            )
        except ResilientHttpError:
            return _empty(ticker, window)
        return _sample_from_payload(response.json(), ticker, window)


def _now() -> datetime:
    """Wall-clock seam, monkeypatched by tests to freeze time (cf. reddit_sentiment)."""
    return datetime.now(tz=UTC)


def _empty(ticker: str, window: str) -> SentimentSample:
    """The honest-empty result: neutral, zero-count, never fabricated."""
    return SentimentSample(
        symbol=ticker,
        score=0.0,
        window=window,
        as_of=_now(),
        source=_SOURCE,
        breakdown={"positive": 0, "negative": 0, "neutral": 0},
    )


def _sample_from_payload(payload: Any, ticker: str, window: str) -> SentimentSample:
    """Map a LunarCrush topic payload to a `SentimentSample`, defensively — a
    shape-broken payload or an unusable vendor score folds into the neutral
    empty, never a raise and never a fabricated number."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return _empty(ticker, window)
    score = _score(data.get("sentiment"))
    if score is None:
        return _empty(ticker, window)
    return SentimentSample(
        symbol=ticker,
        score=score,
        window=window,
        as_of=_now(),
        source=_SOURCE,
        breakdown=_breakdown(data.get("types_sentiment_detail")),
    )


def _score(raw: Any) -> float | None:
    """The signed score from LunarCrush's aggregate `sentiment` (0..100 bullish
    percentage): `(raw - 50) / 50` in [-1, 1]. Non-numeric or out-of-range input
    is garbage → `None` (folded into the neutral empty), never clamped into a
    fabricated reading."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if not 0.0 <= value <= 100.0:
        return None
    return (value - 50.0) / 50.0


def _breakdown(raw: Any) -> dict[str, int]:
    """Sum the per-network polarity interaction counts into one breakdown.

    `types_sentiment_detail` is `{network: {positive, neutral, negative}}`;
    missing/shape-broken parts contribute zero — the counts stay honest (what
    could be read), never invented."""
    counts = {polarity: 0 for polarity in _POLARITIES}
    if not isinstance(raw, dict):
        return counts
    for detail in raw.values():
        if not isinstance(detail, dict):
            continue
        for polarity in _POLARITIES:
            value = detail.get(polarity)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                counts[polarity] += value
    return counts


__all__ = ["SocialSentimentAdapter", "social_label"]
