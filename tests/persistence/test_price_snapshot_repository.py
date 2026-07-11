"""Plan 0035 phase 4: the `price_snapshots` first-write-wins repository.

The determinism mechanism's storage half: a snapshot, once taken, is never
overwritten; a garbage price (zero / NaN / negative) is rejected at the
boundary so it can never poison a replay. The migration chain stays linear
(0006 → 0007, single head).
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from market_analyser.persistence.engine import (
    MIGRATIONS_PACKAGE,
    apply_migrations,
    make_engine,
    make_session_factory,
)
from market_analyser.persistence.price_snapshot_repository import PriceSnapshotRepository

_TOKEN = "base:0x940181a94a35a4569e4529a3cdfb74e38fd98631"
_TS = 1730000000


@pytest.fixture
def repo() -> Iterator[PriceSnapshotRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield PriceSnapshotRepository(make_session_factory(engine))
    engine.dispose()


def test_put_then_get_round_trips(repo: PriceSnapshotRepository) -> None:
    assert repo.put(_TOKEN, _TS, 1.21) is True
    assert repo.get(_TOKEN, _TS) == 1.21


def test_get_missing_snapshot_returns_none(repo: PriceSnapshotRepository) -> None:
    assert repo.get(_TOKEN, _TS) is None


def test_first_write_wins(repo: PriceSnapshotRepository) -> None:
    assert repo.put(_TOKEN, _TS, 1.21) is True
    assert repo.put(_TOKEN, _TS, 9.99) is False
    assert repo.get(_TOKEN, _TS) == 1.21, "an existing snapshot is never overwritten"


def test_distinct_timestamps_are_distinct_snapshots(repo: PriceSnapshotRepository) -> None:
    repo.put(_TOKEN, _TS, 1.21)
    repo.put(_TOKEN, _TS + 3600, 1.30)
    assert repo.get(_TOKEN, _TS) == 1.21
    assert repo.get(_TOKEN, _TS + 3600) == 1.30


@pytest.mark.parametrize("garbage", [0.0, -1.0, float("nan"), float("inf")])
def test_put_rejects_non_finite_or_non_positive_prices(
    repo: PriceSnapshotRepository, garbage: float
) -> None:
    with pytest.raises(ValueError, match="finite and > 0"):
        repo.put(_TOKEN, _TS, garbage)
    assert repo.get(_TOKEN, _TS) is None


def test_migration_applies_and_chain_stays_linear() -> None:
    engine = make_engine(":memory:")
    try:
        apply_migrations(engine)
        inspector = inspect(engine)
        assert "price_snapshots" in inspector.get_table_names()
        columns = {c["name"] for c in inspector.get_columns("price_snapshots")}
        assert columns == {"token", "ts", "price"}
    finally:
        engine.dispose()
    config = AlembicConfig()
    config.set_main_option("script_location", MIGRATIONS_PACKAGE)
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    # The chain head advanced to 0008 (Plan 0080's advice_ledger); it must still
    # be a single linear head, and 0007 must still chain onto 0006.
    assert heads == ["0008_advice_ledger"], f"expected a single head, got {heads}"
    revision = script.get_revision("0007_price_snapshots")
    assert revision.down_revision == "0006_defi_tx_cache", (
        "the two Plan 0035 migrations must form one linear chain"
    )
