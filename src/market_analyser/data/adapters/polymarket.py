"""Polymarket prediction-market odds adapter — Plan 0040 phase 1 (ADR-0041, ADR-0031).

Read-only odds from the **public, auth-free** Polymarket Gamma API
(`gamma-api.polymarket.com`) through `ResilientHttpClient`. A prediction market
trades each outcome between 0 and 1 and the **price is the market-implied
probability directly** — no derivation. The adapter holds **no key, signs
nothing, moves no funds** (ADR-0041): every call is an unauthenticated GET.

Two capabilities, both `PredictionMarketSource` (ADR-0031):

- `search_markets(query)` — `GET /public-search?q=…` returns active *events*, each
  carrying a `markets` array; the adapter flattens the odds-bearing markets out and
  parses each. A market with no outcomes/prices yet (not yet trading) is skipped —
  it is not corruption, just an absence of odds; genuine shape drift still raises.
- `fetch_market(market_id)` — `GET /markets/{id}` returns one market; a 404 raises
  the typed `UnknownMarketError` (the id is wrong / the market was removed — a
  retry won't help), any other upstream failure the resilient client exhausts maps
  to the `UpstreamDataError` taxonomy.

Odds provenance: Gamma denormalizes the outcome's **CLOB midpoint** into the
`outcomePrices` field — verified empirically at build (a market's
`outcomePrices[i]` equalled the CLOB `/midpoint` for its `clobTokenIds[i]`), so a
single keyless Gamma call yields the CLOB-derived implied probability that the
plan's diagram attributes to the CLOB endpoint. The `clobTokenIds` are not needed
for the odds and are not carried; a future refinement that wants the *live book*
(best-bid/ask spread, not just the mid) is a followup that would call
`clob.polymarket.com/midpoint` per token — deliberately out of scope here to keep
one keyless call per market.

Wire shape (both `outcomes` and `outcomePrices` are **JSON-encoded string
arrays**, parallel by index): `"outcomes": "[\"Yes\", \"No\"]"`,
`"outcomePrices": "[\"0.0585\", \"0.9415\"]"`. Volume / liquidity ride the numeric
`volumeNum` / `liquidityNum` fields (honest-uncertainty hints). A malformed /
missing-field payload raises the typed `PolymarketError` before model construction
— upstream drift surfaces typed, never a silently fabricated probability.

Package-internal per ADR-0007: downstream code reaches this through the
prediction-market selector registry, never by importing this class.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote as urlquote
from urllib.parse import urlsplit

from market_analyser.data._http import ResilientHttpClient, ResilientHttpError
from market_analyser.data.errors import (
    RateLimitedError,
    UpstreamDataError,
    UpstreamUnavailableError,
)
from market_analyser.data.types import MarketOutcome, PredictionMarket

_GAMMA_BASE = "https://gamma-api.polymarket.com"
_MARKETS_URL = f"{_GAMMA_BASE}/markets"
_SEARCH_URL = f"{_GAMMA_BASE}/public-search"
_SOURCE = "polymarket"

# Canonical public market page: `polymarket.com/event/<event-slug>` (Plan 0089).
# The numeric market id does NOT resolve to a page; the **event** slug does —
# live-confirmed 2026-07-12 against the real Gamma `public-search` (each event
# carries a `slug`; `polymarket.com/event/<slug>` returns 200, a bogus slug 404s).
# The slug is external data, so the built URL is host-validated before it is
# trusted (ADR-0041 no-fabrication, ADR-0008 external-nav): exact `https` scheme,
# exact `polymarket.com` host, and a single URL-safe path segment (no slash /
# percent / query / fragment that could alter the path or host).
_POLYMARKET_HOST = "polymarket.com"
_POLYMARKET_EVENT_BASE = f"https://{_POLYMARKET_HOST}/event/"
_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# Odds move continuously, but a short TTL absorbs the "agent asks twice in a few
# seconds" pattern without ever serving a stale reading in practice (ADR-0019).
_DEFAULT_TTL_SECONDS = 30.0

_DEFAULT_SEARCH_LIMIT = 20
# A hard ceiling so a caller can't ask the upstream for an unbounded page.
_MAX_SEARCH_LIMIT = 100


class PolymarketError(ValueError):
    """The upstream 2xx payload broke shape — a non-object market, a non-string
    or non-JSON `outcomes` / `outcomePrices`, a length mismatch between the two,
    or a non-numeric / out-of-`[0,1]` outcome price — raised at the adapter
    boundary before model construction. Upstream drift surfaces typed, never as a
    silently fabricated or zeroed probability (ADR-0041)."""


class UnknownMarketError(UpstreamDataError):
    """The upstream has no market with the requested id (Gamma HTTP 404). Distinct
    from a transient outage — a retry won't help; the id is wrong or the market
    was removed. Carries `market_id` so the caller can echo it back."""

    def __init__(self, message: str, *, market_id: str) -> None:
        super().__init__(message)
        self.market_id = market_id


class PolymarketOddsAdapter:
    """Reads Polymarket prediction-market odds (`PredictionMarketSource`, ADR-0031)
    from the public Gamma API — no auth, no key, no signing, no funds (ADR-0041).

    `now` is the provenance seam: `PredictionMarket.queried_at` is stamped from it,
    so tests inject a fixed clock and the read stays deterministic. It defaults to
    the wall clock (live reads); the provider/composition-root never needs to pass
    it."""

    def __init__(
        self,
        http_client: ResilientHttpClient | None = None,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._http = (
            http_client
            if http_client is not None
            else ResilientHttpClient(
                source_name=_SOURCE,
                cache_ttl_seconds=_DEFAULT_TTL_SECONDS,
            )
        )
        self._now = now if now is not None else lambda: datetime.now(tz=UTC)

    def search_markets(
        self,
        query: str,
        *,
        limit: int = _DEFAULT_SEARCH_LIMIT,
    ) -> list[PredictionMarket]:
        """`PredictionMarketSource`: markets matching `query`, with current odds.

        `GET /public-search?q=…&events_status=active` returns active events; the
        odds-bearing markets are flattened out of them and parsed, capped at
        `limit`. A market not yet trading (no outcomes/prices) is skipped — an
        absence of odds, not corruption; genuine shape drift still raises
        `PolymarketError`.

        An empty/whitespace query short-circuits to `[]`; a zero-match query also
        returns `[]` (not an error). Raises `RateLimitedError` on HTTP 429 and
        `UpstreamUnavailableError` on other upstream exhaustion; `ValueError` for a
        non-positive `limit`."""
        query = query.strip()
        if not query:
            return []
        if limit < 1:
            raise ValueError("limit must be >= 1")
        capped = min(limit, _MAX_SEARCH_LIMIT)
        params: dict[str, str | int | float] = {
            "q": query,
            "limit_per_type": capped,
            "events_status": "active",
        }
        try:
            payload = self._http.get(_SEARCH_URL, params=params, expect_json=True).json()
        except ResilientHttpError as err:
            raise _classify_error(err, what=f"search for {query!r}") from err
        queried_at = self._now()
        markets = _parse_search(payload, queried_at=queried_at)
        return markets[:capped]

    def fetch_market(self, market_id: str) -> PredictionMarket:
        """`PredictionMarketSource`: one market's outcomes + implied probabilities
        by id, from `GET /markets/{id}`.

        Raises `UnknownMarketError` on a 404 (unknown/removed id — never retried),
        `RateLimitedError` on 429, `UpstreamUnavailableError` on other upstream
        exhaustion, `PolymarketError` on a shape-broken payload, and `ValueError`
        for an empty `market_id`."""
        market_id = market_id.strip()
        if not market_id:
            raise ValueError("market_id must be non-empty")
        url = f"{_MARKETS_URL}/{urlquote(market_id, safe='')}"
        try:
            payload = self._http.get(url, expect_json=True).json()
        except ResilientHttpError as err:
            if err.last_response is not None and err.last_response.status_code == 404:
                raise UnknownMarketError(
                    f"polymarket: no market with id {market_id!r}",
                    market_id=market_id,
                ) from err
            raise _classify_error(err, what=f"market {market_id}") from err
        if not isinstance(payload, dict):
            raise PolymarketError(f"polymarket: market payload for {market_id} is not an object")
        return _parse_market(payload, queried_at=self._now())


def _parse_search(payload: Any, *, queried_at: datetime) -> list[PredictionMarket]:
    """Flatten the odds-bearing markets out of a `/public-search` payload. The
    shape is `{"events": [{…, "markets": [market, …]}, …]}`; a market with no
    outcomes/prices yet is skipped (an absence of odds, not corruption), while a
    market whose odds fields are present-but-broken raises `PolymarketError`."""
    if not isinstance(payload, dict):
        raise PolymarketError("polymarket: search payload is not an object")
    events = payload.get("events")
    if events is None:
        return []
    if not isinstance(events, list):
        raise PolymarketError("polymarket: search payload 'events' is not a list")
    markets: list[PredictionMarket] = []
    for event in events:
        if not isinstance(event, dict):
            raise PolymarketError("polymarket: search 'events' entry is not an object")
        raw_markets = event.get("markets")
        if raw_markets is None:
            continue
        if not isinstance(raw_markets, list):
            raise PolymarketError("polymarket: event 'markets' is not a list")
        # The public market page is keyed by the *event* slug, which lives on the
        # event wrapper the flatten would otherwise drop (Plan 0089). Capture it
        # here and hand it to each of the event's markets.
        event_slug = event.get("slug")
        for raw in raw_markets:
            if not isinstance(raw, dict):
                raise PolymarketError("polymarket: 'markets' entry is not an object")
            if not raw.get("outcomes") or not raw.get("outcomePrices"):
                # Not yet trading — no odds to report. Skip, don't fabricate.
                continue
            markets.append(_parse_market(raw, queried_at=queried_at, event_slug=event_slug))
    return markets


def _parse_market(
    raw: dict[str, Any], *, queried_at: datetime, event_slug: Any = None
) -> PredictionMarket:
    """Parse one Gamma market object into a boundary-validated `PredictionMarket`.
    Every shape problem raises `PolymarketError` before model construction.

    `event_slug` is the market's parent-event slug (from search flattening) used to
    build the canonical public URL; `None`/absent (e.g. the by-id `fetch_market`
    path, which has no event wrapper) yields `market_url is None`."""
    market_id = raw.get("id")
    if not isinstance(market_id, str) or not market_id:
        raise PolymarketError("polymarket: market missing string 'id'")
    question = raw.get("question")
    if not isinstance(question, str) or not question:
        raise PolymarketError(f"polymarket: market {market_id} missing string 'question'")

    labels = _decode_json_str_array(raw.get("outcomes"), field="outcomes", market_id=market_id)
    prices = _decode_json_str_array(
        raw.get("outcomePrices"), field="outcomePrices", market_id=market_id
    )
    if len(labels) != len(prices):
        raise PolymarketError(
            f"polymarket: market {market_id} has {len(labels)} outcomes but {len(prices)} prices",
        )
    outcomes = [
        MarketOutcome(
            label=_require_label(label, market_id=market_id),
            implied_probability=_parse_probability(price, market_id=market_id),
        )
        for label, price in zip(labels, prices, strict=True)
    ]

    return PredictionMarket(
        market_id=market_id,
        question=question,
        outcomes=outcomes,
        closed=bool(raw.get("closed", False)),
        closes_at=_parse_optional_iso(raw.get("endDate")),
        volume_usd=_optional_nonneg_number(raw.get("volumeNum")),
        liquidity_usd=_optional_nonneg_number(raw.get("liquidityNum")),
        queried_at=queried_at,
        source=_SOURCE,
        market_url=_build_event_url(event_slug),
    )


def _build_event_url(event_slug: Any) -> str | None:
    """Build the canonical public Polymarket URL from an event slug, or `None` when
    the slug is absent or unusable. The slug is external data, so the constructed
    URL is host-validated (exact `https` scheme + `polymarket.com` host, single
    URL-safe path segment) before it is trusted — a fabricated, path-manipulating,
    or off-host link is never emitted (ADR-0041, ADR-0008). Only the **event** slug
    builds the page: `polymarket.com/event/<event-slug>` (the numeric market id
    does not resolve). Live-confirmed 2026-07-12."""
    if not isinstance(event_slug, str):
        return None
    slug = event_slug.strip()
    if not _SLUG_RE.match(slug):
        return None
    url = f"{_POLYMARKET_EVENT_BASE}{slug}"
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != _POLYMARKET_HOST:
        return None
    return url


def _decode_json_str_array(value: Any, *, field: str, market_id: str) -> list[str]:
    """Decode a Gamma JSON-encoded string array (e.g. `outcomes`, `outcomePrices`
    arrive as the *string* `'["Yes", "No"]'`). A non-string value, non-JSON body,
    non-list result, or non-string element is shape drift → `PolymarketError`."""
    if not isinstance(value, str):
        raise PolymarketError(
            f"polymarket: market {market_id} field {field!r} is not a JSON string",
        )
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        raise PolymarketError(
            f"polymarket: market {market_id} field {field!r} is not valid JSON",
        ) from None
    if not isinstance(decoded, list) or not all(isinstance(x, str) for x in decoded):
        raise PolymarketError(
            f"polymarket: market {market_id} field {field!r} is not a string array",
        )
    return decoded


def _require_label(label: str, *, market_id: str) -> str:
    label = label.strip()
    if not label:
        raise PolymarketError(f"polymarket: market {market_id} has an empty outcome label")
    return label


def _parse_probability(price: str, *, market_id: str) -> float:
    """Parse an `outcomePrices` element (a decimal string like `"0.0585"`) into a
    probability. A non-numeric value, or one outside `[0, 1]` / non-finite, is
    upstream drift → `PolymarketError` (never a silently zeroed probability)."""
    try:
        prob = float(price)
    except (TypeError, ValueError):
        raise PolymarketError(
            f"polymarket: market {market_id} has non-numeric outcome price {price!r}",
        ) from None
    if not math.isfinite(prob) or not 0.0 <= prob <= 1.0:
        raise PolymarketError(
            f"polymarket: market {market_id} outcome price {prob!r} outside [0, 1]",
        )
    return prob


def _parse_optional_iso(value: Any) -> datetime | None:
    """Parse a Gamma ISO-8601 `endDate` (e.g. `"2026-07-20T00:00:00Z"`) into a
    UTC-aware datetime, or `None` when absent/blank/unparseable. Non-load-bearing
    metadata (the close time), so an odd value yields `None` rather than sinking
    the whole market — the odds are what matter."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _optional_nonneg_number(value: Any) -> float | None:
    """Parse an optional non-negative numeric hint (`volumeNum` / `liquidityNum`)
    to `float`, or `None` when absent, non-numeric, non-finite, or negative. Never
    raises — these are honest-uncertainty hints, not the load-bearing odds."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return float(value)


def _classify_error(err: ResilientHttpError, *, what: str) -> UpstreamDataError:
    """Translate an exhausted/permanent `ResilientHttpError` into the typed
    taxonomy: HTTP 429 → rate-limited (carrying `Retry-After` when present); any
    other status or transport failure → upstream-unavailable. (Polymarket public
    reads are not geo-restricted — the Dec-2025 CFTC approval, ADR-0041 — so there
    is no 451 branch.)"""
    resp = err.last_response
    if resp is not None and resp.status_code == 429:
        return RateLimitedError(
            f"polymarket: rate limited (HTTP 429) fetching {what}",
            retry_after_seconds=_parse_retry_after(_header(resp.headers, "Retry-After")),
        )
    if resp is not None:
        detail = f"HTTP {resp.status_code}"
    else:
        detail = type(err.last_exception).__name__ if err.last_exception is not None else "unknown"
    return UpstreamUnavailableError(
        f"polymarket: upstream unavailable ({detail}) fetching {what}",
    )


def _header(headers: dict[str, str], name: str) -> str | None:
    """Case-insensitive header lookup (urllib preserves the upstream's casing)."""
    lowered = name.lower()
    return next((v for k, v in headers.items() if k.lower() == lowered), None)


def _parse_retry_after(value: str | None) -> int | None:
    """Parse a `Retry-After` header as whole seconds; the HTTP-date form is
    unsupported (returns `None`) — the agent gets the rate-limit signal regardless."""
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


__all__ = [
    "PolymarketError",
    "PolymarketOddsAdapter",
    "UnknownMarketError",
]
