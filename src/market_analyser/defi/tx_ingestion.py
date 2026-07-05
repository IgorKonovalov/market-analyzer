"""Decoded-tx-history ingestion facade (Plan 0035 phase 3, ADR-0035/0036).

Composes a `TxHistorySource` (Zerion, phase 2) with the immutable `defi_tx`
SQLite cache (this phase) into the one read path the P&L engine consumes:

- **Cold cache** (wallet never ingested): pull the full history from the
  source, write it back, return it. Paid once per wallet.
- **Warm cache, `refresh=False`** (the default): a pure SQLite read — zero
  source fetches. This is what makes a P&L re-run affordable *and*
  deterministic: same cached inputs, same result (ADR-0036).
- **Warm cache, `refresh=True`**: fetch only the gap — transactions mined
  at/after the newest cached timestamp (the source's inclusive `min_mined_at`
  filter; the overlap row dedupes via insert-or-ignore) — write back, and
  return the full merged history. The explicit-refresh idiom mirrors
  `btc_cycle_snapshot` (Plan 0057): the default path never touches the
  network.

The facade depends on `data/` (the Protocol) and `persistence/` (the
repository), never `api/` (ADR-0032).
"""

from __future__ import annotations

from market_analyser.data.sources import TxHistorySource
from market_analyser.defi.tx_models import DecodedTx
from market_analyser.persistence.defi_tx_repository import DefiTxRepository


class TxHistoryService:
    """The cached decoded-history read path: cache-first, gap-fetch on refresh."""

    def __init__(self, *, source: TxHistorySource, repository: DefiTxRepository) -> None:
        self._source = source
        self._repository = repository

    def load_history(self, address: str, *, refresh: bool = False) -> list[DecodedTx]:
        """Return the wallet's decoded history in deterministic replay order
        (`(mined_at_block, in_block_index, chain, hash)` ascending).

        Cold cache → full source pull (write-back). Warm cache → cached set
        as-is unless `refresh=True`, which pulls only the gap. Source errors
        propagate typed (`ZerionAuthError` / `RateLimitedError` / …) — a
        failed pull never silently yields a partial or empty history."""
        newest_cached = self._repository.latest_mined_at(address)
        if newest_cached is None:
            fetched = self._source.fetch_transactions(address)
        elif refresh:
            fetched = self._source.fetch_transactions(address, min_mined_at=newest_cached)
        else:
            return self._repository.list_for_wallet(address)
        self._repository.insert_ignore(address, fetched)
        return self._repository.list_for_wallet(address)


__all__ = ["TxHistoryService"]
