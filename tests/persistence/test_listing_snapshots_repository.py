"""Plan 0113 phase 3 — the `listing_snapshots` repository (ADR-0107).

Done-when claims pinned here: (a) a venue with no row reads as `None` (the cold-start
signal); (b) `replace` upserts (insert then overwrite) and round-trips a symbol set;
(c) the stored blob is a sorted JSON array (deterministic, no set-iteration order);
(d) a shape-broken stored blob reads as `None` (safe re-baseline) rather than raising.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from market_analyser.persistence.engine import apply_migrations, make_engine, make_session_factory
from market_analyser.persistence.models.listing_snapshots import ListingSnapshotRow
from market_analyser.persistence.repositories.listing_snapshots import ListingSnapshotsRepository

_NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield make_session_factory(engine)
    engine.dispose()


@pytest.fixture
def repo(session_factory: sessionmaker[Session]) -> ListingSnapshotsRepository:
    return ListingSnapshotsRepository(session_factory)


def test_missing_venue_reads_none(repo: ListingSnapshotsRepository) -> None:
    assert repo.get_symbols("binance") is None  # cold start


def test_replace_then_get_round_trips(repo: ListingSnapshotsRepository) -> None:
    repo.replace("binance", {"BTCUSDT", "ETHUSDT"}, _NOW)
    assert repo.get_symbols("binance") == {"BTCUSDT", "ETHUSDT"}


def test_replace_overwrites(repo: ListingSnapshotsRepository) -> None:
    repo.replace("binance", {"BTCUSDT", "ETHUSDT"}, _NOW)
    repo.replace("binance", {"BTCUSDT", "SOLUSDT"}, _NOW)
    assert repo.get_symbols("binance") == {"BTCUSDT", "SOLUSDT"}


def test_stored_blob_is_sorted_json(
    repo: ListingSnapshotsRepository, session_factory: sessionmaker[Session]
) -> None:
    repo.replace("coinbase", {"SOL-USD", "BTC-USD", "ETH-USD"}, _NOW)
    with session_factory() as session:
        row = session.get(ListingSnapshotRow, "coinbase")
        assert row is not None
        assert json.loads(row.symbols_json) == ["BTC-USD", "ETH-USD", "SOL-USD"]  # sorted


def test_venues_are_independent(repo: ListingSnapshotsRepository) -> None:
    repo.replace("binance", {"BTCUSDT"}, _NOW)
    repo.replace("coinbase", {"BTC-USD"}, _NOW)
    assert repo.get_symbols("binance") == {"BTCUSDT"}
    assert repo.get_symbols("coinbase") == {"BTC-USD"}


def test_shape_broken_blob_reads_none(
    repo: ListingSnapshotsRepository, session_factory: sessionmaker[Session]
) -> None:
    with session_factory() as session:
        session.add(ListingSnapshotRow(venue="binance", symbols_json="{not json", captured_at=_NOW))
        session.commit()
    assert repo.get_symbols("binance") is None  # safe re-baseline, not a raise
