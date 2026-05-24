"""Whole-word ticker matching for news/sentiment filtering — Plan 0010 phase 1.

Matches a ticker against free text on word boundaries, case-insensitively, so
short or common substrings do not produce false positives: ``ETH`` does not
match ``together`` and ``BTC`` does not match ``BTCUSD``. Tickers shorter than
two characters never match — a one-letter ticker like ``T`` (AT&T) would hit in
almost any sentence (Plan 0010 risk: false-positive token matches on short
tickers).

Package-internal (underscore prefix); reused by the RSS news adapter and, later,
news-derived sentiment.
"""

from __future__ import annotations

import re
from functools import lru_cache

_MIN_TICKER_LEN = 2


def symbol_matches(symbol: str, text: str) -> bool:
    """Return ``True`` if ``symbol`` appears as a whole word in ``text``.

    Case-insensitive. Tickers shorter than ``_MIN_TICKER_LEN`` always return
    ``False`` (false-positive guard); empty text returns ``False``.
    """
    ticker = symbol.strip()
    if len(ticker) < _MIN_TICKER_LEN or not text:
        return False
    return _pattern_for(ticker.upper()).search(text) is not None


@lru_cache(maxsize=512)
def _pattern_for(ticker_upper: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(ticker_upper)}\b", re.IGNORECASE)
