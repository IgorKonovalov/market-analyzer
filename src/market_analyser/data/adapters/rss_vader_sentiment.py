"""RSS-VADER sentiment adapter (Plan 0028 phase 2 / ADR-0031).

Composes a `NewsSource` with VADER scoring: fetch the symbol's news with
sentiment on, then aggregate the per-item compound scores into one
`SentimentSample` (mean score + positive/neutral/negative breakdown). This logic
was inlined in `DefaultMarketDataProvider._news_vader_sentiment`; extracting it
behind `SentimentSource` lets the provider dispatch sentiment via a registry
lookup instead of a source-specific branch, and keeps source-specific math out
of the provider.

The `as_of` wall-clock is injected (`now`) rather than read here, so the
provider stays the single owner of that determinism seam — tests freeze it once
on the provider and both this adapter and the provider observe the frozen value.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from market_analyser.data.sources import NewsSource, SentimentSource
from market_analyser.data.types import SentimentSample

# VADER's conventional compound-score cutoffs for the positive/neutral/negative
# split used to build the sentiment breakdown.
_SENTIMENT_POSITIVE = 0.05
_SENTIMENT_NEGATIVE = -0.05
_SOURCE = "rss-vader"


def _now() -> datetime:
    """Default wall-clock for the sample `as_of` when no `now` is injected."""
    return datetime.now(tz=UTC)


class RssVaderSentimentAdapter(SentimentSource):
    """Aggregates a `NewsSource`'s VADER-scored items into a `SentimentSample`."""

    def __init__(
        self,
        news: NewsSource,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._news = news
        self._now = now if now is not None else _now

    def fetch_sentiment(self, symbol: str, window: str = "24h") -> SentimentSample:
        items = self._news.fetch(symbol=symbol, window=window, with_sentiment=True)
        scores = [item.compound_sentiment for item in items if item.compound_sentiment is not None]
        # No news = zero (neutral) sentiment, not unknown sentiment.
        mean = sum(scores) / len(scores) if scores else 0.0
        positive = sum(1 for s in scores if s > _SENTIMENT_POSITIVE)
        negative = sum(1 for s in scores if s < _SENTIMENT_NEGATIVE)
        return SentimentSample(
            symbol=symbol,
            score=mean,
            window=window,
            as_of=self._now(),
            source=_SOURCE,
            breakdown={
                "positive": positive,
                "negative": negative,
                "neutral": len(scores) - positive - negative,
            },
        )
