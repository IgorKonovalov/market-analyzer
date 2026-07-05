"""Plan 0035 phase 3 done-when: the immutable decoded-tx cache + gap-fetch.

Pinned claims:
(a) a fresh scan persists N decoded transactions;
(b) a second scan with no new on-chain activity issues ZERO source fetches and
    returns the cached set byte-identical (immutable rows, nothing to drift);
(c) a refresh after one new transaction fetches only the gap (min_mined_at ==
    the newest cached timestamp) and merges it in;
(d) re-inserting an existing (wallet, chain, hash) is an idempotent no-op;
(e) the migration applies cleanly on a temp DB and the chain has exactly one
    head (linear — the Plan 0044 serialization rule depends on it).
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.orm import Session, sessionmaker

from market_analyser.defi.tx_ingestion import TxHistoryService
from market_analyser.defi.tx_models import DecodedTx, TxTransfer
from market_analyser.persistence.defi_tx_repository import DefiTxRepository
from market_analyser.persistence.engine import (
    MIGRATIONS_PACKAGE,
    apply_migrations,
    make_engine,
    make_session_factory,
)

_WALLET = "0x2222222222222222222222222222222222222222"


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield make_session_factory(engine)
    engine.dispose()


def _tx(
    tx_hash: str,
    block: int,
    *,
    index: int = 0,
    mined_at: datetime | None = None,
    chain: str = "base",
) -> DecodedTx:
    return DecodedTx.model_validate(
        {
            "chain": chain,
            "hash": tx_hash,
            "operation_type": "deposit",
            "mined_at": mined_at if mined_at is not None else datetime(2025, 9, 1, tzinfo=UTC),
            "mined_at_block": block,
            "in_block_index": index,
            "status": "confirmed",
            "transfers": [
                TxTransfer(direction="out", symbol="USDC", address="0xa0b8", amount=100.0)
            ],
        }
    )


class _RecordingSource:
    """A TxHistorySource fake that records every fetch call."""

    def __init__(self, pages: list[list[DecodedTx]]) -> None:
        self._pages = pages
        self.calls: list[tuple[str, datetime | None]] = []

    def fetch_transactions(
        self,
        address: str,
        *,
        min_mined_at: datetime | None = None,
    ) -> list[DecodedTx]:
        self.calls.append((address, min_mined_at))
        return self._pages.pop(0) if self._pages else []


def test_insert_ignore_persists_and_lists_in_replay_order(
    session_factory: sessionmaker[Session],
) -> None:
    repo = DefiTxRepository(session_factory)
    txs = [
        _tx("0xb", 200, index=1, mined_at=datetime(2025, 9, 2, tzinfo=UTC)),
        _tx("0xa", 200, index=0, mined_at=datetime(2025, 9, 2, tzinfo=UTC)),
        _tx("0xc", 100, mined_at=datetime(2025, 9, 1, tzinfo=UTC)),
    ]
    assert repo.insert_ignore(_WALLET, txs) == 3
    listed = repo.list_for_wallet(_WALLET)
    assert [t.hash for t in listed] == ["0xc", "0xa", "0xb"]
    # Full-fidelity round-trip: the cached models equal the ingested ones.
    assert sorted(listed, key=lambda t: t.hash) == sorted(txs, key=lambda t: t.hash)


def test_reinsert_of_existing_chain_hash_is_idempotent(
    session_factory: sessionmaker[Session],
) -> None:
    repo = DefiTxRepository(session_factory)
    tx = _tx("0xa", 100)
    assert repo.insert_ignore(_WALLET, [tx]) == 1
    assert repo.insert_ignore(_WALLET, [tx]) == 0
    assert len(repo.list_for_wallet(_WALLET)) == 1


def test_wallet_spelling_is_case_normalized(session_factory: sessionmaker[Session]) -> None:
    repo = DefiTxRepository(session_factory)
    repo.insert_ignore(_WALLET.upper().replace("0X", "0x"), [_tx("0xa", 100)])
    assert len(repo.list_for_wallet(_WALLET)) == 1


def test_latest_mined_at_returns_newest_or_none(session_factory: sessionmaker[Session]) -> None:
    repo = DefiTxRepository(session_factory)
    assert repo.latest_mined_at(_WALLET) is None
    repo.insert_ignore(
        _WALLET,
        [
            _tx("0xa", 100, mined_at=datetime(2025, 9, 1, tzinfo=UTC)),
            _tx("0xb", 200, mined_at=datetime(2025, 9, 3, 12, 30, tzinfo=UTC)),
        ],
    )
    assert repo.latest_mined_at(_WALLET) == datetime(2025, 9, 3, 12, 30, tzinfo=UTC)


def test_fresh_scan_pulls_full_history_and_persists(
    session_factory: sessionmaker[Session],
) -> None:
    repo = DefiTxRepository(session_factory)
    source = _RecordingSource([[_tx("0xa", 100), _tx("0xb", 200)]])
    service = TxHistoryService(source=source, repository=repo)
    history = service.load_history(_WALLET)
    assert [t.hash for t in history] == ["0xa", "0xb"]
    assert source.calls == [(_WALLET, None)], "cold cache = one full pull, no min_mined_at"
    assert len(repo.list_for_wallet(_WALLET)) == 2


def test_second_scan_with_no_new_activity_issues_zero_fetches(
    session_factory: sessionmaker[Session],
) -> None:
    repo = DefiTxRepository(session_factory)
    source = _RecordingSource([[_tx("0xa", 100), _tx("0xb", 200)]])
    service = TxHistoryService(source=source, repository=repo)
    first = service.load_history(_WALLET)
    second = service.load_history(_WALLET)
    assert source.calls == [(_WALLET, None)], "the warm-cache read must not touch the source"
    # Byte-identical: immutable rows reconstruct the exact same models.
    assert [t.model_dump_json() for t in second] == [t.model_dump_json() for t in first]


def test_refresh_after_one_new_tx_fetches_only_the_gap(
    session_factory: sessionmaker[Session],
) -> None:
    repo = DefiTxRepository(session_factory)
    newest_cached = datetime(2025, 9, 2, tzinfo=UTC)
    new_tx = _tx("0xnew", 300, mined_at=datetime(2025, 9, 5, tzinfo=UTC))
    source = _RecordingSource(
        [
            [
                _tx("0xa", 100, mined_at=datetime(2025, 9, 1, tzinfo=UTC)),
                _tx("0xb", 200, mined_at=newest_cached),
            ],
            [new_tx],
        ]
    )
    service = TxHistoryService(source=source, repository=repo)
    service.load_history(_WALLET)
    merged = service.load_history(_WALLET, refresh=True)
    assert source.calls == [(_WALLET, None), (_WALLET, newest_cached)], (
        "the refresh pull must be bounded at the newest cached timestamp"
    )
    assert [t.hash for t in merged] == ["0xa", "0xb", "0xnew"]


def test_refresh_overlap_row_dedupes_via_insert_ignore(
    session_factory: sessionmaker[Session],
) -> None:
    """The source's min_mined_at bound is inclusive, so the newest cached tx
    comes back in the gap page — and must not duplicate."""
    repo = DefiTxRepository(session_factory)
    boundary_tx = _tx("0xb", 200, mined_at=datetime(2025, 9, 2, tzinfo=UTC))
    source = _RecordingSource([[boundary_tx], [boundary_tx]])
    service = TxHistoryService(source=source, repository=repo)
    service.load_history(_WALLET)
    merged = service.load_history(_WALLET, refresh=True)
    assert [t.hash for t in merged] == ["0xb"]


def test_migration_applies_cleanly_and_chain_has_one_head() -> None:
    engine = make_engine(":memory:")
    try:
        apply_migrations(engine)
        inspector = inspect(engine)
        assert "defi_tx" in inspector.get_table_names()
        columns = {c["name"] for c in inspector.get_columns("defi_tx")}
        assert columns == {
            "wallet",
            "chain",
            "hash",
            "mined_at",
            "mined_at_block",
            "in_block_index",
            "payload",
        }
    finally:
        engine.dispose()
    config = AlembicConfig()
    config.set_main_option("script_location", MIGRATIONS_PACKAGE)
    heads = ScriptDirectory.from_config(config).get_heads()
    assert len(heads) == 1, f"the migration chain must stay linear, got heads: {heads}"
