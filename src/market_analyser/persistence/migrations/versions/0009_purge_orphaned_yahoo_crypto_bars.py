"""Purge orphaned Yahoo crypto -USD bars — Plan 0081 phase 2 (ADR-0076).

Adopting Coinbase changes the *source* of an existing symbol string: `BTC-USD`,
`ETH-USD`, and every crypto `-USD` pair Coinbase lists were served by Yahoo and
now route to Coinbase (deep, USD-native). Their previously-cached Yahoo rows are
a different series now (Coinbase USD prices != Yahoo synthetic composites), so
they are orphaned.

The provenance-scoped cache read (Plan 0081 phase 2: `BarRepository.get_bars`'s
`source` filter, passed the routed source by the provider) already makes those
rows **inert** — a Coinbase-routed read never returns Yahoo bars. So this
migration is a **space-reclaim + confusion-prevention** measure, not a
correctness prerequisite: it deletes the now-unreachable rows rather than leaving
them to sit forever behind the source filter.

**Scope** is `source = 'yahoo' AND symbol LIKE '%-USD'`. Yahoo's crypto namespace
is exactly the `X-USD` composite form; equities/indices/FX never carry a `-USD`
suffix (Yahoo FX is `EURUSD=X`, indices are `^GSPC`, equities are bare tickers),
so this cannot touch a non-crypto symbol. It deliberately does **not** consult
the live Coinbase product set (a migration has no network): the rare crypto
`-USD` pair Coinbase does not list still routes to Yahoo and would have its rows
purged here, but that is harmless — the next request re-fetches them loudly from
Yahoo. Binance-sourced (`BTCUSDT`) and already-Coinbase-sourced rows are never
matched (the `source = 'yahoo'` clause).

One-way by design (ADR-0076 negatives): the deleted rows are re-derivable from
the live source on demand, so `downgrade` is a documented no-op — there is
nothing schema-shaped to reverse and the data is reconstructable, not lost.

Revision ID: 0009_purge_orphaned_yahoo_crypto_bars
Revises: 0008_advice_ledger
Create Date: 2026-07-11
"""

from __future__ import annotations

from alembic import op

revision: str = "0009_purge_orphaned_yahoo_crypto_bars"
down_revision: str | None = "0008_advice_ledger"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # One-way data purge of the now-orphaned Yahoo crypto -USD rows (see the
    # module docstring for the precise, network-free scope and why it is safe).
    op.execute("DELETE FROM bars WHERE source = 'yahoo' AND symbol LIKE '%-USD'")


def downgrade() -> None:
    # No-op: this is a one-way data purge, not a schema change. The deleted rows
    # carry no schema to restore and are re-fetchable from the live source on the
    # next request, so there is nothing to reverse (ADR-0076).
    pass
