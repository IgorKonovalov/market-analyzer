"""Cross-venue portfolio package (Plan 0041 / ADR-0042).

The one cross-domain consumer in the app: aggregates holdings from the Binance
read adapter (`data/adapters/binance_account.py`), the existing DeFi discovery
(ADR-0034), and a manual positions file into one boundary-validated holdings
model with average-cost basis (ADR-0036's method venue-wide), unrealized P&L,
and exposure by asset and venue.

Top-level deliberately: not `defi/` (DeFi-only by ADR-0035), not `analysis/`
(TradFi-indicator-only) — a cross-venue consumer belongs to neither charter.
Read-only and tools-only: it reports holdings, cost basis, P&L, and exposure
as **facts**; no operator skill owns it, and it emits no rebalance / exit /
buy / sell — that crossing is the advisor's alone (ADR-0029).
"""
