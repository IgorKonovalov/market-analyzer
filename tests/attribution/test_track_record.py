"""Plan 0080 phase 4: the track-record aggregation (ADR-0075).

The honesty properties, on hand-built scored rows:

- hit-rate, mean R, and Brier match hand-computed values on a sufficient set;
- the **baseline comparison is always present**, and an all-long set riding an
  uptrend shows hit-rate ≈ baseline (a ~zero edge — "right" ≠ "beats trivial");
- an overconfident set (80% stated, 55% realized) is flagged miscalibrated;
- a 3-row set returns `sufficient: false` and withholds the advisor's hit-rate;
- flat and pending rows never contribute; every bucket carries its own `n`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from market_analyser.attribution.track_record import (
    MIN_TRACK_RECORD_N,
    track_record,
)
from market_analyser.persistence.advice_ledger_repository import AdviceLedgerEntry

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _scored(
    *,
    symbol: str = "AAA",
    direction: str = "long",
    horizon_bars: int = 5,
    conviction: float = 0.6,
    forecast_prob: float | None = 0.6,
    directional_correct: bool = True,
    realized_r: float = 1.0,
    outcome_class: str = "target_hit",
) -> AdviceLedgerEntry:
    return AdviceLedgerEntry(
        symbol=symbol,
        timeframe="1d",
        strategy_id="rsi",
        as_of_bar_ts=_T0,
        horizon_bars=horizon_bars,
        direction=direction,  # type: ignore[arg-type]
        entry_zone=(99.0, 101.0),
        stop=90.0,
        targets=[110.0],
        conviction=conviction,
        forecast_prob=forecast_prob,
        artifact_path=None,
        created_at=_T0,
        outcome_class=outcome_class,
        realized_return=0.1 if realized_r >= 0 else -0.1,
        realized_r=realized_r,
        directional_correct=directional_correct,
        scored_at=_T0,
    )


def _flat_unscored() -> AdviceLedgerEntry:
    return AdviceLedgerEntry(
        symbol="SPY",
        timeframe="1d",
        strategy_id="rsi",
        as_of_bar_ts=_T0,
        horizon_bars=5,
        direction="flat",
        entry_zone=None,
        stop=None,
        targets=[],
        conviction=0.0,
        forecast_prob=None,
        artifact_path=None,
        created_at=_T0,
    )


def _pending() -> AdviceLedgerEntry:
    # A directional call whose horizon has not matured — no outcome yet.
    return _scored(outcome_class="pending").model_copy(
        update={
            "outcome_class": "pending",
            "realized_return": None,
            "realized_r": None,
            "directional_correct": None,
            "scored_at": None,
        }
    )


def test_hit_rate_mean_r_and_brier_match_hand_computed() -> None:
    # 20 long calls: 13 directionally correct (+1R), 7 wrong (-1R). All staked 0.6.
    rows = [_scored(directional_correct=True, realized_r=1.0) for _ in range(13)]
    rows += [_scored(directional_correct=False, realized_r=-1.0) for _ in range(7)]
    tr = track_record(rows)

    assert tr.n == 20
    assert tr.sufficient is True
    assert tr.hit_rate == pytest.approx(13 / 20)  # 0.65
    assert tr.mean_r == pytest.approx((13 - 7) / 20)  # 0.30
    # Brier over prob=0.6: correct -> (0.6-1)^2=0.16, wrong -> (0.6-0)^2=0.36.
    expected_brier = (13 * 0.16 + 7 * 0.36) / 20
    assert tr.brier == pytest.approx(expected_brier)
    assert tr.calibration_n == 20
    assert tr.mean_forecast_prob == pytest.approx(0.6)
    assert tr.observed_hit_rate == pytest.approx(0.65)


def test_baseline_always_present_and_trend_mimic_shows_near_zero_edge() -> None:
    # All-long calls in an uptrend: 15/20 ended higher (directionally correct).
    # A buy-and-hold baseline (always long) is right on exactly those same 15, so
    # the advisor's hit-rate equals the baseline — no edge over the trivial call.
    rows = [_scored(direction="long", directional_correct=True) for _ in range(15)]
    rows += [_scored(direction="long", directional_correct=False) for _ in range(5)]
    tr = track_record(rows)

    assert tr.baseline_hit_rate == pytest.approx(0.75)  # always present
    assert tr.hit_rate == pytest.approx(0.75)
    assert tr.hit_rate_vs_baseline == pytest.approx(0.0)  # "right" but not "beats trivial"


def test_correct_shorts_beat_the_buy_and_hold_baseline() -> None:
    # 18 short calls that were directionally correct (price fell) + 2 wrong. A
    # buy-and-hold baseline (always long) is wrong whenever price fell, so the
    # advisor's directional hit-rate is well above the baseline — a real edge.
    rows = [_scored(direction="short", directional_correct=True) for _ in range(18)]
    rows += [_scored(direction="short", directional_correct=False) for _ in range(2)]
    tr = track_record(rows)

    assert tr.hit_rate == pytest.approx(0.9)
    # price rose only on the 2 "wrong" shorts → baseline hit-rate 2/20.
    assert tr.baseline_hit_rate == pytest.approx(0.1)
    assert tr.hit_rate_vs_baseline == pytest.approx(0.8)


def test_overconfident_set_is_flagged_miscalibrated() -> None:
    # 20 calls all staking 0.80, but right only 11/20 (0.55) — badly overconfident.
    rows = [_scored(forecast_prob=0.8, directional_correct=True) for _ in range(11)]
    rows += [_scored(forecast_prob=0.8, directional_correct=False) for _ in range(9)]
    tr = track_record(rows)

    assert tr.mean_forecast_prob is not None and tr.observed_hit_rate is not None
    assert tr.mean_forecast_prob == pytest.approx(0.8)
    assert tr.observed_hit_rate == pytest.approx(0.55)
    # The calibration gap is large and the reliability bucket shows it.
    assert abs(tr.mean_forecast_prob - tr.observed_hit_rate) == pytest.approx(0.25)
    assert len(tr.reliability) == 1
    bucket = tr.reliability[0]
    assert bucket.mean_predicted == pytest.approx(0.8)
    assert bucket.observed_freq == pytest.approx(0.55)
    assert bucket.n == 20


def test_small_sample_is_insufficient_and_withholds_the_hit_rate() -> None:
    rows = [_scored() for _ in range(3)]
    tr = track_record(rows)

    assert tr.n == 3
    assert tr.sufficient is False
    assert tr.min_n == MIN_TRACK_RECORD_N
    assert tr.hit_rate is None  # no conclusion on a handful of calls
    assert tr.mean_r is None
    assert tr.brier is None
    assert tr.hit_rate_vs_baseline is None
    # The raw baseline fact is still available (present whenever any call scored).
    assert tr.baseline_hit_rate is not None


def test_flat_and_pending_rows_never_contribute() -> None:
    rows: list[AdviceLedgerEntry] = [_scored() for _ in range(20)]
    rows.append(_flat_unscored())
    rows.append(_pending())
    tr = track_record(rows)
    assert tr.n == 20  # only the scored directional calls


def test_empty_input_is_a_clean_insufficient_record() -> None:
    tr = track_record([])
    assert tr.n == 0
    assert tr.sufficient is False
    assert tr.hit_rate is None
    assert tr.baseline_hit_rate is None  # nothing scored → no market fact either
    assert tr.by_bucket == []


def test_by_bucket_breaks_down_with_its_own_n() -> None:
    rows = [_scored(symbol="AAA", horizon_bars=5, conviction=0.8) for _ in range(20)]
    rows += [_scored(symbol="BBB", horizon_bars=5, conviction=0.2) for _ in range(4)]
    tr = track_record(rows)

    by_key = {(b.symbol, b.horizon_bars, b.conviction_bucket): b for b in tr.by_bucket}
    aaa = by_key[("AAA", 5, "high")]
    bbb = by_key[("BBB", 5, "low")]
    assert aaa.n == 20 and aaa.sufficient is True and aaa.hit_rate is not None
    assert bbb.n == 4 and bbb.sufficient is False and bbb.hit_rate is None  # own small-n
