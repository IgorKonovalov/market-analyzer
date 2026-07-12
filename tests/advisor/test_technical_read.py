"""Phase-1 done-when for Plan 0074: the single-indicator technical read (ADR-0068).

The technical read is the *lesser* advisory tier — one curated regime indicator
mapped to a direction by its textbook mechanical rule, with **no** conviction and
**no** entry/stop/target levels. The suite pins:

* each curated rule's regime→direction mapping on a fixture where the answer is
  clear by construction (uptrend → long, the mixed EMA stack → flat, MACD by
  histogram sign);
* the honest degenerate: too little history → ``flat`` (indicator undefined);
* boundary validation: an unknown ``indicator_id`` raises, listing the known set;
* the structural honesty pin: `TechnicalRead` **rejects** a ``conviction``/``stop``
  field at construction (``extra="forbid"`` — it can never be dressed as a ticket);
* anti-lookahead: the read on a prefix equals the full-series indicator read as of
  the truncation bar (no future bars leak into the call).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from market_analyser.advisor.models import TechnicalRead
from market_analyser.advisor.technical_read import (
    eligible_indicators,
    technical_read,
)
from market_analyser.analysis import indicators as ind
from market_analyser.data.types import Bar

_TF = "1d"
_SYMBOL = "BTC-USD"


def _bar(i: int, *, o: float, h: float, low: float, c: float) -> Bar:
    return Bar(
        symbol=_SYMBOL,
        timeframe=_TF,
        event_ts=datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=i),
        open=o,
        high=h,
        low=low,
        close=c,
        volume=1000.0,
        source="synthetic",
    )


def _uptrend(n: int) -> list[Bar]:
    """A steady rising series with real ranges (so ATR/Supertrend are well-defined):
    close climbs +2/bar near the top of each bar's range."""

    out: list[Bar] = []
    for i in range(n):
        base = 100.0 + 2.0 * i
        out.append(_bar(i, o=base - 0.5, h=base + 1.5, low=base - 1.0, c=base + 1.0))
    return out


def _bars_from_closes(closes: list[float]) -> list[Bar]:
    return [_bar(i, o=c, h=c + 0.5, low=c - 0.5, c=c) for i, c in enumerate(closes)]


def _read(bars: list[Bar], indicator_id: str) -> TechnicalRead:
    return technical_read(symbol=_SYMBOL, timeframe=_TF, bars=bars, indicator_id=indicator_id)


# --------------------------------------------------------------------------- #
# Per-rule regime→direction mapping                                           #
# --------------------------------------------------------------------------- #


def test_supertrend_uptrend_returns_long() -> None:
    read = _read(_uptrend(60), "supertrend")
    assert read.direction == "long"
    assert "supertrend" in read.regime_state.lower()
    assert "+1" in read.regime_state  # names the Supertrend direction
    assert read.rationale  # the mechanical rule is stated
    assert read.as_of_bar_ts == _uptrend(60)[-1].event_ts


def test_ema_stack_mixed_returns_flat() -> None:
    # Downtrend (fast < slow) with a last-bar spike that pushes close *above* the
    # fast EMA: long fails (fast !> slow), short fails (close !<= fast) → flat.
    closes = [200.0 - 2.0 * i for i in range(55)]
    closes[-1] = closes[-2] + 25.0
    bars = _bars_from_closes(closes)

    # The fixture must actually exercise the mixed case, not a defined trend.
    ema_s = next(v for v in reversed(ind.ema(closes, 20)) if v is not None)
    ema_l = next(v for v in reversed(ind.ema(closes, 50)) if v is not None)
    assert ema_s < ema_l  # fast below slow
    assert closes[-1] > ema_s  # but close above fast

    assert _read(bars, "ema_stack").direction == "flat"


def test_ema_stack_uptrend_returns_long() -> None:
    assert _read(_uptrend(60), "ema_stack").direction == "long"


def _last_macd_hist(closes: list[float]) -> float:
    mv = next(v for v in reversed(ind.macd(closes)) if v is not None)
    return mv.histogram


def test_macd_maps_positive_histogram_to_long() -> None:
    # Accelerating (convex) rise → MACD line rising above its signal → histogram > 0.
    closes = [100.0 + 0.05 * i * i for i in range(80)]
    assert _last_macd_hist(closes) > 0
    assert _read(_bars_from_closes(closes), "macd").direction == "long"


def test_macd_maps_negative_histogram_to_short() -> None:
    # Accelerating (convex) fall → histogram < 0.
    closes = [5000.0 - 0.05 * i * i for i in range(80)]
    assert _last_macd_hist(closes) < 0
    assert _read(_bars_from_closes(closes), "macd").direction == "short"


@pytest.mark.parametrize("indicator_id", ["supertrend", "ema_stack", "macd", "ichimoku"])
def test_too_short_history_returns_flat(indicator_id: str) -> None:
    # Five bars: below every curated indicator's defined-from index → flat, never a
    # fabricated direction.
    assert _read(_uptrend(5), indicator_id).direction == "flat"


# --------------------------------------------------------------------------- #
# Ichimoku (registered because Plan 0073 phase 1's ichimoku() exists)         #
# --------------------------------------------------------------------------- #


def test_ichimoku_registered() -> None:
    assert "ichimoku" in eligible_indicators()


def test_ichimoku_uptrend_reads_price_above_cloud_long() -> None:
    # 90 bars: the last bar is well above the *displaced* cloud (spans from 26 bars
    # ago, at lower prices) with tenkan > kijun → long.
    read = _read(_uptrend(90), "ichimoku")
    assert read.direction == "long"
    assert "cloud" in read.regime_state.lower()


def test_eligible_indicators_are_the_curated_four() -> None:
    assert set(eligible_indicators()) == {"supertrend", "ema_stack", "macd", "ichimoku"}


# --------------------------------------------------------------------------- #
# Boundary validation & structural honesty                                    #
# --------------------------------------------------------------------------- #


def test_unknown_indicator_id_raises_listing_known_set() -> None:
    with pytest.raises(ValueError, match="unknown indicator_id"):
        _read(_uptrend(60), "rsi")
    # The error names the curated set so the caller can correct the id.
    try:
        _read(_uptrend(60), "rsi")
    except ValueError as exc:
        assert "supertrend" in str(exc)


def test_empty_bars_raises() -> None:
    with pytest.raises(ValueError, match="at least one bar"):
        _read([], "supertrend")


def test_model_rejects_conviction_and_stop_fields() -> None:
    base = {
        "symbol": _SYMBOL,
        "timeframe": _TF,
        "as_of_bar_ts": datetime(2025, 1, 1, tzinfo=UTC),
        "indicator_id": "supertrend",
        "direction": "long",
        "regime_state": "supertrend direction=+1 (uptrend)",
        "rationale": ["supertrend rule: long while direction == +1"],
    }
    # A valid read constructs fine…
    assert TechnicalRead.model_validate(base).direction == "long"
    # …but a conviction or a stop is structurally forbidden (ADR-0068): a thin
    # single-indicator basis can never be dressed as a trade ticket.
    with pytest.raises(ValidationError):
        TechnicalRead.model_validate({**base, "conviction": 0.7})
    with pytest.raises(ValidationError):
        TechnicalRead.model_validate({**base, "stop": 100.0})


# --------------------------------------------------------------------------- #
# Anti-lookahead                                                              #
# --------------------------------------------------------------------------- #


def test_truncation_invariance_no_lookahead() -> None:
    """The read on a prefix equals the full-series indicator read as of the
    truncation bar: the direction technical_read derives from ``bars[:k]`` alone
    matches the Supertrend direction at bar ``k-1`` computed over the *full* series.
    If the call had lookahead (or the indicator weren't trailing) these would
    diverge."""

    full = _uptrend(80)
    st_full = ind.supertrend(full)  # trailing series over all 80 bars
    for k in (30, 45, 60, 80):
        read = _read(full[:k], "supertrend")
        st_k = st_full[k - 1]
        expected = (
            "long"
            if (st_k is not None and st_k.direction == 1)
            else ("short" if st_k is not None else "flat")
        )
        assert read.direction == expected
        assert read.as_of_bar_ts == full[k - 1].event_ts
