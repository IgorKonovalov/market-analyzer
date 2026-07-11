"""Track-record aggregation — Plan 0080 phase 4 (ADR-0075).

The pure roll-up over scored ledger rows into the honest track record: overall
and per-bucket **hit-rate + mean R**, a **calibration** read (Brier + reliability
buckets), and a **baseline comparison** — every aggregate carrying its sample
size and refusing a conclusion below a stated floor.

The three honesty rules from ADR-0075, made structural:

* **Baseline-relative.** A directional hit-rate means nothing without the trivial
  alternative. The pinned baseline is **buy-and-hold over the horizon** (always
  long; a "hit" is price ending the horizon higher): `baseline_hit_rate` is
  always computed and `hit_rate_vs_baseline` is the number that actually matters.
  A call-set that merely rides an uptrend (always long, price usually up) scores
  a hit-rate ≈ the baseline — "right" without "beats trivial" shows as ~zero edge.
* **Calibration, not just accuracy.** The forecast probability each call staked on
  its direction is scored: `brier` plus `reliability` buckets (stated probability
  vs realized frequency) surface the overconfidence a raw hit-rate hides.
* **Honest small-n.** Below `MIN_TRACK_RECORD_N` the advisor's conclusion fields
  (`hit_rate`, `mean_r`, `brier`, `hit_rate_vs_baseline`) are withheld (`None`)
  and `sufficient` is `False` — a 3-call "67%" is noise, and the surface says so
  rather than implying skill. Every bucket carries its own `n` + `sufficient`.

Direction axis: a "hit" is `directional_correct` — did price end the horizon in
the called direction — which is the axis comparable to a buy-and-hold baseline. R
(`mean_r`) is the separate, path-dependent ticket P&L (stops honoured). Both are
reported; they answer different questions.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from statistics import fmean
from typing import Literal

from pydantic import BaseModel, ConfigDict

from market_analyser.persistence.advice_ledger_repository import AdviceLedgerEntry

# The stated sample floor below which no conclusion is drawn (ADR-0075 honest
# small-n). A deliberately conservative default: a personal advisor accrues calls
# slowly, and a handful of them is noise, not evidence.
MIN_TRACK_RECORD_N = 20

# The one pinned, documented baseline (ADR-0075: the choice is a judgment call, so
# it is named, not implied). Buy-and-hold over the horizon = always long; a hit is
# price ending the horizon higher than the entry.
Baseline = Literal["buy_and_hold_over_horizon"]
DEFAULT_BASELINE: Baseline = "buy_and_hold_over_horizon"

ConvictionBucket = Literal["low", "medium", "high"]

# Reliability bins for the calibration read (coarse, so each bin can hold enough
# calls to be readable). Upper bound of the last bin is > 1 so a stated prob of
# exactly 1.0 lands in it.
_RELIABILITY_BINS: tuple[tuple[float, float], ...] = (
    (0.0, 0.2),
    (0.2, 0.4),
    (0.4, 0.6),
    (0.6, 0.8),
    (0.8, 1.0001),
)

_SCORED_CLASSES = ("target_hit", "stopped", "timeout")


class BucketStat(BaseModel):
    """One (symbol, horizon, conviction-bucket) slice of the record. Carries its
    own `n` + `sufficient`; below the floor its hit-rate/mean-R are withheld."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str
    horizon_bars: int
    conviction_bucket: ConvictionBucket
    n: int
    sufficient: bool
    hit_rate: float | None
    mean_r: float | None


class ReliabilityBucket(BaseModel):
    """One reliability bin: how often calls staking ~`mean_predicted` were right
    (`observed_freq`). A well-calibrated advisor has the two close in every bin."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lower: float
    upper: float
    n: int
    mean_predicted: float
    observed_freq: float


class TrackRecord(BaseModel):
    """The advisor's live track record over the scored directional calls.

    `hit_rate`/`mean_r`/`brier`/`hit_rate_vs_baseline` are the advisor's
    conclusion fields — `None` when `sufficient` is `False` (below `min_n`), so a
    small sample can never be presented as a conclusion. `baseline_hit_rate` is a
    market fact (present whenever any call was scored), and `hit_rate_vs_baseline`
    is the edge over the trivial alternative. Calibration lives in `brier` +
    `reliability` + the `mean_forecast_prob`/`observed_hit_rate` pair.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    n: int
    sufficient: bool
    min_n: int
    hit_rate: float | None
    mean_r: float | None
    brier: float | None
    calibration_n: int
    mean_forecast_prob: float | None
    observed_hit_rate: float | None
    reliability: list[ReliabilityBucket]
    baseline_kind: Baseline
    baseline_hit_rate: float | None
    hit_rate_vs_baseline: float | None
    by_bucket: list[BucketStat]


def _price_ended_higher(row: AdviceLedgerEntry) -> bool:
    """Reconstruct the raw price move sign from the call's direction axis: a long
    that was directionally correct means price rose; a short that was correct
    means it fell. This is the buy-and-hold baseline's "hit"."""
    assert row.directional_correct is not None  # scored rows only
    return row.directional_correct if row.direction == "long" else not row.directional_correct


def _conviction_bucket(conviction: float) -> ConvictionBucket:
    if conviction < 0.34:
        return "low"
    if conviction < 0.67:
        return "medium"
    return "high"


def _mean_hit(rows: Sequence[AdviceLedgerEntry]) -> float:
    return fmean(1.0 if row.directional_correct else 0.0 for row in rows)


def _mean_r(rows: Sequence[AdviceLedgerEntry]) -> float | None:
    realized = [row.realized_r for row in rows if row.realized_r is not None]
    return fmean(realized) if realized else None


def _reliability(calibrated: Sequence[AdviceLedgerEntry]) -> list[ReliabilityBucket]:
    out: list[ReliabilityBucket] = []
    for lower, upper in _RELIABILITY_BINS:
        binned = [
            row
            for row in calibrated
            if row.forecast_prob is not None and lower <= row.forecast_prob < upper
        ]
        if not binned:
            continue
        probs = [row.forecast_prob for row in binned if row.forecast_prob is not None]
        out.append(
            ReliabilityBucket(
                lower=lower,
                # Report the nominal band upper (1.0, not the 1.0001 sentinel).
                upper=min(upper, 1.0),
                n=len(binned),
                mean_predicted=fmean(probs),
                observed_freq=_mean_hit(binned),
            )
        )
    return out


def _by_bucket(rows: Sequence[AdviceLedgerEntry], *, min_n: int) -> list[BucketStat]:
    groups: dict[tuple[str, int, ConvictionBucket], list[AdviceLedgerEntry]] = defaultdict(list)
    for row in rows:
        key = (row.symbol, row.horizon_bars, _conviction_bucket(row.conviction))
        groups[key].append(row)
    out: list[BucketStat] = []
    # Deterministic order: symbol, then horizon, then conviction band.
    order = {"low": 0, "medium": 1, "high": 2}
    for symbol, horizon, conviction in sorted(groups, key=lambda k: (k[0], k[1], order[k[2]])):
        bucket = groups[(symbol, horizon, conviction)]
        n = len(bucket)
        sufficient = n >= min_n
        out.append(
            BucketStat(
                symbol=symbol,
                horizon_bars=horizon,
                conviction_bucket=conviction,
                n=n,
                sufficient=sufficient,
                hit_rate=_mean_hit(bucket) if sufficient else None,
                mean_r=_mean_r(bucket) if sufficient else None,
            )
        )
    return out


def track_record(
    rows: Sequence[AdviceLedgerEntry],
    *,
    baseline: Baseline = DEFAULT_BASELINE,
    min_n: int = MIN_TRACK_RECORD_N,
) -> TrackRecord:
    """Aggregate scored ledger rows into the honest track record.

    Only scored directional calls contribute (flat calls have no direction to
    score; `pending`/unscored rows are ignored). Deterministic given the same
    rows. `baseline` is the pinned buy-and-hold-over-horizon alternative — a
    single documented value, not a knob to flatter the number.
    """
    scored = [
        row
        for row in rows
        if row.direction in ("long", "short")
        and row.outcome_class in _SCORED_CLASSES
        and row.directional_correct is not None
    ]
    n = len(scored)
    sufficient = n >= min_n

    baseline_hit_rate = (
        fmean(1.0 if _price_ended_higher(row) else 0.0 for row in scored) if n >= 1 else None
    )
    by_bucket = _by_bucket(scored, min_n=min_n)

    if not sufficient:
        # Below the floor: withhold every advisor conclusion; keep the raw
        # sample size, the market-fact baseline, and the per-bucket breakdown
        # (each bucket already withholds its own conclusion).
        return TrackRecord(
            n=n,
            sufficient=False,
            min_n=min_n,
            hit_rate=None,
            mean_r=None,
            brier=None,
            calibration_n=0,
            mean_forecast_prob=None,
            observed_hit_rate=None,
            reliability=[],
            baseline_kind=baseline,
            baseline_hit_rate=baseline_hit_rate,
            hit_rate_vs_baseline=None,
            by_bucket=by_bucket,
        )

    hit_rate = _mean_hit(scored)
    calibrated = [row for row in scored if row.forecast_prob is not None]
    brier = (
        fmean(
            (row.forecast_prob - (1.0 if row.directional_correct else 0.0)) ** 2
            for row in calibrated
            if row.forecast_prob is not None
        )
        if calibrated
        else None
    )
    mean_forecast_prob = (
        fmean(row.forecast_prob for row in calibrated if row.forecast_prob is not None)
        if calibrated
        else None
    )
    observed_hit_rate = _mean_hit(calibrated) if calibrated else None
    assert baseline_hit_rate is not None  # n >= min_n >= 1 here

    return TrackRecord(
        n=n,
        sufficient=True,
        min_n=min_n,
        hit_rate=hit_rate,
        mean_r=_mean_r(scored),
        brier=brier,
        calibration_n=len(calibrated),
        mean_forecast_prob=mean_forecast_prob,
        observed_hit_rate=observed_hit_rate,
        reliability=_reliability(calibrated),
        baseline_kind=baseline,
        baseline_hit_rate=baseline_hit_rate,
        hit_rate_vs_baseline=hit_rate - baseline_hit_rate,
        by_bucket=by_bucket,
    )


__all__ = [
    "DEFAULT_BASELINE",
    "MIN_TRACK_RECORD_N",
    "Baseline",
    "BucketStat",
    "ConvictionBucket",
    "ReliabilityBucket",
    "TrackRecord",
    "track_record",
]
