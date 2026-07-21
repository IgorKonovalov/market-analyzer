"""Plan 0113 phase 3 — offline tests for the crypto listings/delistings self-diff.

Two layers are pinned. The **venue fetchers** (`BinanceListingVenue`,
`CoinbaseListingVenue`) are driven through a `ResilientHttpClient` whose transport
seam is monkeypatched, asserting the tradeable-symbol filter (Binance `status=TRADING`,
Coinbase `online` + not `trading_disabled`). The **diff source** runs over fake venues
and a real in-memory `ListingSnapshotsRepository`, pinning the phase-3 done-when: a
cold start records a baseline and emits nothing; a one-symbol add/remove emits exactly
one listing and one delisting with the symbol, venue, and detection time; an empty or
failed venue read leaves the snapshot untouched (no spurious delistings); and every
payload carries the honest-incompleteness note.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from market_analyser.data._http import HttpResponse, ResilientHttpClient, ResilientHttpError
from market_analyser.data.adapters.listings_diff import (
    BinanceListingVenue,
    CoinbaseListingVenue,
    ListingsDiffSource,
    ListingVenueSource,
)
from market_analyser.persistence.engine import apply_migrations, make_engine, make_session_factory
from market_analyser.persistence.repositories.listing_snapshots import ListingSnapshotsRepository

_NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
_ACTION_KEYS = {"action", "signal", "side", "direction", "recommendation", "call"}


@pytest.fixture
def repo() -> Iterator[ListingSnapshotsRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    factory: sessionmaker[Session] = make_session_factory(engine)
    yield ListingSnapshotsRepository(factory)
    engine.dispose()


class _FakeVenue(ListingVenueSource):
    """Returns a preset symbol set (mutable between calls to simulate a change)."""

    def __init__(self, symbols: set[str]) -> None:
        self.symbols = symbols

    def fetch_symbols(self) -> set[str]:
        return set(self.symbols)


class _RaisingVenue(ListingVenueSource):
    def fetch_symbols(self) -> set[str]:
        raise ResilientHttpError(
            source_name="fake", last_response=None, last_exception=None, attempts=1
        )


def _source(
    repo: ListingSnapshotsRepository, venues: dict[str, ListingVenueSource]
) -> ListingsDiffSource:
    return ListingsDiffSource(repository=repo, venues=venues, clock=lambda: _NOW)


# -- venue fetchers ---------------------------------------------------------


def _spy_client(
    monkeypatch: pytest.MonkeyPatch, body: bytes
) -> tuple[ResilientHttpClient, list[str]]:
    client = ResilientHttpClient(source_name="listings-test", max_retries=0)
    urls: list[str] = []

    def fake(method: str, url: str, body_: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        urls.append(url)
        return HttpResponse(status_code=200, headers={}, body=body, elapsed_seconds=0.0)

    monkeypatch.setattr(client, "_perform_request", fake)
    return client, urls


def test_binance_venue_filters_to_trading(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps(
        {
            "symbols": [
                {"symbol": "BTCUSDT", "status": "TRADING"},
                {"symbol": "OLDUSDT", "status": "BREAK"},  # not tradeable
                {"symbol": "ETHUSDT", "status": "TRADING"},
            ]
        }
    ).encode()
    client, urls = _spy_client(monkeypatch, body)

    symbols = BinanceListingVenue(http_client=client).fetch_symbols()

    assert symbols == {"BTCUSDT", "ETHUSDT"}
    assert urls == ["https://api.binance.com/api/v3/exchangeInfo"]


def test_coinbase_venue_filters_to_online_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps(
        [
            {"id": "BTC-USD", "status": "online", "trading_disabled": False},
            {"id": "DEAD-USD", "status": "delisted", "trading_disabled": True},
            {"id": "PAUSE-USD", "status": "online", "trading_disabled": True},  # disabled
            {"id": "ETH-USD", "status": "online", "trading_disabled": False},
        ]
    ).encode()
    client, _ = _spy_client(monkeypatch, body)

    symbols = CoinbaseListingVenue(http_client=client).fetch_symbols()

    assert symbols == {"BTC-USD", "ETH-USD"}


# -- diff source ------------------------------------------------------------


def test_cold_start_records_baseline_without_events(repo: ListingSnapshotsRepository) -> None:
    source = _source(repo, {"binance": _FakeVenue({"BTCUSDT", "ETHUSDT"})})

    fetch = source.fetch_events()

    assert fetch.events == []  # first run: nothing to diff against
    assert repo.get_symbols("binance") == {"BTCUSDT", "ETHUSDT"}  # baseline recorded
    assert any("baseline recorded" in note for note in fetch.notes)


def test_detects_one_add_and_one_remove(repo: ListingSnapshotsRepository) -> None:
    venue = _FakeVenue({"BTCUSDT", "ETHUSDT"})
    source = _source(repo, {"binance": venue})
    source.fetch_events()  # baseline {BTC, ETH}

    venue.symbols = {"BTCUSDT", "SOLUSDT"}  # ETH delisted, SOL listed
    fetch = source.fetch_events()

    kinds = {(event.symbol, event.title.split()[1]) for event in fetch.events}
    assert kinds == {("SOLUSDT", "listed"), ("ETHUSDT", "delisted")}
    for event in fetch.events:
        assert event.category == "listings"
        assert event.source == "binance"
        assert event.scheduled_at == _NOW  # detection time
        assert not _ACTION_KEYS & set(event.model_dump(mode="json"))
    assert repo.get_symbols("binance") == {"BTCUSDT", "SOLUSDT"}  # baseline advanced


def test_empty_read_preserves_snapshot_no_spurious_delistings(
    repo: ListingSnapshotsRepository,
) -> None:
    venue = _FakeVenue({"BTCUSDT", "ETHUSDT"})
    source = _source(repo, {"binance": venue})
    source.fetch_events()  # baseline

    venue.symbols = set()  # partial upstream failure that still "parsed"
    fetch = source.fetch_events()

    assert fetch.events == []  # NOT two delistings
    assert repo.get_symbols("binance") == {"BTCUSDT", "ETHUSDT"}  # snapshot untouched
    assert any("no tradeable symbols" in note for note in fetch.notes)


def test_venue_failure_degrades_and_preserves_snapshot(
    repo: ListingSnapshotsRepository,
) -> None:
    repo.replace("binance", {"BTCUSDT"}, _NOW)
    source = _source(repo, {"binance": _RaisingVenue()})

    fetch = source.fetch_events()

    assert fetch.events == []
    assert repo.get_symbols("binance") == {"BTCUSDT"}  # unchanged
    assert any("unavailable" in note for note in fetch.notes)


def test_incompleteness_note_always_present(repo: ListingSnapshotsRepository) -> None:
    source = _source(repo, {"binance": _FakeVenue({"BTCUSDT"})})

    fetch = source.fetch_events()

    assert any("forks/upgrades are NOT covered" in note for note in fetch.notes)


def test_venues_diff_independently(repo: ListingSnapshotsRepository) -> None:
    binance = _FakeVenue({"BTCUSDT"})
    coinbase = _FakeVenue({"BTC-USD"})
    source = _source(repo, {"binance": binance, "coinbase": coinbase})
    source.fetch_events()  # baseline both

    coinbase.symbols = {"BTC-USD", "SOL-USD"}  # only Coinbase changes
    fetch = source.fetch_events()

    assert [(event.source, event.symbol) for event in fetch.events] == [("coinbase", "SOL-USD")]
