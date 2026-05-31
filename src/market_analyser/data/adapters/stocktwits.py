"""StockTwits per-symbol sentiment adapter — Plan 0012 (ADR-0019, ADR-0007).

StockTwits attaches an explicit, user-applied sentiment label
(`Bullish` / `Bearish` / none) to each post, served by
`GET /api/2/streams/symbol/{ticker}.json`. This adapter counts those labels over
a time window and returns a `SentimentSample` — no NLP model, unlike the
news-VADER path (Plan 0010). The signed score is

    (bullish - bearish) / max(1, bullish + bearish)        in [-1, 1]

so "no labels" and "no posts" both read as 0.0 (neutral, not unknown).

Symbol handling is **pass-through**: the caller supplies the exact StockTwits
ticker. Crypto Bitcoin is `BTC.X` (`instrument_class: CRYPTO`); bare `BTC` is a
*different* instrument (a Grayscale Bitcoin ETF) that also returns 200, so the
adapter never auto-suffixes — doing so would silently serve the wrong stream
(Plan 0012, "Amended again" note).

This is the first adapter to *subclass* `ResilientHttpClient`: StockTwits' free
tier signals "over rate limit" with HTTP 403 (not 429), so `classify` is
overridden to map that one case to `RATELIMIT`. Package-internal per ADR-0007:
downstream reaches this through `MarketDataProvider`, never by importing it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from market_analyser.data._http import (
    ErrorKind,
    HttpResponse,
    ResilientHttpClient,
    ResilientHttpError,
)
from market_analyser.data._windows import window_delta
from market_analyser.data.errors import UnknownSymbolError
from market_analyser.data.types import SentimentSample

_BASE_URL = "https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
_SOURCE = "stocktwits"

# 5-minute TTL: sentiment is wall-clock-current but doesn't move every second.
_DEFAULT_TTL_SECONDS = 300.0
# StockTwits is less generous than TradingView; don't fan out aggressively.
_DEFAULT_MAX_CONCURRENCY = 2


class StockTwitsHttpClient(ResilientHttpClient):
    """`ResilientHttpClient` that understands StockTwits' 403-as-rate-limit quirk.

    The free tier returns HTTP 403 with a rate-limit message body when over quota
    (not the conventional 429). Map *that* shape to `RATELIMIT` (retry with the
    longer floor); every other 403 falls through to the base classifier, which
    treats 4xx as `PERMANENT`.
    """

    def classify(self, exc: BaseException | None, response: HttpResponse | None) -> ErrorKind:
        if response is not None and response.status_code == 403 and _is_rate_limited(response):
            return ErrorKind.RATELIMIT
        return super().classify(exc, response)


class StockTwitsAdapter:
    """Fetches StockTwits posts for a ticker and counts their sentiment labels."""

    def __init__(self, http_client: StockTwitsHttpClient | None = None) -> None:
        self._http: ResilientHttpClient = (
            http_client
            if http_client is not None
            else StockTwitsHttpClient(
                source_name="stocktwits",
                cache_ttl_seconds=_DEFAULT_TTL_SECONDS,
                max_concurrency=_DEFAULT_MAX_CONCURRENCY,
            )
        )

    def fetch_sentiment(self, symbol: str, window: str = "24h") -> SentimentSample:
        """Return the labeled-sentiment reading for `symbol` over `window`.

        `symbol` is the exact StockTwits ticker (pass-through, case-normalised):
        `AAPL` for the stock, `BTC.X` for crypto Bitcoin. Raises
        `UnknownSymbolError` when StockTwits doesn't track the ticker (404),
        `ResilientHttpError` on any other upstream failure, and `ValueError` for
        an unsupported `window`.
        """
        ticker = symbol.strip().upper()
        cutoff = _now() - window_delta(window)
        url = _BASE_URL.format(ticker=ticker)
        try:
            response = self._http.get(url, expect_json=True)
        except ResilientHttpError as err:
            if err.last_response is not None and err.last_response.status_code == 404:
                raise UnknownSymbolError(
                    f"stocktwits: symbol {ticker!r} is not tracked",
                    symbol=ticker,
                ) from err
            raise
        positive = negative = neutral = 0
        for message in _messages(response.json()):
            posted = _created_at(message)
            if posted is None or posted < cutoff:
                continue
            label = _label(message)
            if label == "Bullish":
                positive += 1
            elif label == "Bearish":
                negative += 1
            else:
                neutral += 1
        labeled = positive + negative
        return SentimentSample(
            symbol=ticker,
            score=(positive - negative) / max(1, labeled),
            window=window,
            as_of=_now(),
            source=_SOURCE,
            breakdown={"positive": positive, "negative": negative, "neutral": neutral},
        )


def _now() -> datetime:
    """Wall-clock seam, monkeypatched by tests to freeze time (cf. rss_news._now)."""
    return datetime.now(tz=UTC)


def _messages(payload: Any) -> list[Any]:
    if not isinstance(payload, dict):
        return []
    messages = payload.get("messages")
    return messages if isinstance(messages, list) else []


def _created_at(message: Any) -> datetime | None:
    if not isinstance(message, dict):
        return None
    raw = message.get("created_at")
    if not isinstance(raw, str):
        return None
    try:
        # StockTwits emits ISO-8601 with a trailing 'Z'; 3.10's fromisoformat
        # doesn't accept 'Z', so normalise it to an explicit offset.
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _label(message: Any) -> str | None:
    entities = message.get("entities") if isinstance(message, dict) else None
    sentiment = entities.get("sentiment") if isinstance(entities, dict) else None
    if not isinstance(sentiment, dict):
        return None
    basic = sentiment.get("basic")
    return basic if isinstance(basic, str) else None


def _is_rate_limited(response: HttpResponse) -> bool:
    """StockTwits signals rate-limiting with a 403 whose body mentions it; match on
    the message text so we're robust to `{"error": ...}` vs `{"errors": [...]}`."""
    return "rate limit" in response.text.lower()


__all__ = [
    "StockTwitsAdapter",
    "StockTwitsHttpClient",
]
