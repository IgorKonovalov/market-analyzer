"""BTC halving-cycle math — Plan 0055 phase 4 (ADR-0051's computed series family).

Pure, deterministic functions over constants and already-cached daily closes:
no wall-clock reads (callers pass `as_of`), no network, no RNG. Everything is
trailing — the moving averages read only the last N closes of the sequence the
caller supplies, and a `None` is returned (never a silently-shortened window)
when history is insufficient.

The halving dates are protocol facts. The *next* halving date is a **labeled
estimate** (block-height-driven in reality, unknowable as a date): we pin the
mean of the three observed inter-halving intervals as the estimated cycle
length and derive the estimate from the last known halving. Dates at or past
the estimate saturate (`days_to_next_halving_est` floors at 0, `halving_phase`
caps at 1.0) rather than going negative — a saturated reading is the signal to
update the constants after the real halving lands.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, timedelta

# Protocol facts: the four BTC halvings to date.
HALVING_DATES: tuple[date, ...] = (
    date(2012, 11, 28),
    date(2016, 7, 9),
    date(2020, 5, 11),
    date(2024, 4, 19),
)

# Mean of the observed inter-halving intervals (1319, 1402, 1439 days), rounded
# to a whole day. ESTIMATE — see the module docstring.
ESTIMATED_CYCLE_DAYS: int = 1387

# 2024-04-19 + 1387d = 2028-02-05. ESTIMATE — labeled as such everywhere it
# surfaces (the `_est` suffix is part of the contract, not decoration).
NEXT_HALVING_DATE_EST: date = HALVING_DATES[-1] + timedelta(days=ESTIMATED_CYCLE_DAYS)

# Mayer Multiple denominator: the 200-day simple moving average of daily closes.
MAYER_SMA_DAYS: int = 200

# The "200-week MA" measured in daily bars (200 * 7), per the plan's definition
# of `dist_200w_ma` as close / SMA1400(daily closes) - 1.
SMA_200W_DAYS: int = 1400


def _last_halving_at_or_before(as_of: date) -> date:
    if as_of < HALVING_DATES[0]:
        raise ValueError(
            f"as_of {as_of.isoformat()} predates the first BTC halving "
            f"({HALVING_DATES[0].isoformat()}) — no cycle is defined there",
        )
    return max(d for d in HALVING_DATES if d <= as_of)


def _next_halving_after(last: date) -> date:
    """The halving that closes the cycle `last` opened: the next known date, or
    the labeled estimate when `last` is the latest known halving."""
    later = [d for d in HALVING_DATES if d > last]
    return later[0] if later else NEXT_HALVING_DATE_EST


def days_since_halving(as_of: date) -> int:
    """Days elapsed since the last halving at or before `as_of` (0 on the day)."""
    return (as_of - _last_halving_at_or_before(as_of)).days


def days_to_next_halving_est(as_of: date) -> int:
    """Days from `as_of` to the next halving — exact between known halvings,
    an ESTIMATE in the current (open) cycle. Floors at 0 past the estimate."""
    return max(0, (_next_halving_after(_last_halving_at_or_before(as_of)) - as_of).days)


def halving_phase(as_of: date) -> float:
    """Fraction of the ~4y halving cycle elapsed at `as_of`, in [0.0, 1.0].

    Inside a completed cycle the denominator is that cycle's exact length;
    in the current cycle it is `ESTIMATED_CYCLE_DAYS` (estimate). Capped at
    1.0 once `as_of` reaches the estimated next halving."""
    last = _last_halving_at_or_before(as_of)
    total = (_next_halving_after(last) - last).days
    return min(1.0, (as_of - last).days / total)


def _trailing_sma(closes: Sequence[float], window: int) -> float | None:
    """Mean of the last `window` closes, or `None` when fewer exist — never a
    silently-shortened window (Plan 0055 phase 4)."""
    if len(closes) < window:
        return None
    tail = closes[len(closes) - window :]
    return sum(tail) / window


def mayer_multiple(closes: Sequence[float]) -> float | None:
    """Latest close divided by the 200-day SMA of daily closes, or `None` when
    fewer than 200 closes exist."""
    sma = _trailing_sma(closes, MAYER_SMA_DAYS)
    if sma is None:
        return None
    return closes[-1] / sma


def dist_200w_ma(closes: Sequence[float]) -> float | None:
    """Latest close relative to the 200-week MA (SMA over the last 1400 daily
    closes) minus 1 — e.g. +0.5 means 50% above it. `None` (not a number) when
    fewer than 1400 daily closes exist."""
    sma = _trailing_sma(closes, SMA_200W_DAYS)
    if sma is None:
        return None
    return closes[-1] / sma - 1.0


__all__ = [
    "ESTIMATED_CYCLE_DAYS",
    "HALVING_DATES",
    "MAYER_SMA_DAYS",
    "NEXT_HALVING_DATE_EST",
    "SMA_200W_DAYS",
    "days_since_halving",
    "days_to_next_halving_est",
    "dist_200w_ma",
    "halving_phase",
    "mayer_multiple",
]
