"""`sentiment` MCP tool (Plan 0109 phase 3, ADR-0104).

One symbol-sentiment verb with a `source` discriminator, folding `sentiment_for_news`
(Plan 0010 — RSS + VADER) and `stocktwits_sentiment` (Plan 0012 — StockTwits crowd
labels) into modes of a single tool, plus `reddit` (Plan 0103 — keyless crowd lexicon)
and `x` (Plan 0108 — keyed LunarCrush X/social aggregate). `source` ∈ {`news`,
`stocktwits`, `reddit`, `x`}. Each source is a handler bound in a **registry**
(`DEFAULT_SENTIMENT_SOURCES`) that `register_sentiment` takes as an injectable
dependency — so adding a source (the 0103 Reddit binding, then the 0108 social one,
ADR-0104 §Notes) is one enum value on `SentimentSource` + one registry entry, with
**no new module and no new `register_*` call**. The `x` entry is factory-built
(`make_x_source`): the composition root closes it over the `SecretsStore` so the
handler can honestly say "no key configured" (ADR-0103 honest-empty posture) instead
of a bare empty; the default registry carries the store-less variant, which reports
exactly that.

Both sources return `dict[str, Any]` (as the retired tools did) — FastMCP leaves the
mapping as the structured content unchanged, so this consolidation is a zero-shape
change (unlike the disjoint-model clusters that need the `{kind, result}` envelope). Each
handler produces its predecessor's exact payload: `news` returns
{score, window, source, breakdown, queried_at}; `stocktwits` adds the upper-cased
`symbol` and maps an untracked ticker to a clear tool error. There is no `as_of` — every
sentiment source is wall-clock-sensitive, so historical replay is not offered here.

The provider calls (RSS/VADER, StockTwits) are synchronous and offloaded with
`asyncio.to_thread` so a slow feed cannot stall the loop. The body is factored as
`_sentiment_response` so the dispatch + per-source paths are unit-testable without a live
MCP server.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from market_analyser.data import UnknownSymbolError
from market_analyser.data._windows import SentimentWindow
from market_analyser.data.adapters.reddit_sentiment import reddit_label
from market_analyser.data.adapters.social_sentiment import social_label
from market_analyser.data.provider import MarketDataProvider
from market_analyser.persistence.secrets import SecretsStore

SentimentSource = Literal["news", "stocktwits", "reddit", "x"]

# A source handler maps (provider, symbol, window) to the source's payload dict. Each
# handler owns its source-specific input normalisation (StockTwits upper-cases + rejects
# punctuation) and error mapping — the registry is the ADR-0104 extension point.
SentimentHandler = Callable[[MarketDataProvider, str, SentimentWindow], Awaitable[dict[str, Any]]]

# StockTwits ticker rule (was the retired tool's input pattern): letters, "." (BRK.B /
# BTC.X) and "-", up to 10 chars — rejects punctuation like "AAPL$".
_STOCKTWITS_SYMBOL_RE = re.compile(r"^[A-Za-z.\-]+$")
_STOCKTWITS_SYMBOL_MAXLEN = 10

SENTIMENT_DESCRIPTION = (
    "Summarise crowd/news sentiment for a symbol over a window; `source` selects the "
    "feed. Returns {score (in [-1, 1]), window, source, breakdown "
    "(positive/negative/neutral counts), queried_at}. "
    "source='news': mean VADER compound over each recent RSS headline (source tag "
    "'rss-vader'); no headlines in the window returns score 0.0 with an all-zero "
    "breakdown (zero, not unknown). "
    "source='stocktwits': (bullish - bearish) / labeled-post count from StockTwits' "
    "explicit post labels (no NLP; source tag 'stocktwits') — the payload also echoes "
    "the upper-cased `symbol`; pass the exact StockTwits ticker (AAPL for stocks, the "
    "'.X' suffix for crypto like BTC.X/ETH.X); patchy small-cap coverage returns an "
    "all-zero breakdown (neutral, not unknown), a symbol StockTwits does not track is an "
    "error. "
    "source='reddit': keyless upvote-weighted keyword-lexicon score over one fixed "
    "multi-subreddit crowd group (r/CryptoCurrency+Bitcoin+wallstreetbets+stocks+"
    "investing) searched for the symbol (source tag 'reddit'); the payload adds a "
    "`label` (Strongly Bullish..Strongly Bearish) and `sample_size` (scored-post count); "
    "Reddit rate-limits hard, so an empty result (score 0.0, all-zero breakdown) may be a "
    "rate-limit rather than genuine silence — never fabricated. "
    "source='x': KEYED X (Twitter) / social crowd sentiment via the LunarCrush "
    "aggregator (source tag 'x'): the vendor's 0..100 aggregate maps linearly to the "
    "signed score, per-network polarity interaction counts form the breakdown, and the "
    "payload adds `label` and `sample_size` (polarity-classified interactions) like the "
    "reddit mode. Requires the `lunarcrush_api_key` secret — absent it the source is "
    "inert and returns an honest-empty result with a `note` (not an error). The upstream "
    "read is a current social snapshot (roughly 24h), so `window` does not narrow it; "
    "the funded tier budget is small (~100 requests/day, 4/min — responses are cached "
    "~15 min), so a keyed empty result may be a rate-limit rather than silence — never "
    "fabricated. "
    "`window` is one of 1h/4h/24h/7d. Wall-clock-sensitive — no historical "
    "replay (no as_of). This is a CONDITION (crowd/news mood), never buy/sell advice."
)


async def _news_source(
    provider: MarketDataProvider, symbol: str, window: SentimentWindow
) -> dict[str, Any]:
    """`source="news"` handler — RSS + VADER aggregation (was `sentiment_for_news`)."""

    sample = await asyncio.to_thread(provider.get_sentiment, symbol=symbol, window=window)
    return {
        "score": sample.score,
        "window": sample.window,
        "source": sample.source,
        "breakdown": sample.breakdown,
        "queried_at": datetime.now(tz=UTC).isoformat(),
    }


async def _stocktwits_source(
    provider: MarketDataProvider, symbol: str, window: SentimentWindow
) -> dict[str, Any]:
    """`source="stocktwits"` handler — StockTwits crowd labels (was
    `stocktwits_sentiment`). Owns the source-specific symbol rule (punctuation rejected,
    upper-cased) and maps an untracked ticker to a clear tool error, not a 500."""

    if len(symbol) > _STOCKTWITS_SYMBOL_MAXLEN or _STOCKTWITS_SYMBOL_RE.match(symbol) is None:
        raise ValueError(
            f"symbol {symbol!r} is not a valid StockTwits ticker "
            "(letters, '.', '-'; up to 10 chars)",
        )
    symbol = symbol.upper()
    try:
        sample = await asyncio.to_thread(
            provider.get_sentiment,
            symbol=symbol,
            window=window,
            source="stocktwits",
        )
    except UnknownSymbolError as err:
        raise ValueError(f"symbol {symbol!r} is not tracked by StockTwits") from err
    return {
        "symbol": symbol,
        "score": sample.score,
        "window": sample.window,
        "source": sample.source,
        "breakdown": sample.breakdown,
        "queried_at": datetime.now(tz=UTC).isoformat(),
    }


async def _reddit_source(
    provider: MarketDataProvider, symbol: str, window: SentimentWindow
) -> dict[str, Any]:
    """`source="reddit"` handler — keyless Reddit crowd lexicon (Plan 0103).

    `label` and `sample_size` are derived here: the adapter returns a plain
    `SentimentSample` (score + positive/negative/neutral `breakdown`), so the presentation
    label stays out of the data layer. Reddit needs no special symbol rule (the symbol is a
    free-text search query) and degrades to a neutral empty reading rather than erroring, so
    there is no untracked-symbol mapping here."""

    sample = await asyncio.to_thread(
        provider.get_sentiment,
        symbol=symbol,
        window=window,
        source="reddit",
    )
    return {
        "symbol": sample.symbol,
        "score": sample.score,
        "label": reddit_label(sample.score),
        "sample_size": sum(sample.breakdown.values()),
        "breakdown": sample.breakdown,
        "source": sample.source,
        "window": sample.window,
        "queried_at": datetime.now(tz=UTC).isoformat(),
    }


def make_x_source(secrets_store: SecretsStore | None) -> SentimentHandler:
    """Build the `source="x"` handler — keyed LunarCrush X/social aggregate (Plan 0108).

    Factory rather than a plain handler so the composition root can close it over the
    real `SecretsStore`: when the key is absent the handler answers honest-empty **with
    a "no key configured" note** and issues no provider call at all (the adapter is
    inert too — this just names the reason at the payload level, ADR-0103). With a key,
    `label`/`sample_size` are derived here exactly like the reddit handler, keeping the
    presentation fields out of the data layer. A keyed-but-empty result stays bare —
    the description discloses that it may be a rate-limit or thin coverage."""

    async def _x_source(
        provider: MarketDataProvider, symbol: str, window: SentimentWindow
    ) -> dict[str, Any]:
        key = secrets_store.get("lunarcrush_api_key") if secrets_store is not None else None
        if not key:
            return {
                "symbol": symbol.strip().upper(),
                "score": 0.0,
                "label": social_label(0.0),
                "sample_size": 0,
                "breakdown": {"positive": 0, "negative": 0, "neutral": 0},
                "source": "x",
                "window": window,
                "note": (
                    "no LunarCrush key configured (lunarcrush_api_key) — "
                    "the x/social source is inert until a key is set"
                ),
                "queried_at": datetime.now(tz=UTC).isoformat(),
            }
        sample = await asyncio.to_thread(
            provider.get_sentiment,
            symbol=symbol,
            window=window,
            source="x",
        )
        return {
            "symbol": sample.symbol,
            "score": sample.score,
            "label": social_label(sample.score),
            "sample_size": sum(sample.breakdown.values()),
            "breakdown": sample.breakdown,
            "source": sample.source,
            "window": sample.window,
            "queried_at": datetime.now(tz=UTC).isoformat(),
        }

    return _x_source


DEFAULT_SENTIMENT_SOURCES: dict[str, SentimentHandler] = {
    "news": _news_source,
    "stocktwits": _stocktwits_source,
    "reddit": _reddit_source,
    # Store-less default: honestly reports "no key configured". The composition
    # root overrides this entry with `make_x_source(secrets_store)` (mcp_app).
    "x": make_x_source(None),
}


class SentimentInput(BaseModel):
    """MCP-boundary input. Unknown keys are rejected (`extra="forbid"`). Source-specific
    symbol rules (StockTwits punctuation/length) live in the source handler, so the
    boundary enforces only the shared minimum (non-empty symbol, known window)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: SentimentSource
    symbol: str = Field(min_length=1)
    window: SentimentWindow = "24h"


async def _sentiment_response(
    *,
    provider: MarketDataProvider,
    source: str,
    symbol: str,
    window: SentimentWindow,
    sources: Mapping[str, SentimentHandler],
) -> dict[str, Any]:
    """Body of the `sentiment` tool: resolve the source handler from the registry and
    dispatch. An unregistered source is a clear error, not a silent empty."""

    handler = sources.get(source)
    if handler is None:
        raise ValueError(f"sentiment source {source!r} not supported (one of {sorted(sources)})")
    return await handler(provider, symbol, window)


def register_sentiment(
    server: FastMCP,
    *,
    provider: MarketDataProvider,
    sources: Mapping[str, SentimentHandler] = DEFAULT_SENTIMENT_SOURCES,
) -> None:
    """Bind the `sentiment` tool to `server`. The provider + source registry are captured
    by closure so the tool body keeps its single declared parameter. `sources` is
    injectable (default `DEFAULT_SENTIMENT_SOURCES`) — the ADR-0104 extension point a new
    source binds into without a new module or `register_*` call."""

    @server.tool(name="sentiment", description=SENTIMENT_DESCRIPTION)
    async def sentiment(params: SentimentInput) -> dict[str, Any]:
        return await _sentiment_response(
            provider=provider,
            source=params.source,
            symbol=params.symbol,
            window=params.window,
            sources=sources,
        )


__all__ = [
    "DEFAULT_SENTIMENT_SOURCES",
    "SENTIMENT_DESCRIPTION",
    "SentimentHandler",
    "SentimentInput",
    "SentimentSource",
    "_news_source",
    "_reddit_source",
    "_sentiment_response",
    "_stocktwits_source",
    "make_x_source",
    "register_sentiment",
]
