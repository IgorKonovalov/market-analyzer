"""Shared sentiment-window vocabulary (Plan 0012 followup).

The per-symbol sentiment adapters (`rss_news`, `stocktwits`) and their MCP tools
(`sentiment_for_news`, `stocktwits_sentiment`) all speak the same fixed window
vocabulary — `1h`/`4h`/`24h`/`7d`. Before this module each adapter carried its own
copy of the `_WINDOW_TO_DELTA` map and the `_window_delta` lookup, and each MCP
input model repeated the `Literal[...]` set. Three copies of one fact drift; this
is the single source.

`SentimentWindow` is a plain `Literal` alias (not a PEP 695 `type` statement) on
purpose: pydantic inlines a plain alias transparently, so the MCP models' JSON
schema — and the TypeScript the renderer generates from it — is byte-identical to
the inline `Literal` it replaces.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

SentimentWindow = Literal["1h", "4h", "24h", "7d"]

WINDOW_TO_DELTA: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
}


def window_delta(window: str) -> timedelta:
    """Map a window token to its `timedelta`, raising `ValueError` if unknown."""
    try:
        return WINDOW_TO_DELTA[window]
    except KeyError:
        raise ValueError(
            f"unsupported window {window!r}; supported: {sorted(WINDOW_TO_DELTA)}",
        ) from None


__all__ = ["SentimentWindow", "WINDOW_TO_DELTA", "window_delta"]
