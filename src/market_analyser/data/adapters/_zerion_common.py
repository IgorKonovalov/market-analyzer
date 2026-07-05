"""Shared Zerion plumbing for the adapter family (Plan 0035 phase 2 extraction).

`zerion.py` (wallet positions, Plan 0032) and `zerion_tx.py` (decoded tx
history, Plan 0035) hit the same API with the same HTTP Basic auth scheme and
the same target-chain mapping. Both live here so the two adapters can't drift.

Package-internal (leading underscore): reached only by the Zerion adapters,
never imported downstream — the public seams are the ADR-0031 Protocols.
"""

from __future__ import annotations

import base64

from market_analyser.defi.models import Chain

# Zerion's chain ids → our target-chain literal. An entity on any chain absent
# from this map is dropped (off-target). Zerion ids are kebab-case
# ("binance-smart-chain"); the four targets happen to match our literals.
CHAIN_IDS: dict[str, Chain] = {
    "ethereum": "ethereum",
    "base": "base",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
}


def basic_auth_header(key: str) -> str:
    """Zerion uses HTTP Basic auth with the API key as the username and an empty
    password. The key never reaches a log or the cache key (the client excludes
    headers from the cache key; nothing here logs the header)."""
    token = base64.b64encode(f"{key}:".encode()).decode("ascii")
    return f"Basic {token}"


__all__ = ["CHAIN_IDS", "basic_auth_header"]
