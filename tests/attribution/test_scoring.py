"""Plan 0080 phase 2: the pure path-dependent scoring engine (ADR-0075).

Each branch of `score_recommendation` is pinned on a hand-built bar fixture:

- a target hit before the stop → `target_hit` with the right R;
- **stopped out intraday but ending the horizon higher → `stopped` (a loss)** —
  the anecdote-killer, with `directional_correct=True` proving the two axes are
  independent;
- a single bar spanning both stop and target → `stopped` (conservative
  stop-first tie-break);
- a horizon with no touch → `timeout` with the marked-to-close return;
- an immature horizon → `pending`, and a spy fixture proving no bar beyond the
  horizon is ever price-read (no lookahead);
- re-scoring a matured call is byte-identical;
- short-side symmetry, flat calls, and malformed tickets.

Entry is fixed at the as-of bar's close (100), stop at 90, target at 110 — so a
target is +1R and a stop is -1R by construction.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from market_analyser.attribution.scoring import score_recommendation
from market_analyser.data.types import Bar
from market_analyser.persistence.advice_ledger_repository import AdviceLedgerEntry

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_DAY = timedelta(days=1)
_HORIZON = 3
# Well past the last horizon bar (day 3), so every horizon bar is closed.
_NOW = _T0 + 10 * _DAY


def _day(n: int) -> datetime:
    return _T0 + n * _DAY


def _bar(n: int, *, high: float, low: float, close: float) -> Bar:
    # `open` is never read by the scorer; set it to `close` so it always sits
    # inside [low, high] (Bar's invariant) whatever the range.
    return Bar(
        symbol="DOGE-USD",
        timeframe="1d",
        event_ts=_day(n),
        open=close,
        high=high,
        low=low,
        close=close,
        volume=1_000.0,
        source="test",
    )


def _entry(
    *,
    direction: str = "long",
    stop: float | None = 90.0,
    targets: list[float] | None = None,
    forecast_prob: float | None = 0.62,
    horizon_bars: int = _HORIZON,
) -> AdviceLedgerEntry:
    return AdviceLedgerEntry(
        symbol="DOGE-USD",
        timeframe="1d",
        strategy_id="rsi",
        as_of_bar_ts=_T0,
        horizon_bars=horizon_bars,
        direction=direction,  # type: ignore[arg-type]
        entry_zone=(99.0, 101.0),
        stop=stop,
        targets=targets if targets is not None else [110.0],
        conviction=0.6,
        forecast_prob=forecast_prob,
        artifact_path=None,
        created_at=_T0,
    )


# The as-of bar: its close (100) is the notional entry.
_AS_OF_BAR = _bar(0, high=100.0, low=100.0, close=100.0)


def test_target_hit_before_stop_scores_target_hit_at_plus_one_r() -> None:
    bars = [
        _AS_OF_BAR,
        _bar(1, high=111.0, low=99.0, close=105.0),  # touches target (110), not stop
        _bar(2, high=106.0, low=101.0, close=104.0),
        _bar(3, high=109.0, low=103.0, close=108.0),
    ]
    outcome = score_recommendation(_entry(), bars, now=_NOW)
    assert outcome.outcome_class == "target_hit"
    assert outcome.realized_return == pytest.approx(0.10)  # (110 - 100) / 100
    assert outcome.realized_r == pytest.approx(1.0)  # reward 10 / risk 10
    assert outcome.directional_correct is True
    assert outcome.prob_for_calibration == 0.62
    assert outcome.scored_at == _NOW


def test_stopped_intraday_but_ending_higher_is_a_loss() -> None:
    """The anecdote-killer: price dips through the stop early, then recovers to
    end the horizon well above entry. The ticket was stopped out — a loss — even
    though the directional call was right."""
    bars = [
        _AS_OF_BAR,
        _bar(1, high=103.0, low=89.0, close=95.0),  # low pierces the stop (90)
        _bar(2, high=107.0, low=96.0, close=105.0),
        _bar(3, high=114.0, low=104.0, close=112.0),  # ends far above entry
    ]
    outcome = score_recommendation(_entry(), bars, now=_NOW)
    assert outcome.outcome_class == "stopped"  # a LOSS, not a win
    assert outcome.realized_return == pytest.approx(-0.10)  # (90 - 100) / 100
    assert outcome.realized_r == pytest.approx(-1.0)
    # The direction axis is independent: the call's direction was right...
    assert outcome.directional_correct is True
    # ...but the ticket still lost. That separation is the whole point.


def test_bar_spanning_both_stop_and_target_scores_stopped() -> None:
    bars = [
        _AS_OF_BAR,
        _bar(1, high=112.0, low=88.0, close=100.0),  # spans stop (90) AND target (110)
        _bar(2, high=106.0, low=101.0, close=104.0),
        _bar(3, high=109.0, low=103.0, close=108.0),
    ]
    outcome = score_recommendation(_entry(), bars, now=_NOW)
    assert outcome.outcome_class == "stopped"  # conservative stop-first tie-break
    assert outcome.realized_r == pytest.approx(-1.0)


def test_no_touch_scores_timeout_marked_to_close() -> None:
    bars = [
        _AS_OF_BAR,
        _bar(1, high=105.0, low=97.0, close=103.0),
        _bar(2, high=108.0, low=99.0, close=102.0),
        _bar(3, high=109.0, low=98.0, close=104.0),  # neither stop nor target fired
    ]
    outcome = score_recommendation(_entry(), bars, now=_NOW)
    assert outcome.outcome_class == "timeout"
    assert outcome.realized_return == pytest.approx(0.04)  # (104 - 100) / 100
    assert outcome.realized_r == pytest.approx(0.4)
    assert outcome.directional_correct is True


def test_immature_horizon_too_few_bars_is_pending() -> None:
    bars = [
        _AS_OF_BAR,
        _bar(1, high=111.0, low=99.0, close=105.0),  # would be a target_hit if mature
        _bar(2, high=106.0, low=101.0, close=104.0),
    ]  # only 2 bars after as-of; horizon is 3
    outcome = score_recommendation(_entry(), bars, now=_NOW)
    assert outcome.outcome_class == "pending"
    assert outcome.realized_return is None
    assert outcome.realized_r is None
    assert outcome.directional_correct is None
    assert outcome.scored_at is None


def test_forming_last_horizon_bar_is_pending() -> None:
    """Three bars exist, but `now` is before the third has closed — the horizon
    has not matured, so pending (no scoring on a still-forming bar)."""
    bars = [
        _AS_OF_BAR,
        _bar(1, high=105.0, low=99.0, close=103.0),
        _bar(2, high=106.0, low=101.0, close=104.0),
        _bar(3, high=111.0, low=103.0, close=108.0),
    ]
    # `now` sits inside day 3's bar → day 3 is still forming.
    now = _day(3) + timedelta(hours=6)
    outcome = score_recommendation(_entry(), bars, now=now)
    assert outcome.outcome_class == "pending"


def test_no_bar_beyond_the_horizon_is_price_read() -> None:
    """Lookahead guard: a bar just past the horizon carries a range that would
    force a target_hit if it were read. The outcome must ignore it (timeout),
    proving nothing beyond `as_of + horizon` is priced."""
    bars = [
        _AS_OF_BAR,
        _bar(1, high=105.0, low=97.0, close=103.0),
        _bar(2, high=108.0, low=99.0, close=102.0),
        _bar(3, high=109.0, low=98.0, close=104.0),  # horizon ends here → timeout
        _bar(4, high=999.0, low=1.0, close=500.0),  # a trap: never allowed to be read
    ]
    outcome = score_recommendation(_entry(), bars, now=_NOW)
    assert outcome.outcome_class == "timeout"  # the trap bar was not read
    assert outcome.realized_return == pytest.approx(0.04)


def test_rescoring_a_matured_call_is_byte_identical() -> None:
    bars = [
        _AS_OF_BAR,
        _bar(1, high=111.0, low=99.0, close=105.0),
        _bar(2, high=106.0, low=101.0, close=104.0),
        _bar(3, high=109.0, low=103.0, close=108.0),
    ]
    entry = _entry()
    first = score_recommendation(entry, bars, now=_NOW)
    second = score_recommendation(entry, bars, now=_NOW)
    assert first.model_dump() == second.model_dump()


class TestShortSide:
    def test_short_target_hit(self) -> None:
        entry = _entry(direction="short", stop=110.0, targets=[90.0])
        bars = [
            _AS_OF_BAR,
            _bar(1, high=101.0, low=89.0, close=95.0),  # low pierces the short target (90)
            _bar(2, high=99.0, low=91.0, close=94.0),
            _bar(3, high=97.0, low=88.0, close=92.0),
        ]
        outcome = score_recommendation(entry, bars, now=_NOW)
        assert outcome.outcome_class == "target_hit"
        assert outcome.realized_return == pytest.approx(0.10)  # (100 - 90) / 100, a profit
        assert outcome.realized_r == pytest.approx(1.0)
        assert outcome.directional_correct is True  # ended below entry

    def test_short_stopped(self) -> None:
        entry = _entry(direction="short", stop=110.0, targets=[90.0])
        bars = [
            _AS_OF_BAR,
            _bar(1, high=111.0, low=99.0, close=108.0),  # high pierces the short stop (110)
            _bar(2, high=105.0, low=95.0, close=98.0),
            _bar(3, high=96.0, low=90.0, close=95.0),
        ]
        outcome = score_recommendation(entry, bars, now=_NOW)
        assert outcome.outcome_class == "stopped"
        assert outcome.realized_r == pytest.approx(-1.0)


class TestGuards:
    def test_flat_call_raises(self) -> None:
        flat = _entry(direction="flat", stop=None, targets=[])
        with pytest.raises(ValueError, match="flat recommendation"):
            score_recommendation(flat, [_AS_OF_BAR], now=_NOW)

    def test_malformed_stop_on_wrong_side_raises(self) -> None:
        bad = _entry(direction="long", stop=110.0)  # stop above entry for a long
        bars = [
            _AS_OF_BAR,
            _bar(1, high=105.0, low=97.0, close=103.0),
            _bar(2, high=108.0, low=99.0, close=102.0),
            _bar(3, high=109.0, low=98.0, close=104.0),
        ]
        with pytest.raises(ValueError, match="wrong side of entry"):
            score_recommendation(bad, bars, now=_NOW)

    def test_missing_as_of_bar_is_pending(self) -> None:
        # Bars cover the horizon but omit the as-of bar → no entry price → pending.
        bars = [
            _bar(1, high=111.0, low=99.0, close=105.0),
            _bar(2, high=106.0, low=101.0, close=104.0),
            _bar(3, high=109.0, low=103.0, close=108.0),
        ]
        outcome = score_recommendation(_entry(), bars, now=_NOW)
        assert outcome.outcome_class == "pending"
