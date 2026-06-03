"""DeFi wallet discovery service (ADR-0035, Plan 0032 phase 3).

The thin DeFi access facade over a `WalletPositionsSource` (ADR-0031): it calls
the source, then normalizes the returned positions into one set with a
deterministic per-chain breakdown and a total USD value.

Boundary discipline: the positions arrive already boundary-validated by the
source adapter + the `DefiPosition` model (finite/non-negative `usd_value`,
finite/positive token `amount`) — a malformed field was rejected *there*, loud,
before reaching this service. This service therefore never re-coerces or
zero-fills; it propagates any source error unchanged so the scan job can surface
it as `defi.scan_failed` rather than returning a silently-zeroed position set
(Plan 0032 "never silently zero"). It is source-agnostic — it knows the
`WalletPositionsSource` Protocol, not Zerion — so swapping the provider behind
the ADR-0031 registry needs no change here.
"""

from __future__ import annotations

from dataclasses import dataclass

from market_analyser.data.sources import WalletPositionsSource
from market_analyser.defi.models import DefiPosition

# How much of the address to keep when masking for any logged/surfaced form
# (ADR-0038): the `0x` + 4 nibbles head and the 4-nibble tail.
_MASK_HEAD = 6
_MASK_TAIL = 4


def mask_wallet(address: str) -> str:
    """Return the masked form of an address (`0x1234…abcd`) for events and logs.

    A string too short to mask meaningfully is returned unchanged — it carries no
    more information than the mask would."""
    if len(address) <= _MASK_HEAD + _MASK_TAIL:
        return address
    return f"{address[:_MASK_HEAD]}…{address[-_MASK_TAIL:]}"


@dataclass(frozen=True)
class DiscoveryResult:
    """A normalized wallet position set.

    `chains` is the chains that returned at least one position, in first-seen
    order (deterministic — no set iteration). `total_usd_value` is the sum of the
    positions' `usd_value`."""

    positions: list[DefiPosition]
    chains: list[str]
    total_usd_value: float


class DiscoveryService:
    """Composes a `WalletPositionsSource` into a normalized discovery result."""

    def __init__(self, source: WalletPositionsSource) -> None:
        self._source = source

    def discover(self, address: str) -> DiscoveryResult:
        """Fetch and normalize the wallet's positions.

        Propagates the source's typed errors unchanged (e.g. the shared
        `UpstreamDataError` taxonomy, or a `pydantic.ValidationError` / adapter
        `ValueError` on a malformed payload) — the scan job classifies them. Never
        swallows an error into an empty/zeroed result."""
        positions = list(self._source.fetch_positions(address))
        chains: list[str] = []
        total = 0.0
        for position in positions:
            if position.chain not in chains:
                chains.append(position.chain)
            total += position.usd_value
        return DiscoveryResult(positions=positions, chains=chains, total_usd_value=total)


__all__ = ["DiscoveryResult", "DiscoveryService", "mask_wallet"]
