"""Aerodrome-native fundamentals deep reader — RPC `eth_call` over Base (Plan 0107 phase 4).

The **deep tier** of the DeFi-fundamentals read (ADR-0102): DefiLlama gives the
broad, cross-protocol picture (TVL/volume/APR/mcap), but not Aerodrome's exact
**emission schedule** or its **veAERO / Voter** vote dynamics — the levers that set
AERO LP reward APR. This reader fills that gap for Aerodrome-on-Base by reading the
protocol's own contracts over the Base RPC we already hold (ADR-0038), reusing the
proven read-only `eth_call` transport from `lp_detail.py`.

**Best-effort and additive by charter.** Every read degrades to `None` + a `notes`
entry and never raises to the caller: an absent RPC URL, a reverting getter, a
throttle, or a shape-broken result leaves the DefiLlama-depth fields intact (Plan
0107 phase-4 done-when b). Nothing is coerced to zero. The tier is folded onto the
`DefiFundamentals` payload only for Aerodrome; every other token/protocol stays at
DefiLlama depth.

**Read-only by construction.** The only RPC method is `eth_call` (a staticcall
simulation) against public view getters — no key, no signing, no state change, no
funds moved. The contract addresses are **pinned from the Aerodrome contracts
repository** (verified against the on-chain AERO token address DefiLlama reports),
never model memory (Plan 0107 risk #2); each selector is `keccak256(sig)[:4]`,
self-checked in the tests.

Contracts read (Base):
- **Minter** `weekly()` (current epoch emission), `WEEKLY_DECAY()` (9900 bps → 1%),
  `epochCount()`, `tailEmissionRate()` → `EmissionsDetail`.
- **VotingEscrow** `supply()` (total AERO locked), `totalSupply()` (veAERO power).
- **Voter** `totalWeight()` (aggregate gauge vote weight) → `VeGaugeStats`.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from market_analyser.data._http import ResilientHttpClient
from market_analyser.data.adapters.lp_detail import (
    LpDetailConfigError,
    LpDetailError,
    rpc_eth_call,
    rpc_url_for,
)
from market_analyser.data.errors import UpstreamDataError
from market_analyser.defi.models import Chain, EmissionsDetail, VeGaugeStats

_SOURCE = "aerodrome-native"
_CHAIN: Chain = "base"

# Pinned Aerodrome deployment addresses on Base, from the official contracts
# repository (github.com/aerodrome-finance/contracts). The AERO token address in
# that same table matches the one DefiLlama reports for AERO
# (0x940181a94A35A4569E4529A3CDfB74e38FD98631), cross-checking the source (Plan
# 0107 risk #2 — verify against the docs, never model memory).
_MINTER = "0xeB018363F0a9Af8f91F06FEe6613a751b2A33FE5"
_VOTER = "0x16613524e02ad97eDfeF371bC883F2F5d6C480A5"
_VOTING_ESCROW = "0xeBf418Fe2512e7E6bd9b87a8F0f294aCDC67e6B4"

# Function selectors — keccak256(signature)[:4], self-checked in the tests
# (the only ground truth; a wrong selector reverts → honest-null, never a crash).
_SEL_WEEKLY = "0x26cfc17b"  # weekly() -> uint256
_SEL_WEEKLY_DECAY = "0xea64743d"  # WEEKLY_DECAY() -> uint256 (basis points, 9900)
_SEL_EPOCH_COUNT = "0x829965cc"  # epochCount() -> uint256
_SEL_TAIL_RATE = "0x9ba6f976"  # tailEmissionRate() -> uint256
_SEL_SUPPLY = "0x047fc9aa"  # supply() -> uint256 (total AERO locked in ve)
_SEL_TOTAL_SUPPLY = "0x18160ddd"  # totalSupply() -> uint256 (veAERO voting power)
_SEL_TOTAL_WEIGHT = "0x96c82e57"  # totalWeight() -> uint256 (Voter aggregate weight)

# AERO and veAERO are 18-decimal; the vote weight is denominated in the same units.
_WAD = 1e18

# WEEKLY_DECAY is basis points of the retained weekly emission (9900 = keep 99% →
# decay 1%). Percent decay = (10000 - decay_bps) / 100.
_BPS = 10_000.0

# Emissions/ve state changes on a weekly cadence, so a short TTL amortizes the ~7
# eth_calls without going stale.
_DEFAULT_TTL_SECONDS = 300.0

# A pause before each RPC request — the Base provider per-second-throttles bursts
# (the lp_detail smoke observation); injected as a no-op by tests.
_INTER_REQUEST_SECONDS = 0.25

_WORD_BYTES = 32


class AerodromeNativeReader:
    """Reads Aerodrome's emission + veAERO/Voter deep state over the Base RPC,
    best-effort. Reached only through the fundamentals composition (Plan 0107
    phase 5), never imported downstream."""

    def __init__(
        self,
        *,
        secrets_store: object,
        http_client: ResilientHttpClient | None = None,
        inter_request_seconds: float = _INTER_REQUEST_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        # `secrets_store` is a `SecretsStore` (typed loosely to avoid a persistence
        # import in the data layer); `rpc_url_for` reads the Base URL from it.
        self._secrets = secrets_store
        self._http = (
            http_client
            if http_client is not None
            else ResilientHttpClient(source_name=_SOURCE, cache_ttl_seconds=_DEFAULT_TTL_SECONDS)
        )
        self._inter_request_seconds = inter_request_seconds
        self._sleep = sleep

    def read_aerodrome(
        self, notes: list[str]
    ) -> tuple[EmissionsDetail | None, VeGaugeStats | None]:
        """Return `(emissions_detail, ve_gauge)` for Aerodrome-on-Base, each
        best-effort `None` + a `notes` entry on failure. Never raises: an absent RPC
        URL degrades the whole tier to DefiLlama depth (Plan 0107 phase-4 done-when
        b)."""
        try:
            rpc_url = rpc_url_for(self._secrets, _CHAIN)  # type: ignore[arg-type]
        except LpDetailConfigError:
            notes.append("aerodrome deep tier: Base RPC not configured — DefiLlama depth only")
            return None, None
        return (
            self._read_emissions(rpc_url, notes),
            self._read_ve_gauge(rpc_url, notes),
        )

    # -- Minter (emissions) -------------------------------------------------

    def _read_emissions(self, rpc_url: str, notes: list[str]) -> EmissionsDetail | None:
        weekly_raw = self._read_uint_or_none(rpc_url, _MINTER, _SEL_WEEKLY)
        if weekly_raw is None:
            notes.append("aerodrome emissions: Minter weekly() read failed — DefiLlama depth only")
            return None
        weekly_emission = weekly_raw / _WAD
        if weekly_emission <= 0:
            notes.append("aerodrome emissions: Minter weekly() returned 0 — honest-null")
            return None
        return EmissionsDetail(
            weekly_emission=weekly_emission,
            weekly_decay_pct=self._decay_pct(rpc_url),
            epoch=self._read_uint_or_none(rpc_url, _MINTER, _SEL_EPOCH_COUNT),
            tail_emission_rate=self._as_float(
                self._read_uint_or_none(rpc_url, _MINTER, _SEL_TAIL_RATE)
            ),
        )

    def _decay_pct(self, rpc_url: str) -> float | None:
        decay_bps = self._read_uint_or_none(rpc_url, _MINTER, _SEL_WEEKLY_DECAY)
        if decay_bps is None or decay_bps > _BPS:
            return None
        return (_BPS - decay_bps) / 100.0

    # -- VotingEscrow + Voter (ve / gauge) ----------------------------------

    def _read_ve_gauge(self, rpc_url: str, notes: list[str]) -> VeGaugeStats | None:
        locked = self._read_scaled_or_none(rpc_url, _VOTING_ESCROW, _SEL_SUPPLY)
        power = self._read_scaled_or_none(rpc_url, _VOTING_ESCROW, _SEL_TOTAL_SUPPLY)
        weight = self._read_scaled_or_none(rpc_url, _VOTER, _SEL_TOTAL_WEIGHT)
        if locked is None and power is None and weight is None:
            notes.append(
                "aerodrome ve/gauge: VotingEscrow/Voter reads failed — DefiLlama depth only"
            )
            return None
        return VeGaugeStats(
            ve_total_locked=locked,
            ve_total_voting_power=power,
            total_vote_weight=weight,
        )

    # -- transport ----------------------------------------------------------

    def _read_scaled_or_none(self, rpc_url: str, to: str, selector: str) -> float | None:
        raw = self._read_uint_or_none(rpc_url, to, selector)
        return None if raw is None else raw / _WAD

    def _read_uint_or_none(self, rpc_url: str, to: str, selector: str) -> int | None:
        """One paced `eth_call` decoded as a uint256, or `None` on any failure
        (revert, throttle, transport, short result) — the best-effort contract."""
        self._sleep(self._inter_request_seconds)
        try:
            data = rpc_eth_call(self._http, rpc_url, to, selector)
        except (LpDetailError, UpstreamDataError):
            return None
        if len(data) < _WORD_BYTES:
            return None
        return int.from_bytes(data[:_WORD_BYTES], "big", signed=False)

    @staticmethod
    def _as_float(value: int | None) -> float | None:
        return None if value is None else float(value)


__all__ = ["AerodromeNativeReader"]
