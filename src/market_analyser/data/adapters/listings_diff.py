"""Crypto listings/delistings self-diff adapter — Plan 0113 phase 3 (ADR-0107, ADR-0019).

No exchange publishes an official listing-announcement API, so the only robust keyless
signal is **self-diffing** the venues' tradeable-symbol sets against a persisted prior
snapshot: a symbol that appears is a listing, one that disappears is a delisting
(ADR-0107). This catches *tradeable* add/remove events on Binance (`exchangeInfo`) and
Coinbase (`products`); it deliberately **misses forward announcements and forks/
upgrades**, and every event says so (honest incompleteness, not hidden).

**Cold start baselines, it does not detect.** A venue with no prior snapshot has
nothing to diff against — the provider records the baseline and emits nothing (by
design, ADR-0107 risk). Only from the second observation on can it detect a change.

**Empty-guard against spurious delistings.** A venue read that returns *no* symbols
(a partial upstream failure that still parsed, or a shape-broken 200) is skipped with
a note and the snapshot is left untouched — otherwise an empty read would look like
"everything delisted". Only a non-empty read updates the baseline.

**Keyless and honest-degrade (ADR-0019).** Each venue is read independently over the
resilient client; a failed or shape-broken read skips that venue with a note and
leaves its snapshot unchanged — never an exception, never a fabricated event.

Conforms to `EventCalendarSource.fetch_events` (ADR-0031); package-internal per
ADR-0007 — reached through the `event_calendar` tool's registry, never imported
directly.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from market_analyser.data._http import ResilientHttpClient, ResilientHttpError
from market_analyser.data.sources import EventCalendarSource
from market_analyser.data.types import CalendarFetch, MarketEvent
from market_analyser.persistence.repositories.listing_snapshots import ListingSnapshotsRepository

_BINANCE_EXCHANGE_INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"
_COINBASE_PRODUCTS_URL = "https://api.exchange.coinbase.com/products"

# A short TTL absorbs repeat calendar reads; the venue sets move slowly.
_DEFAULT_TTL_SECONDS = 900.0

_INCOMPLETENESS_NOTE = (
    "Detected by self-diffing exchange tradeable-symbol sets; scheduled_at is the "
    "DETECTION time, not the on-chain listing block. Forward announcements and "
    "forks/upgrades are NOT covered (ADR-0107)."
)


@runtime_checkable
class ListingVenueSource(Protocol):
    """Returns a venue's current set of tradeable symbol identifiers (Binance pairs,
    Coinbase product ids). Raises the resilient client's error on transport failure;
    a shape-broken payload yields an empty set (the diff's empty-guard handles it)."""

    def fetch_symbols(self) -> set[str]: ...


def _utcnow() -> datetime:
    """Wall-clock seam; the calendar is wall-clock-sensitive (no as_of)."""
    return datetime.now(tz=UTC)


class BinanceListingVenue(ListingVenueSource):
    """The set of Binance symbols with `status == "TRADING"` from `exchangeInfo`."""

    def __init__(self, *, http_client: ResilientHttpClient | None = None) -> None:
        self._http = http_client or ResilientHttpClient(
            source_name="binance-listings", cache_ttl_seconds=_DEFAULT_TTL_SECONDS
        )

    def fetch_symbols(self) -> set[str]:
        response = self._http.get(_BINANCE_EXCHANGE_INFO_URL, expect_json=True)
        payload = response.json()
        rows = payload.get("symbols") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return set()
        return {
            row["symbol"]
            for row in rows
            if isinstance(row, dict)
            and row.get("status") == "TRADING"
            and isinstance(row.get("symbol"), str)
        }


class CoinbaseListingVenue(ListingVenueSource):
    """The set of Coinbase product ids that are `online` and not trading-disabled."""

    def __init__(self, *, http_client: ResilientHttpClient | None = None) -> None:
        self._http = http_client or ResilientHttpClient(
            source_name="coinbase-listings", cache_ttl_seconds=_DEFAULT_TTL_SECONDS
        )

    def fetch_symbols(self) -> set[str]:
        response = self._http.get(_COINBASE_PRODUCTS_URL, expect_json=True)
        payload = response.json()
        if not isinstance(payload, list):
            return set()
        return {
            row["id"]
            for row in payload
            if isinstance(row, dict)
            and row.get("status") == "online"
            and not row.get("trading_disabled", False)
            and isinstance(row.get("id"), str)
        }


class ListingsDiffSource(EventCalendarSource):
    """Diffs each venue's current tradeable-symbol set against its persisted baseline,
    emitting one listing/delisting event per add/remove, then overwriting the baseline.
    Cold start baselines silently; an empty or failed venue read is skipped."""

    def __init__(
        self,
        *,
        repository: ListingSnapshotsRepository,
        venues: Mapping[str, ListingVenueSource],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repo = repository
        self._venues = venues
        self._clock = clock if clock is not None else _utcnow

    def fetch_events(
        self, *, symbol: str | None = None, window: str | None = None
    ) -> CalendarFetch:
        now = self._clock()
        events: list[MarketEvent] = []
        notes: list[str] = [_INCOMPLETENESS_NOTE]
        for venue, source in self._venues.items():
            try:
                current = source.fetch_symbols()
            except ResilientHttpError:
                notes.append(
                    f"{venue} listing read unavailable (upstream error); snapshot unchanged."
                )
                continue
            if not current:
                notes.append(f"{venue} returned no tradeable symbols; snapshot unchanged.")
                continue
            prior = self._repo.get_symbols(venue)
            if prior is None:
                self._repo.replace(venue, current, now)
                notes.append(
                    f"{venue}: baseline recorded ({len(current)} symbols) — first run, no diff."
                )
                continue
            for sym in sorted(current - prior):
                events.append(_event(venue, sym, listed=True, now=now))
            for sym in sorted(prior - current):
                events.append(_event(venue, sym, listed=False, now=now))
            self._repo.replace(venue, current, now)
        return CalendarFetch(events=events, notes=notes)


def _event(venue: str, symbol: str, *, listed: bool, now: datetime) -> MarketEvent:
    """One listing (or delisting) event at detection time `now`."""
    verb = "listed" if listed else "delisted"
    return MarketEvent(
        category="listings",
        title=f"{symbol} {verb} on {venue.capitalize()}",
        symbol=symbol,
        scheduled_at=now,
        magnitude=None,
        source=venue,
        note=_INCOMPLETENESS_NOTE,
    )


def default_listing_venues() -> dict[str, ListingVenueSource]:
    """The keyless Binance + Coinbase venue readers, network-free to construct."""
    return {"binance": BinanceListingVenue(), "coinbase": CoinbaseListingVenue()}


__all__ = [
    "BinanceListingVenue",
    "CoinbaseListingVenue",
    "ListingVenueSource",
    "ListingsDiffSource",
    "default_listing_venues",
]
