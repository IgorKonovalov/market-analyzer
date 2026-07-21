"""`event_calendar` MCP tool (Plan 0113 phase 1, ADR-0107, ADR-0104, ADR-0029).

One discriminated **conditions-only** verb answering "what scheduled events are
coming" (ADR-0104 one-verb granularity): a `category` selects the calendar. Each
category composes one or more `EventCalendarSource` providers behind a **registry**
(`category → [sources]`) that `register_event_calendar` takes as an injectable
dependency — so adding a category (earnings in phase 2, listings in phase 3) is one
registry entry, and adding a provider to a category is one list append, with no new
`register_*` call.

Phase 1 ships `category="macro"`: the FOMC static seed (keyless, always available)
plus the FRED release-dates adapter (keyed — inert without `fred_api_key`). Every
provider **honest-degrades independently** (ADR-0019): a dead/unconfigured provider
contributes zero events and a `notes` entry, never an exception and never a
fabricated event. The tool unions the providers' events, sorts them by
`scheduled_at`, and concatenates their notes.

Events are **conditions, never calls** (ADR-0029): a `MarketEvent` carries no
action/signal/side/direction field, so neither the model nor the serialized payload
can be read as advice. **Wall-clock-sensitive with no `as_of`** (ADR-0107, the
sentiment-source posture): forward-looking scheduled facts, so repeated calls
legitimately differ as the calendar advances.

The providers' `fetch_events` is synchronous and may be network-bound (FRED), so it
is offloaded with `asyncio.to_thread` to keep the event loop responsive. The body is
factored as `_event_calendar_response` so the dispatch + union are unit-testable
without a live MCP server.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict

from market_analyser.data.adapters.finnhub_earnings import FinnhubEarningsSource
from market_analyser.data.adapters.fomc_seed import FomcSeedSource
from market_analyser.data.adapters.fred_releases import FredReleasesSource
from market_analyser.data.sources import EventCalendarSource
from market_analyser.data.types import MarketEvent
from market_analyser.persistence.secrets import SecretsStore

# Phase 1 exposed `macro`; phase 2 adds `earnings`. Phase 3 extends this to `listings`
# (one registry entry + one enum value each).
EventCalendarCategory = Literal["macro", "earnings"]

# The forward look-ahead horizon the `earnings` category bounds its calendar query by
# (ignored by the date-driven macro sources). Default 90d.
EarningsWindow = Literal["7d", "30d", "90d", "180d", "1y"]

# The registry the tool dispatches on: a category maps to the ordered list of
# `EventCalendarSource`s composed behind it. The ADR-0104 extension point.
EventCalendarRegistry = Mapping[str, Sequence[EventCalendarSource]]

EVENT_CALENDAR_DESCRIPTION = (
    "List upcoming SCHEDULED market events for a category — dated forward facts (a "
    "timestamp, sometimes a magnitude), never buy/sell advice (a CONDITION). Returns "
    "{category, events: [{category, title, symbol, scheduled_at (UTC ISO-8601), "
    "magnitude, source, note}], notes, queried_at}, events sorted by scheduled_at "
    "ascending. "
    "category='macro': upcoming FOMC rate-decision dates (from a curated seed — "
    "dates only, no consensus/actual numbers) plus CPI and PCE release dates from "
    "FRED. FRED needs a free `fred_api_key` secret; WITHOUT the key the macro read is "
    "FOMC-only and a `notes` entry says FRED is unconfigured (inert — zero requests). "
    "Coverage is honestly incomplete: release DATES, not the printed figures, and the "
    "curated FOMC seed can lag a Fed reschedule. "
    "category='earnings': upcoming equity earnings dates from Finnhub over a forward "
    "`window` (7d/30d/90d/180d/1y, default 90d); pass `symbol` (e.g. 'TSLA') to narrow "
    "to one company, or omit it for the whole window. Each event's `magnitude` is the "
    "EPS estimate where the free tier serves it (null when gated), and the `note` "
    "carries the session (before/after market), quarter/year, revenue estimate, and "
    "any gated field. Finnhub needs a free `finnhub_api_key` secret; WITHOUT the key "
    "the earnings read is honest-empty with a `notes` entry (inert — zero requests). "
    "Each degraded or unconfigured provider adds a `notes` entry rather than failing "
    "the call. "
    "Wall-clock-sensitive: forward-looking, no historical replay (no as_of) — "
    "repeated calls legitimately differ as the calendar advances."
)


def build_event_calendar_registry(
    secrets_store: SecretsStore | None,
) -> dict[str, list[EventCalendarSource]]:
    """Build the default `category → [sources]` registry (Plan 0113). `secrets_store`
    is threaded to the keyed providers (FRED, Finnhub), which stay inert until their
    key is present; it may be `None` (the apiref wiring / a store-less test), in which
    case the keyed providers report unconfigured. Constructed network-free — the
    providers reach out only on an actual call."""
    return {
        "macro": [
            FomcSeedSource(),
            FredReleasesSource(secrets_store=secrets_store),
        ],
        "earnings": [
            FinnhubEarningsSource(secrets_store=secrets_store),
        ],
    }


class EventCalendarInput(BaseModel):
    """MCP-boundary input. Unknown keys are rejected (`extra="forbid"`). `category`
    is the required discriminator selecting the calendar (ADR-0104); `symbol` and
    `window` narrow the `earnings` category (ignored by macro)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: EventCalendarCategory
    symbol: str | None = None
    window: EarningsWindow = "90d"


async def _event_calendar_response(
    *,
    registry: EventCalendarRegistry,
    category: str,
    symbol: str | None = None,
    window: str | None = None,
) -> dict[str, Any]:
    """Body of the `event_calendar` tool: resolve the category's providers from the
    registry, fan out (each honest-degrades independently), union the events sorted by
    `scheduled_at`, and concatenate the source-level notes. An unregistered category is
    a clear error, not a silent empty."""

    sources = registry.get(category)
    if sources is None:
        raise ValueError(
            f"event calendar category {category!r} not supported (one of {sorted(registry)})"
        )
    events: list[MarketEvent] = []
    notes: list[str] = []
    for source in sources:
        fetch = await asyncio.to_thread(source.fetch_events, symbol=symbol, window=window)
        events.extend(fetch.events)
        notes.extend(fetch.notes)
    events.sort(key=lambda event: event.scheduled_at)
    return {
        "category": category,
        "events": [event.model_dump(mode="json") for event in events],
        "notes": notes,
        "queried_at": datetime.now(tz=UTC).isoformat(),
    }


def register_event_calendar(
    server: FastMCP,
    *,
    registry: EventCalendarRegistry,
) -> None:
    """Bind the `event_calendar` tool to `server`. The category→providers registry is
    captured by closure so the tool body keeps its single declared parameter;
    `registry` is injectable (default built by `build_event_calendar_registry`) — the
    ADR-0104 extension point a new category binds into without a new `register_*`
    call."""

    @server.tool(name="event_calendar", description=EVENT_CALENDAR_DESCRIPTION)
    async def event_calendar(params: EventCalendarInput) -> dict[str, Any]:
        return await _event_calendar_response(
            registry=registry,
            category=params.category,
            symbol=params.symbol,
            window=params.window,
        )


__all__ = [
    "EVENT_CALENDAR_DESCRIPTION",
    "EarningsWindow",
    "EventCalendarCategory",
    "EventCalendarInput",
    "EventCalendarRegistry",
    "_event_calendar_response",
    "build_event_calendar_registry",
    "register_event_calendar",
]
