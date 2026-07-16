"""DeFi position-watch boundary types + the pure dwell reducer (Plan 0099
phase 1, ADR-0093).

A position watch names a wallet's concentrated-liquidity LP (by chain, pool
address, and optionally the position NFT token id) and a dwell threshold. The
in-sidecar monitor (phase 2) re-reads the position's live `in_range` on an
interval and drives `evaluate_position_dwell` — the one piece of trigger
logic, kept pure and clock-free so the fire/reset semantics are exhaustively
testable without a scheduler.

Dwell semantics (ADR-0093, "dwell-qualified edge"):

- an in-range observation **resets and re-arms** the watch;
- the first out-of-range observation only starts the dwell clock
  (`out_since`) — it never fires. This is also the restart rule: if the
  sidecar was down while the position drifted out, the first post-restart
  observation starts a fresh dwell (conservative — may delay one dwell,
  never fires early);
- the watch fires **exactly once** when observations have been continuously
  out of range for at least the dwell threshold; further out-of-range
  observations after the fire do not re-fire (`fired` latches until
  re-entry).

Alert payloads are condition facts only — pool, tick bounds, dwell hours,
forgone-fee context — never a directive (ADR-0029 boundary); the model is
`extra="forbid"` so an advice-shaped field cannot ride along. This module
stays import-light and pure: persistence lives in
`persistence/repositories/defi_position_watches.py`, the clock in the
phase-2 monitor.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from market_analyser.defi.models import Chain, PositionToken

# An EVM address: `0x` + 40 hex nibbles — the same shape `defi/scan_job.py`
# pins at the scan boundary (kept local so this types module does not import
# the discovery/event machinery scan_job pulls in). ENS is out of scope.
EVM_ADDRESS_PATTERN = r"^0x[0-9a-fA-F]{40}$"

# Where a watch came from: pinned in config at startup, or agent-created via
# the MCP tools (phase 2). Both live behind the one repository.
WatchSource = Literal["config", "agent"]

DEFAULT_DWELL_HOURS = 6.0
DEFAULT_INTERVAL_SECONDS = 900


class DwellState(BaseModel):
    """The reducer's persisted memory for one watch.

    `out_since` is the first observation time of the current out-of-range
    excursion (`None` while in range / never observed out); `fired` latches
    once the excursion has alerted, so the fire happens exactly once per
    excursion. Persisting both is what makes the dwell survive a sidecar
    restart (done-when (e)).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    out_since: datetime | None = None
    fired: bool = False

    @model_validator(mode="after")
    def _fired_requires_out_since(self) -> DwellState:
        if self.fired and self.out_since is None:
            raise ValueError("fired=True requires out_since (a fire happens mid-excursion)")
        return self


def evaluate_position_dwell(
    prev_state: DwellState,
    *,
    in_range: bool,
    now: datetime,
    dwell: timedelta,
) -> tuple[DwellState, bool]:
    """Advance one watch's dwell state by one live observation.

    Returns `(new_state, fired)` where `fired` is True exactly when this
    observation crosses the dwell threshold for the first time in the current
    excursion. Pure: no clock reads, no I/O — `now` is the observation time
    the caller (monitor / test) supplies. A failed RPC read must NOT reach
    this function; the caller keeps the prior state untouched (ADR-0093).

    `now` earlier than the recorded `out_since` (a clock step backwards
    across a restart) does not fire and leaves the state unchanged — the
    reducer never fires on a negative elapsed time.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware (UTC)")
    if dwell <= timedelta(0):
        raise ValueError(f"dwell must be positive, got {dwell!r}")

    if in_range:
        # Reset + re-arm; a fired excursion ending is not an event (v1).
        return DwellState(), False
    if prev_state.out_since is None:
        # First out-of-range observation — start the dwell clock, never fire.
        return DwellState(out_since=now, fired=False), False
    if prev_state.fired:
        return prev_state, False
    if now - prev_state.out_since >= dwell:
        return DwellState(out_since=prev_state.out_since, fired=True), True
    return prev_state, False


class DefiPositionWatch(BaseModel):
    """A validated position-watch definition — the domain shape the repository
    returns and the monitor ticks. Boundary-validated; trusted downstream."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    wallet: str = Field(pattern=EVM_ADDRESS_PATTERN)
    chain: Chain
    pool_address: str = Field(pattern=EVM_ADDRESS_PATTERN)
    # None = match any CL LP this wallet holds in the pool.
    nft_token_id: Annotated[int, Field(ge=0)] | None
    dwell_hours: float = Field(default=DEFAULT_DWELL_HOURS, gt=0)
    interval_seconds: Annotated[int, Field(gt=0)] = DEFAULT_INTERVAL_SECONDS
    enabled: bool = True
    source: WatchSource
    created_at: datetime
    dwell_state: DwellState = DwellState()

    @field_validator("dwell_hours")
    @classmethod
    def _dwell_hours_must_be_finite(cls, v: float) -> float:
        # `gt=0` already rejects NaN and negatives; this also rejects +Inf.
        if not math.isfinite(v):
            raise ValueError("dwell_hours must be finite (no NaN/Inf)")
        return v

    @property
    def dwell(self) -> timedelta:
        return timedelta(hours=self.dwell_hours)


class DefiPositionAlert(BaseModel):
    """One fired out-of-range alert — the persisted history row and the core
    of the `defi.position_alert v1` payload (phase 2).

    Condition facts only (ADR-0029/0093): where the range is, where the tick
    is, how long the position has been idle, and the forgone-fee context.
    `extra="forbid"` structurally bars a directive/advice/size field from
    ever riding along. `fired_at` is run provenance (wall-clock at fire),
    outside any determinism guarantee.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    watch_id: int
    wallet: str = Field(pattern=EVM_ADDRESS_PATTERN)
    chain: Chain
    pool_address: str = Field(pattern=EVM_ADDRESS_PATTERN)
    nft_token_id: Annotated[int, Field(ge=0)] | None
    fired_at: datetime
    out_since: datetime
    hours_out: float = Field(ge=0)
    tick_lower: int
    tick_upper: int
    current_tick: int
    in_range: bool  # False by construction at fire; pinned below
    uncollected_fees: list[PositionToken] | None

    @field_validator("hours_out")
    @classmethod
    def _hours_out_must_be_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("hours_out must be finite (no NaN/Inf)")
        return v

    @model_validator(mode="after")
    def _fire_time_facts_consistent(self) -> DefiPositionAlert:
        if self.in_range:
            raise ValueError("a position alert fires out of range; in_range must be False")
        if self.tick_lower >= self.tick_upper:
            raise ValueError("tick_lower must be strictly less than tick_upper")
        return self


def validate_evm_address(value: str, *, field: str) -> str:
    """Boundary check for a raw `0x…` address; returns the value unchanged.

    Shared by the repository create path and the phase-2 tool boundary so a
    non-address is refused with a uniform message before any write.
    """
    if not re.fullmatch(EVM_ADDRESS_PATTERN, value):
        raise ValueError(f"{field} must be an EVM address (0x + 40 hex chars)")
    return value


__all__ = [
    "DEFAULT_DWELL_HOURS",
    "DEFAULT_INTERVAL_SECONDS",
    "EVM_ADDRESS_PATTERN",
    "DefiPositionAlert",
    "DefiPositionWatch",
    "DwellState",
    "WatchSource",
    "evaluate_position_dwell",
    "validate_evm_address",
]
