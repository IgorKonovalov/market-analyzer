"""Phase 1 done-when for Plan 0075 — the `ichimoku` TK-cross strategy.

All tests share one deterministic V-shaped fixture (decline → rally → pullback,
`high == low == close` so the Ichimoku high/low midpoints reduce to close
midpoints), run with *small* periods (`conversion=2, base=3, span_b=4`) so the
signals land inside a ~30-bar series that is tractable to reason about. The
displacement is varied per test because it governs which cloud/Chikou index the
strategy reads — it does not change the (trailing) Ichimoku series itself.

Signal bars were derived from the indicator output, not guessed:

    bull TK cross at bar 11 (close 88, above the cloud under it);
    bear TK cross at bar 21 (close 130).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from market_analyser.contracts import Bar, Signal, SignalKind
from market_analyser.contracts.strategy import discover
from market_analyser.strategies import ichimoku

# Decline (0-9) → rally (10-19) → pullback (20-29). A bullish TK cross forms at
# the rally's start, a bearish one at the pullback's start.
_CLOSES: tuple[float, ...] = (
    100.0,
    98.0,
    96.0,
    94.0,
    92.0,
    90.0,
    88.0,
    86.0,
    84.0,
    82.0,
    84.0,
    88.0,
    94.0,
    100.0,
    106.0,
    112.0,
    118.0,
    124.0,
    130.0,
    136.0,
    134.0,
    130.0,
    124.0,
    118.0,
    112.0,
    106.0,
    100.0,
    96.0,
    92.0,
    90.0,
)


def _bars(closes: Sequence[float]) -> list[Bar]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return [
        Bar(
            symbol="TEST",
            timeframe="1d",
            event_ts=start + timedelta(days=i),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=0.0,
            source="fixture",
        )
        for i, close in enumerate(closes)
    ]


def _fixture() -> list[Bar]:
    return _bars(_CLOSES)


def _params(**overrides: Any) -> ichimoku.Params:
    """The shared small-period config; `**overrides` tweak displacement/toggles."""

    merged: dict[str, Any] = {"conversion": 2, "base": 3, "span_b": 4, "displacement": 2}
    merged.update(overrides)
    return ichimoku.Params(**merged)


def _kinds_at(signals: Sequence[Signal], bar: int) -> list[SignalKind]:
    return [s.kind for s in signals if s.bar_index == bar]


def test_discover_includes_ichimoku() -> None:
    assert "ichimoku" in discover()
    assert discover()["ichimoku"].META.id == "ichimoku"


def test_bull_cross_above_cloud_emits_long_at_cross_bar_not_before() -> None:
    """Default (cloud-confirmed) run: a long entry at the bull-cross bar, and
    nothing at any earlier bar — the decline before it produces no signal."""

    bars = _fixture()
    signals = list(ichimoku.generate_signals(bars, _params()))

    assert signals, "expected at least the bull-cross long entry"
    first = signals[0]
    assert first.bar_index == 11
    assert first.kind is SignalKind.ENTER_LONG
    assert all(s.bar_index >= 11 for s in signals), "no signal may precede the cross"


def test_bear_cross_emits_short_that_long_only_suppresses() -> None:
    """With cloud confirmation off the bearish TK cross opens a short (stop-and-
    reverse out of the long); `long_only=True` suppresses that short entirely."""

    bars = _fixture()

    both_ways = list(ichimoku.generate_signals(bars, _params(require_cloud_confirmation=False)))
    assert _kinds_at(both_ways, 21) == [SignalKind.EXIT_LONG, SignalKind.ENTER_SHORT]

    long_only = list(
        ichimoku.generate_signals(bars, _params(require_cloud_confirmation=False, long_only=True))
    )
    assert all(s.kind is not SignalKind.ENTER_SHORT for s in long_only)
    # With no short to reverse into, the long simply stays open past bar 21.
    assert [s.kind for s in long_only] == [SignalKind.ENTER_LONG]


def test_chikou_confirmation_withholds_disagreeing_entry() -> None:
    """Reading the close `displacement` bars back: at the bull cross (bar 11,
    close 88) the price is *below* where it was 6 bars earlier (close 90), so a
    Chikou-confirmed long is withheld even though the cross fired."""

    bars = _fixture()

    without = list(
        ichimoku.generate_signals(bars, _params(displacement=6, require_cloud_confirmation=False))
    )
    assert any(s.bar_index == 11 and s.kind is SignalKind.ENTER_LONG for s in without), (
        "chikou-off run should take the bar-11 long"
    )

    with_chikou = list(
        ichimoku.generate_signals(
            bars,
            _params(
                displacement=6,
                require_cloud_confirmation=False,
                require_chikou_confirmation=True,
            ),
        )
    )
    assert not any(s.bar_index == 11 and s.kind is SignalKind.ENTER_LONG for s in with_chikou), (
        "chikou-on run must withhold the disagreeing bar-11 long"
    )


def test_exit_on_cloud_cross_flattens_the_position() -> None:
    """`exit_on_cloud_cross` closes the long when the pullback re-enters the
    cloud, without waiting for a (cloud-unconfirmed) opposing cross."""

    bars = _fixture()
    signals = list(ichimoku.generate_signals(bars, _params(exit_on_cloud_cross=True)))
    kinds = [(s.bar_index, s.kind) for s in signals]
    assert (11, SignalKind.ENTER_LONG) in kinds
    assert any(k is SignalKind.EXIT_LONG for _, k in kinds), "cloud re-entry should exit"


def test_no_lookahead_truncation_invariance() -> None:
    """The displacement is the real lookahead risk. Re-running on every prefix
    must leave each already-emitted signal unchanged — a decision at bar i is a
    fact about bars[0..=i] only."""

    bars = _fixture()
    params = _params(require_cloud_confirmation=False)  # yields both a long and a short
    full = list(ichimoku.generate_signals(bars, params))
    assert len(full) >= 2, "fixture should exercise more than one signal"

    for sig in full:
        prefix = list(ichimoku.generate_signals(bars[: sig.bar_index + 1], params))
        prefix_at_or_before = [s for s in prefix if s.bar_index <= sig.bar_index]
        full_at_or_before = [s for s in full if s.bar_index <= sig.bar_index]
        assert prefix_at_or_before == full_at_or_before


def test_deterministic() -> None:
    bars = _fixture()
    params = _params()
    a = list(ichimoku.generate_signals(bars, params))
    b = list(ichimoku.generate_signals(bars, params))
    assert a == b


def test_signal_indices_in_range() -> None:
    bars = _fixture()
    for sig in ichimoku.generate_signals(bars, _params(require_cloud_confirmation=False)):
        assert 0 <= sig.bar_index < len(bars)


def test_params_rejects_unknown_key() -> None:
    with pytest.raises(ValidationError):
        ichimoku.Params(cnoversion=9)  # type: ignore[call-arg]  # typo — extra="forbid"


def test_params_validates_period_constraints() -> None:
    with pytest.raises(ValidationError):
        ichimoku.Params(conversion=0)
    with pytest.raises(ValidationError):
        ichimoku.Params(displacement=0)


def test_defaults_are_the_classic_reading() -> None:
    p = ichimoku.Params()
    assert (p.conversion, p.base, p.span_b, p.displacement) == (9, 26, 52, 26)
    assert p.require_cloud_confirmation is True
    assert p.require_chikou_confirmation is False
    assert p.long_only is False
    assert p.exit_on_cloud_cross is False
