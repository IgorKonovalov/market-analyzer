"""Wallet-scan job: runs discovery as an async job streaming SSE progress.

`run_wallet_scan` wraps the synchronous `DiscoveryService` in `asyncio.to_thread`
(the source's HTTP call is blocking, per ADR-0019) and publishes the
`defi.scan_*` lifecycle on the layer-neutral event bus (ADR-0017, ADR-0032:
`defi → events`, never `defi → api`):

    scan_started → (≥1 scan_progress, one per chain with positions) → scan_completed

On failure it publishes `defi.scan_failed` with a typed, closed reason and then
re-raises the original exception, so a fire-and-forget consumer learns of the
failure on the bus while the scan tool (phase 4) that awaits the call still sees
the precise typed error (e.g. a missing-key auth error) and can tell the agent
exactly what to fix. It never returns a zeroed result on failure — the failure
is loud (Plan 0032 "never silently zero").

Wallet addresses are masked (`mask_wallet`) before they reach any event payload.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import ValidationError

from market_analyser.data.errors import RateLimitedError, UpstreamDataError
from market_analyser.data.sources import WalletPositionsSource
from market_analyser.defi.discovery import DiscoveryService, mask_wallet
from market_analyser.defi.models import DefiPosition
from market_analyser.events import (
    DefiScanCompletedPayloadV1,
    DefiScanFailedPayloadV1,
    DefiScanProgressPayloadV1,
    DefiScanStartedPayloadV1,
    EventBus,
)

_logger = logging.getLogger(__name__)

# The chains a scan targets (ADR-0034), reported up front in `scan_started`.
TARGET_CHAINS: tuple[str, ...] = ("ethereum", "base", "arbitrum", "optimism")

ScanFailureReason = Literal["rate_limited", "upstream_unavailable", "malformed_response"]


@dataclass(frozen=True)
class WalletScanResult:
    """The successful scan outcome. `wallet` is the masked address; `chains` are
    the chains that returned positions; `total_usd_value` sums the positions."""

    wallet: str
    positions: list[DefiPosition]
    chains: list[str]
    total_usd_value: float


async def run_wallet_scan(
    *,
    source: WalletPositionsSource,
    address: str,
    event_bus: EventBus,
) -> WalletScanResult:
    """Run a wallet scan, streaming `defi.scan_*` progress; return the result.

    Raises the source's typed error (after publishing `defi.scan_failed`) on
    failure — never returns a zeroed result."""
    masked = mask_wallet(address)
    event_bus.publish(
        "defi.scan_started",
        DefiScanStartedPayloadV1(wallet=masked, chains=list(TARGET_CHAINS)),
    )
    try:
        result = await asyncio.to_thread(DiscoveryService(source).discover, address)
    except Exception as err:
        event_bus.publish(
            "defi.scan_failed",
            DefiScanFailedPayloadV1(
                wallet=masked,
                reason=_classify_failure(err),
                message=str(err),
            ),
        )
        raise
    for chain, count in _per_chain_counts(result.positions).items():
        event_bus.publish(
            "defi.scan_progress",
            DefiScanProgressPayloadV1(wallet=masked, chain=chain, position_count=count),
        )
    event_bus.publish(
        "defi.scan_completed",
        DefiScanCompletedPayloadV1(
            wallet=masked,
            chains=result.chains,
            position_count=len(result.positions),
        ),
    )
    return WalletScanResult(
        wallet=masked,
        positions=result.positions,
        chains=result.chains,
        total_usd_value=result.total_usd_value,
    )


def _per_chain_counts(positions: Sequence[DefiPosition]) -> dict[str, int]:
    """Positions-per-chain in first-seen order (deterministic, no set iteration)."""
    counts: dict[str, int] = {}
    for position in positions:
        counts[position.chain] = counts.get(position.chain, 0) + 1
    return counts


def _classify_failure(err: Exception) -> ScanFailureReason:
    """Map an exception onto the closed scan_failed reason set, source-agnostically.

    Rate limits → `rate_limited`; any other upstream error (including a missing /
    rejected key, an `UpstreamDataError` subclass) → `upstream_unavailable`; a
    malformed payload (`ValidationError` or an adapter `ValueError`) →
    `malformed_response`. The caller re-raises the original exception, so a
    precise type (e.g. an auth error) is not lost on the wire's coarser reason."""
    if isinstance(err, RateLimitedError):
        return "rate_limited"
    if isinstance(err, UpstreamDataError):
        return "upstream_unavailable"
    if isinstance(err, (ValidationError, ValueError)):
        return "malformed_response"
    return "upstream_unavailable"


__all__ = ["TARGET_CHAINS", "WalletScanResult", "run_wallet_scan"]
