"""`GET /search` — symbol search for the renderer's autocomplete (Plan 0024).

Delegates to `request.app.state.provider.search_symbols(...)`. Renderer-bearer-
gated by the central middleware in `app.py`; a request bearing the MCP secret is
rejected cross-tenant (the agent uses the `search_symbols` MCP tool instead).

Mirrors `routes/ohlcv.py`'s error mapping: a typed upstream failure (or a raw
`ResilientHttpError`) surfaces as 502, a bad-input `ValueError` as 422. An
empty/whitespace query is a no-op that returns `[]` with 200 — the renderer
fires on each keystroke and a just-cleared box must not 422.
"""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter, HTTPException, Query, Request

from market_analyser.data._http import ResilientHttpError
from market_analyser.data.errors import UpstreamDataError
from market_analyser.data.provider import MarketDataProvider
from market_analyser.data.types import SymbolInfo

router = APIRouter()


@router.get("/search", response_model=list[SymbolInfo])
def search_symbols(
    request: Request,
    q: str = Query(default=""),
) -> Sequence[SymbolInfo]:
    if not q.strip():
        return []
    provider: MarketDataProvider = request.app.state.provider
    try:
        return provider.search_symbols(query=q)
    except (UpstreamDataError, ResilientHttpError) as exc:
        # Upstream (Yahoo) failed or exhausted retries — surface a clean 502.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
