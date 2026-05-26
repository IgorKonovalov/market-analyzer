"""In-house Yahoo Finance symbol-search fetcher (Plan 0024 phase 1).

Builds the Yahoo ``/v1/finance/search`` request and parses the response into raw
symbol-quote dicts (the ``quotes`` array); the caller
(:meth:`~market_analyser.data.adapters.yahoo.YahooAdapter.search`) promotes these
into validated :class:`~market_analyser.data.types.SymbolInfo` models. The
``news`` array Yahoo also returns is requested off and ignored — this endpoint is
used purely for symbol discovery (ADR-0026).

Per ADR-0019 the request is issued through the shared
:class:`~market_analyser.data._http.ResilientHttpClient`, so search inherits the
same retry / backoff / TTL-cache / concurrency posture as the OHLCV chart fetch.
"""

from __future__ import annotations

from typing import Any

from market_analyser.data._http import ResilientHttpClient

_YF_SEARCH_BASE = "https://query1.finance.yahoo.com/v1/finance/search"


def _fetch_yahoo_search(
    query: str,
    *,
    client: ResilientHttpClient,
    quotes_count: int,
) -> list[dict[str, Any]]:
    """Fetch symbol-search quotes from Yahoo for ``query`` through ``client``.

    Returns the raw ``quotes`` list (one dict per match, in Yahoo's upstream
    relevance order). ``newsCount=0`` turns the (ignored) news section off. A
    response missing the ``quotes`` array yields ``[]`` — a zero-match search is
    not an error.
    """
    response = client.get(
        _YF_SEARCH_BASE,
        params={
            "q": query,
            "quotesCount": quotes_count,
            "newsCount": 0,
            "enableFuzzyQuery": "false",
        },
        expect_json=True,
    )
    return _parse_search_payload(response.json())


def _parse_search_payload(payload: Any) -> list[dict[str, Any]]:
    """Pull the ``quotes`` array out of a Yahoo search payload, defensively.

    The endpoint is reverse-engineered (ADR-0026 risk note), so a payload that is
    not a dict, or that lacks a list-valued ``quotes`` key, degrades to ``[]``
    rather than raising — the caller treats no-quotes as zero matches.
    """
    if not isinstance(payload, dict):
        return []
    quotes = payload.get("quotes")
    if not isinstance(quotes, list):
        return []
    return [quote for quote in quotes if isinstance(quote, dict)]
