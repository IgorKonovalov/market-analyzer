"""Repository for the `defi_tx` immutable decoded-transaction cache
(Plan 0035 phase 3, ADR-0035/0036).

Transaction history never changes, so the write path is **insert-or-ignore**:
a `(wallet, chain, hash)` row that already exists is left untouched, never
updated — a re-ingest of the same history is a no-op, and an overlapping
gap-fetch (the adapter's inclusive `min_mined_at` boundary) dedupes for free.

Reads are wallet-scoped and deterministic: ordered by `(mined_at_block,
in_block_index, chain, hash)` ascending — primary-key-stable, never hash
iteration. The stored payload is the full `DecodedTx` JSON, so a cached read
reconstructs the exact models the adapter produced (byte-identical modulo
nothing; these are immutable).

Wallets are normalized to lowercase at this boundary — EVM addresses are
case-insensitive hex, and a checksummed vs lowercased spelling of the same
address must not fork the cache.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from market_analyser.defi.tx_models import DecodedTx
from market_analyser.persistence.models.defi_tx import DefiTxRow


class DefiTxRepository:
    """CRUD facade for the `defi_tx` table. Owns its own per-call sessions."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def insert_ignore(self, wallet: str, txs: Sequence[DecodedTx]) -> int:
        """Insert the transactions that aren't already cached, returning how
        many were newly inserted. Existing `(wallet, chain, hash)` rows are
        ignored (immutability: never update). One transaction per batch."""
        key = _normalize(wallet)
        inserted = 0
        with self._session_factory() as session:
            for tx in txs:
                existing = session.get(DefiTxRow, (key, tx.chain, tx.hash))
                if existing is not None:
                    continue
                session.add(
                    DefiTxRow(
                        wallet=key,
                        chain=tx.chain,
                        hash=tx.hash,
                        mined_at=int(tx.mined_at.timestamp()),
                        mined_at_block=tx.mined_at_block,
                        in_block_index=tx.in_block_index,
                        payload=tx.model_dump_json(),
                    )
                )
                inserted += 1
            session.commit()
        return inserted

    def list_for_wallet(self, wallet: str) -> list[DecodedTx]:
        """Every cached transaction for the wallet, ordered by
        `(mined_at_block, in_block_index, chain, hash)` ascending — the
        engine's deterministic replay order."""
        stmt = (
            select(DefiTxRow)
            .where(DefiTxRow.wallet == _normalize(wallet))
            .order_by(
                DefiTxRow.mined_at_block.asc(),
                DefiTxRow.in_block_index.asc(),
                DefiTxRow.chain.asc(),
                DefiTxRow.hash.asc(),
            )
        )
        with self._session_factory() as session:
            return [DecodedTx.model_validate_json(row.payload) for row in session.scalars(stmt)]

    def latest_mined_at(self, wallet: str) -> datetime | None:
        """The newest cached transaction's `mined_at` for the wallet (UTC), or
        `None` when nothing is cached — the gap-fetch lower bound."""
        stmt = select(func.max(DefiTxRow.mined_at)).where(DefiTxRow.wallet == _normalize(wallet))
        with self._session_factory() as session:
            newest = session.execute(stmt).scalar()
        return datetime.fromtimestamp(newest, tz=UTC) if newest is not None else None


def _normalize(wallet: str) -> str:
    return wallet.lower()


__all__ = ["DefiTxRepository"]
