"""Plan 0060 phase 2 — the pure watch evaluators + edge reducer.

Done-when claims pinned here:
(a) threshold evaluation reads only the latest *closed* bar — a forming-bar
    value crossing the level does NOT fire (no-lookahead carried to alerting),
    and the same watch fires once that bar closes;
(b) the edge reducer proves false→true fires, true→true does not (a condition
    staying true across N polls yields exactly one alert), and
    true→false→true fires again; `None` arms without firing;
(c) each evaluator is pure — called twice with identical inputs it returns
    identical outputs.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from types import ModuleType
from typing import Any

import pytest

from market_analyser.alerts.evaluate import (
    evaluate_watch,
    evaluate_watch_detail,
    should_fire,
)
from market_analyser.alerts.types import Watch, validate_watch_params
from market_analyser.contracts import BaseParams, Signal, SignalKind, StrategyMeta
from market_analyser.data.timeframes import bar_duration
from market_analyser.data.types import Bar

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
_FAR_FUTURE = datetime(2030, 1, 1, tzinfo=UTC)
_CREATED_AT = datetime(2026, 6, 1, tzinfo=UTC)


def _bars(closes: Sequence[float], *, timeframe: str = "1d", symbol: str = "TEST") -> list[Bar]:
    """Flat-body bars (open=high=low=close) — range 0, so no candlestick
    pattern can print on them; the threshold/strategy tests read only closes."""
    step = bar_duration(timeframe)
    return [
        Bar(
            symbol=symbol,
            timeframe=timeframe,
            event_ts=_EPOCH + step * i,
            open=price,
            high=price,
            low=price,
            close=price,
            volume=100.0,
            source="fixture",
        )
        for i, price in enumerate(closes)
    ]


def _watch(kind: str, params: dict[str, Any], *, timeframe: str = "1d") -> Watch:
    return Watch(
        id=1,
        symbol="TEST",
        timeframe=timeframe,
        kind=kind,  # type: ignore[arg-type]  # exercised through the boundary validator
        params=validate_watch_params(kind, params),
        interval_seconds=60,
        enabled=True,
        last_state=None,
        created_at=_CREATED_AT,
    )


def _close_below(level: float) -> Watch:
    return _watch(
        "indicator_threshold",
        {"indicator": "close", "operator": "<", "level": level},
    )


class TestIndicatorThreshold:
    def test_fires_on_latest_closed_bar_value(self) -> None:
        bars = _bars([105.0, 104.0, 95.0])
        assert evaluate_watch(_close_below(100.0), bars, now=_FAR_FUTURE) is True

    def test_forming_bar_crossing_the_level_does_not_fire(self) -> None:
        """No-lookahead carried to alerting: the latest bar's close (95) is
        under the level but the bar is still forming relative to `now` — the
        latest *closed* bar (105) is what counts, so no fire. Once `now`
        passes the bar's close time, the same inputs fire."""
        bars = _bars([106.0, 105.0, 95.0])
        step = bar_duration("1d")
        # One second after the last bar OPENED — it has not closed yet.
        now_forming = bars[-1].event_ts.replace(second=1)
        assert evaluate_watch(_close_below(100.0), bars, now=now_forming) is False

        now_closed = bars[-1].event_ts + step
        assert evaluate_watch(_close_below(100.0), bars, now=now_closed) is True

    def test_condition_detail_names_the_fact(self) -> None:
        bars = _bars([105.0, 95.0])
        detail = evaluate_watch_detail(_close_below(100.0), bars, now=_FAR_FUTURE)
        assert detail.result is True
        assert detail.condition == "close 95 < 100"
        assert detail.values == {"close": 95.0, "level": 100.0}

    def test_real_indicator_composes(self) -> None:
        """RSI over a monotonically falling series is low (0, in fact) — the
        rsi<50 watch fires; the mirrored rsi>50 watch does not."""
        bars = _bars([float(100 - i) for i in range(30)])
        rsi_low = _watch(
            "indicator_threshold", {"indicator": "rsi", "operator": "<", "level": 50.0}
        )
        rsi_high = _watch(
            "indicator_threshold", {"indicator": "rsi", "operator": ">", "level": 50.0}
        )
        assert evaluate_watch(rsi_low, bars, now=_FAR_FUTURE) is True
        assert evaluate_watch(rsi_high, bars, now=_FAR_FUTURE) is False

    def test_undefined_indicator_is_false_not_error(self) -> None:
        """Two bars are too few for RSI(14) to warm up — an honest False."""
        bars = _bars([100.0, 99.0])
        watch = _watch("indicator_threshold", {"indicator": "rsi", "operator": "<", "level": 50.0})
        detail = evaluate_watch_detail(watch, bars, now=_FAR_FUTURE)
        assert detail.result is False
        assert "undefined" in detail.condition

    def test_no_closed_bars_is_false_not_error(self) -> None:
        bars = _bars([95.0])
        now = bars[0].event_ts.replace(second=1)  # the only bar is still forming
        detail = evaluate_watch_detail(_close_below(100.0), bars, now=now)
        assert detail.result is False
        assert detail.condition == "no closed bars to evaluate"
        assert evaluate_watch_detail(_close_below(100.0), [], now=now).result is False

    def test_naive_now_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            evaluate_watch(_close_below(100.0), _bars([95.0]), now=datetime(2026, 6, 1))


class TestPattern:
    def _doji_bar(self, ts_index: int, *, timeframe: str = "1d") -> Bar:
        step = bar_duration(timeframe)
        return Bar(
            symbol="TEST",
            timeframe=timeframe,
            event_ts=_EPOCH + step * ts_index,
            open=100.0,
            high=105.0,
            low=95.0,
            close=100.2,  # body 0.2 vs range 10 -> doji
            volume=100.0,
            source="fixture",
        )

    def test_pattern_on_latest_closed_bar_fires(self) -> None:
        bars = [*_bars([101.0, 102.0]), self._doji_bar(2)]
        watch = _watch("pattern", {"pattern": "doji"})
        detail = evaluate_watch_detail(watch, bars, now=_FAR_FUTURE)
        assert detail.result is True
        assert detail.condition == "doji printed on the latest closed bar"
        assert set(detail.values) == {"strength"}

    def test_pattern_on_an_earlier_bar_does_not_fire(self) -> None:
        step = bar_duration("1d")
        trailing = Bar(
            symbol="TEST",
            timeframe="1d",
            event_ts=_EPOCH + step * 2,
            open=103.0,
            high=103.0,
            low=103.0,
            close=103.0,
            volume=100.0,
            source="fixture",
        )
        bars = [*_bars([101.0]), self._doji_bar(1), trailing]
        watch = _watch("pattern", {"pattern": "doji"})
        assert evaluate_watch(watch, bars, now=_FAR_FUTURE) is False

    def test_forming_pattern_bar_does_not_fire(self) -> None:
        bars = [*_bars([101.0, 102.0]), self._doji_bar(2)]
        now = bars[-1].event_ts.replace(second=1)  # doji bar still forming
        watch = _watch("pattern", {"pattern": "doji"})
        assert evaluate_watch(watch, bars, now=now) is False


class _NoParams(BaseParams):
    pass


def _fake_strategy(signals_for: dict[int, SignalKind]) -> ModuleType:
    """A contract-shaped strategy emitting `signals_for[bar_index]` signals."""

    def _gen(bars: Sequence[Bar], params: BaseParams) -> list[Signal]:
        return [Signal(bar_index=i, kind=kind) for i, kind in signals_for.items() if i < len(bars)]

    mod = ModuleType("fake_strategy")
    setattr(
        mod,
        "META",
        StrategyMeta(
            id="fake",
            name="Fake",
            description="test double",
            version="1.0.0",
            timeframes=("1d",),
        ),
    )
    setattr(mod, "Params", _NoParams)
    setattr(mod, "generate_signals", _gen)
    return mod


class TestStrategySignal:
    def _patch_discover(self, monkeypatch: pytest.MonkeyPatch, module: ModuleType) -> None:
        monkeypatch.setattr("market_analyser.alerts.evaluate.discover", lambda: {"fake": module})

    def test_fresh_signal_fires(self, monkeypatch: pytest.MonkeyPatch) -> None:
        bars = _bars([100.0, 101.0, 102.0])
        self._patch_discover(monkeypatch, _fake_strategy({2: SignalKind.ENTER_LONG}))
        watch = _watch("strategy_signal", {"strategy_id": "fake"})
        detail = evaluate_watch_detail(watch, bars, now=_FAR_FUTURE)
        assert detail.result is True
        assert detail.condition == ("strategy fake emitted enter_long on the latest closed bar")

    def test_stale_signal_does_not_fire(self, monkeypatch: pytest.MonkeyPatch) -> None:
        bars = _bars([100.0, 101.0, 102.0])
        self._patch_discover(monkeypatch, _fake_strategy({0: SignalKind.ENTER_LONG}))
        watch = _watch("strategy_signal", {"strategy_id": "fake"})
        assert evaluate_watch(watch, bars, now=_FAR_FUTURE) is False

    def test_signal_on_forming_bar_does_not_fire(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The strategy sees only closed bars: with the latest bar forming, a
        would-be signal on it is invisible to the evaluation."""
        bars = _bars([100.0, 101.0, 102.0])
        self._patch_discover(monkeypatch, _fake_strategy({2: SignalKind.ENTER_LONG}))
        now = bars[-1].event_ts.replace(second=1)
        watch = _watch("strategy_signal", {"strategy_id": "fake"})
        assert evaluate_watch(watch, bars, now=now) is False

    def test_unknown_strategy_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_discover(monkeypatch, _fake_strategy({}))
        watch = _watch("strategy_signal", {"strategy_id": "nope"})
        with pytest.raises(ValueError, match="unknown strategy_id"):
            evaluate_watch(watch, _bars([100.0, 101.0]), now=_FAR_FUTURE)


class TestEdgeReducer:
    @pytest.mark.parametrize(
        ("last_state", "current", "expected"),
        [
            (False, True, True),  # the edge: false -> true fires
            (True, True, False),  # staying true stays silent
            (True, False, False),  # going false re-arms, no fire
            (False, False, False),
            (None, True, False),  # fresh watch arms without firing
            (None, False, False),
        ],
    )
    def test_transitions(self, last_state: bool | None, current: bool, expected: bool) -> None:
        assert should_fire(last_state, current) is expected

    def test_condition_staying_true_yields_exactly_one_fire(self) -> None:
        """Fold a poll sequence through the reducer: one fire for the first
        false→true, silence while true, a second fire after true→false→true."""
        observations = [False, True, True, True, False, True]
        last_state: bool | None = None
        fires = []
        for current in observations:
            fires.append(should_fire(last_state, current))
            last_state = current
        assert fires == [False, True, False, False, False, True]


class TestPurity:
    def test_threshold_evaluation_is_pure(self) -> None:
        bars = _bars([105.0, 95.0])
        watch = _close_below(100.0)
        first = evaluate_watch_detail(watch, bars, now=_FAR_FUTURE)
        second = evaluate_watch_detail(watch, bars, now=_FAR_FUTURE)
        assert first == second

    def test_pattern_evaluation_is_pure(self) -> None:
        step = bar_duration("1d")
        doji = Bar(
            symbol="TEST",
            timeframe="1d",
            event_ts=_EPOCH + step * 2,
            open=100.0,
            high=105.0,
            low=95.0,
            close=100.2,
            volume=100.0,
            source="fixture",
        )
        bars = [*_bars([101.0, 102.0]), doji]
        watch = _watch("pattern", {"pattern": "doji"})
        first = evaluate_watch_detail(watch, bars, now=_FAR_FUTURE)
        second = evaluate_watch_detail(watch, bars, now=_FAR_FUTURE)
        assert first == second

    def test_strategy_evaluation_is_pure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        bars = _bars([100.0, 101.0, 102.0])
        monkeypatch.setattr(
            "market_analyser.alerts.evaluate.discover",
            lambda: {"fake": _fake_strategy({2: SignalKind.ENTER_LONG})},
        )
        watch = _watch("strategy_signal", {"strategy_id": "fake"})
        first = evaluate_watch_detail(watch, bars, now=_FAR_FUTURE)
        second = evaluate_watch_detail(watch, bars, now=_FAR_FUTURE)
        assert first == second
