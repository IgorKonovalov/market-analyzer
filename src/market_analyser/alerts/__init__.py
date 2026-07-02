"""Watchlist alerting (Plan 0060, ADR-0055).

Persisted watch definitions evaluated by an in-sidecar asyncio scheduler,
firing edge-triggered, condition-only `alert.triggered v1` events. This
package holds the boundary types (`types`), the pure evaluation core
(`evaluate`, phase 2), and the lifespan scheduler (`scheduler`, phase 3).
"""
