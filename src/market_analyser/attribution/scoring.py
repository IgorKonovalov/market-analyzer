"""Pure path-dependent recommendation scoring — Plan 0080 phase 2 (ADR-0075).

`score_recommendation` takes one recorded advisory call and the bars around its
horizon, and decides how it resolved by *simulating the ticket it actually gave*:
a notional entry at the as-of bar's close, then a walk over the realized bars to
see whether the **stop or a target was hit first** (each bar's high/low). The
call's stop and targets are honoured, so "stand in long" with a stop that would
have been hit is scored as the loss it was, even if price ended the horizon
higher — the anecdote-killer ADR-0075 exists to enforce.

Honesty invariants baked in here:

* **No lookahead.** Only the first `horizon_bars` closed bars strictly after the
  as-of bar are ever read; a call whose horizon has not fully matured (fewer than
  `horizon_bars` *closed* bars available) returns `pending` and reads no price
  beyond the horizon. Closedness is decided by the seam-routed `now` — the only
  wall-clock input, and it never touches the return/R math.
* **Conservative intrabar tie-break.** When a single bar's range spans both the
  stop and a target, we assume the **stop hit first** — the anti-optimistic
  choice that understates rather than flatters (ADR-0075).
* **Determinism.** Given the same call + bars + `now`, the outcome is
  byte-identical (`scored_at` aside — the documented run-provenance exception).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from market_analyser.attribution.models import Outcome
from market_analyser.data.timeframes import timeframe_spec
from market_analyser.data.types import Bar
from market_analyser.persistence.advice_ledger_repository import AdviceLedgerEntry


def _pending(entry: AdviceLedgerEntry) -> Outcome:
    return Outcome(
        outcome_class="pending",
        realized_return=None,
        realized_r=None,
        directional_correct=None,
        prob_for_calibration=entry.forecast_prob,
        scored_at=None,
    )


def score_recommendation(
    entry: AdviceLedgerEntry,
    bars: Sequence[Bar],
    *,
    now: datetime,
) -> Outcome:
    """Score one directional recommendation against realized price.

    `bars` must include the as-of bar (whose close is the notional entry) and the
    bars after it; only the first `horizon_bars` *closed* bars after the as-of bar
    are read. Returns `pending` when the horizon has not matured or the as-of bar
    is absent from `bars` — never a partial peek.

    Raises `ValueError` for a flat call (nothing to score) or a malformed ticket
    (a stop on the wrong side of entry) — the phase-3 job's per-row containment
    surfaces those rather than letting a garbage R into the record.
    """

    if entry.direction == "flat":
        raise ValueError("cannot score a flat recommendation — it has no ticket to simulate")
    if entry.stop is None or not entry.targets:
        raise ValueError("a directional recommendation must carry a stop and at least one target")

    duration = timeframe_spec(entry.timeframe).bar_duration

    # The horizon window: the first `horizon_bars` bars strictly after the as-of
    # bar, and only those — nothing beyond `as_of + horizon` is read (no
    # lookahead). Slicing before any price read means a bar past the horizon is
    # never touched.
    after = [bar for bar in bars if bar.event_ts > entry.as_of_bar_ts]
    window = after[: entry.horizon_bars]
    if len(window) < entry.horizon_bars:
        return _pending(entry)
    # Maturity: every horizon bar must be closed relative to `now` (the same
    # closedness rule the live evaluator uses). A still-forming last bar → the
    # horizon has not matured → pending. Reads only `event_ts`, never a price.
    if not all(bar.event_ts + duration <= now for bar in window):
        return _pending(entry)

    as_of_bar = next((bar for bar in bars if bar.event_ts == entry.as_of_bar_ts), None)
    if as_of_bar is None:
        # The entry bar is missing from the supplied series — we cannot fix the
        # notional entry price, so we cannot score yet. Pending, not a guess.
        return _pending(entry)

    entry_price = as_of_bar.close
    stop = entry.stop
    is_long = entry.direction == "long"

    # The nearest target in the profit direction is the one a real ticket would
    # take first: the lowest target above entry for a long, the highest below for
    # a short.
    target = min(entry.targets) if is_long else max(entry.targets)

    risk = (entry_price - stop) if is_long else (stop - entry_price)
    if risk <= 0:
        raise ValueError(
            f"malformed ticket: stop {stop} is on the wrong side of entry {entry_price} "
            f"for a {entry.direction} call"
        )

    outcome_class: str
    exit_price: float
    for bar in window:
        if is_long:
            hit_stop = bar.low <= stop
            hit_target = bar.high >= target
        else:
            hit_stop = bar.high >= stop
            hit_target = bar.low <= target
        if hit_stop:
            # Conservative tie-break: a bar spanning both stop and target is
            # scored stop-first (ADR-0075).
            outcome_class = "stopped"
            exit_price = stop
            break
        if hit_target:
            outcome_class = "target_hit"
            exit_price = target
            break
    else:
        # Neither the stop nor a target fired within the horizon — mark to the
        # last horizon bar's close.
        outcome_class = "timeout"
        exit_price = window[-1].close

    realized_return = (
        (exit_price - entry_price) / entry_price
        if is_long
        else (entry_price - exit_price) / entry_price
    )
    realized_r = realized_return / (risk / entry_price)

    # The direction axis, deliberately independent of the ticket: did price end
    # the *full* horizon in the called direction? A call can be directionally
    # right (price up) yet score a `stopped` loss (whipsawed out first) — that
    # separation is the honest core.
    horizon_close = window[-1].close
    directional_correct = horizon_close > entry_price if is_long else horizon_close < entry_price

    return Outcome(
        outcome_class=outcome_class,  # type: ignore[arg-type]
        realized_return=realized_return,
        realized_r=realized_r,
        directional_correct=directional_correct,
        prob_for_calibration=entry.forecast_prob,
        scored_at=now,
    )


__all__ = ["score_recommendation"]
