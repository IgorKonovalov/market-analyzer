"""The `GET /news` response envelope (Plan 0023 phase 1).

`NewsItem` and `SentimentSample` already exist in `data/types.py` (Plan 0010);
this module only adds the wrapper the renderer consumes. It is its own module
(not inline in `news.py`) so `gen-types.mjs` can find `NewsResponse` as a named
`response_model` in the OpenAPI dump alongside the two models it references.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from market_analyser.data.types import NewsItem, SentimentSample


class NewsResponse(BaseModel):
    """Envelope for `GET /news`: headlines + optional aggregate tone + fetch time."""

    model_config = ConfigDict(frozen=True)

    items: list[NewsItem]
    """Newest-first; each carries `compound_sentiment` (with_sentiment=True)."""
    sentiment: SentimentSample | None
    """Per-symbol aggregate tone; `None` when the request carried no symbol."""
    queried_at: datetime
    """Wall-clock of the fetch (ISO 8601 UTC)."""
