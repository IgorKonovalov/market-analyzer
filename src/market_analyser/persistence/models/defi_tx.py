"""`defi_tx` ORM model — Plan 0035 phase 3 (ADR-0035/0036).

The immutable decoded-transaction cache: one row per (wallet, chain, hash),
carrying the full normalized `DecodedTx` as a JSON payload plus the three
columns the gap-fetch and deterministic-read queries need (`mined_at` as UTC
epoch seconds, `mined_at_block`, `in_block_index`).

The wallet is part of the key, not just an attribute: Zerion's decode is
**wallet-relative** (each transfer's `direction: in/out` is relative to the
scanned address), so the same on-chain transaction produces a *different*
decoded view per wallet. Keying by `(chain, hash)` alone would let a second
scanned wallet's view silently overwrite (or be masked by) the first's.

Rows are immutable — transaction history never changes — so the write path is
insert-or-ignore, never update (ADR-0036 "decoded events are likewise cached").

`Base` lives in `_base.py`; the class is re-exported from the package
`__init__.py` so `Base.metadata` sees the table at migration time.
"""

from __future__ import annotations

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from market_analyser.persistence.models._base import Base


class DefiTxRow(Base):
    """One cached decoded transaction. Composite PK `(wallet, chain, hash)` —
    insert-or-ignore immutability per ADR-0036."""

    __tablename__ = "defi_tx"

    wallet: Mapped[str] = mapped_column(String, primary_key=True)
    chain: Mapped[str] = mapped_column(String, primary_key=True)
    hash: Mapped[str] = mapped_column(String, primary_key=True)
    mined_at: Mapped[int] = mapped_column(Integer, nullable=False)
    mined_at_block: Mapped[int] = mapped_column(Integer, nullable=False)
    in_block_index: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
