"""RSS news adapter — Plan 0010 phase 1 (ADR-0019, ADR-0007).

Fetches a curated catalog of free RSS feeds through `ResilientHttpClient` (the
shared TTL cache / retry / backoff / concurrency cap), parses each with
`feedparser`, and returns `NewsItem` rows newest-first. One feed failing degrades
gracefully — it is logged at WARNING and skipped, never raising — so a single
dead feed does not kill the whole news call.

Symbol filtering is a whole-word token match (see `_symbol_match`), not a
substring match, so `ETH` does not match `together`. Package-internal per
ADR-0007: downstream code reaches this through `MarketDataProvider`, never by
importing this class.
"""

from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import feedparser

from market_analyser import __version__
from market_analyser.data import _vader
from market_analyser.data._http import ResilientHttpClient, ResilientHttpError
from market_analyser.data._symbol_match import symbol_matches
from market_analyser.data.types import NewsItem

_logger = logging.getLogger(__name__)

_USER_AGENT = f"market-analyser/{__version__}"

# 5-minute TTL: news refreshes far slower than screener results (ADR-0019).
_DEFAULT_TTL_SECONDS = 300.0
# Be a polite client against five feeds — don't fan out all at once.
_DEFAULT_MAX_CONCURRENCY = 2

_WINDOW_TO_DELTA: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
}


@dataclass(frozen=True)
class Feed:
    """One catalog entry: where to fetch, what kind of news, and per-feed quirks."""

    url: str
    category: str  # "crypto" | "equity" | "general"
    needs_ua: bool = False  # Yahoo/CNBC reject requests without a User-Agent.


# Curated free-feed catalog. Adding a feed is a one-row change. The shared HTTP
# client always sends a User-Agent; `needs_ua` makes it explicit per-call for the
# feeds that reject UA-less requests (the others rely on the client default).
_FEED_CATALOG: dict[str, Feed] = {
    "coindesk": Feed(url="https://feeds.coindesk.com/feed", category="crypto"),
    "cointelegraph": Feed(url="https://cointelegraph.com/rss", category="crypto"),
    "yahoo_finance": Feed(
        url="https://finance.yahoo.com/news/rssindex", category="equity", needs_ua=True
    ),
    "marketwatch": Feed(
        url="https://feeds.marketwatch.com/marketwatch/topstories/", category="equity"
    ),
    "cnbc": Feed(
        url="https://www.cnbc.com/id/100003114/device/rss/rss.html",
        category="equity",
        needs_ua=True,
    ),
}


class RssNewsAdapter:
    """Fetches and parses the RSS feed catalog into `NewsItem` rows."""

    def __init__(self, http_client: ResilientHttpClient | None = None) -> None:
        self._http = (
            http_client
            if http_client is not None
            else ResilientHttpClient(
                source_name="rss-news",
                cache_ttl_seconds=_DEFAULT_TTL_SECONDS,
                max_concurrency=_DEFAULT_MAX_CONCURRENCY,
            )
        )

    def fetch(
        self,
        symbol: str | None = None,
        window: str = "24h",
        limit: int = 50,
        with_sentiment: bool = False,
    ) -> list[NewsItem]:
        """Return recent news items newest-first, capped at `limit`.

        With `symbol=None`, returns the union across all feeds; otherwise keeps
        only items whose title or summary mentions `symbol` as a whole-word
        token. A feed that fails is logged and skipped (graceful degradation).
        With `with_sentiment=True`, each item carries a VADER `compound_sentiment`
        over its title + summary; otherwise that field stays `None`.
        """
        cutoff = _now() - _window_delta(window)
        applied_symbol = symbol if symbol is not None else ""
        items: list[NewsItem] = []
        for name, feed in _FEED_CATALOG.items():
            try:
                raw = self._fetch_feed(feed)
            except ResilientHttpError:
                _logger.warning("rss-news: feed %r unavailable, skipping", name)
                continue
            items.extend(
                self._parse_feed(raw, name, cutoff, symbol, applied_symbol, with_sentiment)
            )
        items.sort(key=lambda item: item.published_at, reverse=True)
        return items[:limit]

    def _fetch_feed(self, feed: Feed) -> bytes:
        headers = {"User-Agent": _USER_AGENT} if feed.needs_ua else None
        return self._http.get(feed.url, headers=headers).body

    def _parse_feed(
        self,
        raw: bytes,
        source: str,
        cutoff: datetime,
        symbol: str | None,
        applied_symbol: str,
        with_sentiment: bool,
    ) -> list[NewsItem]:
        parsed = feedparser.parse(raw)
        out: list[NewsItem] = []
        for entry in parsed.entries:
            published = _entry_published(entry)
            if published is None or published < cutoff:
                continue
            title = str(entry.get("title", "")).strip()
            link = str(entry.get("link", "")).strip()
            if not title or not link:
                continue
            summary = str(entry.get("summary", "")).strip()
            if symbol is not None and not symbol_matches(symbol, f"{title} {summary}"):
                continue
            out.append(
                NewsItem(
                    symbol=applied_symbol,
                    title=title,
                    url=link,
                    published_at=published,
                    source=source,
                    summary=summary,
                    compound_sentiment=(
                        _vader.score_headline(title, summary) if with_sentiment else None
                    ),
                )
            )
        return out


def _now() -> datetime:
    """Wall-clock seam, monkeypatched by tests to freeze time."""
    return datetime.now(tz=UTC)


def _window_delta(window: str) -> timedelta:
    try:
        return _WINDOW_TO_DELTA[window]
    except KeyError:
        raise ValueError(
            f"unsupported window {window!r}; supported: {sorted(_WINDOW_TO_DELTA)}",
        ) from None


def _entry_published(entry: Any) -> datetime | None:
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if struct is None:
        return None
    # feedparser normalises *_parsed to a UTC time.struct_time; timegm reads it
    # as UTC (mktime would wrongly apply the local offset).
    return datetime.fromtimestamp(calendar.timegm(struct), tz=UTC)


__all__ = ["Feed", "RssNewsAdapter"]
