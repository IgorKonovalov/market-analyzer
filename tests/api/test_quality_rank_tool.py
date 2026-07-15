"""Phase-2 done-when for Plan 0101: the `quality_rank` watchlist tool (ADR-0096).

Exercises the factored `_quality_rank_response` on a single event loop: the whole
watchlist is ranked by composite score descending (highest-quality setup first);
symbols with too short a history to score (or no cached bars / a fetch error) are
skipped; `as_of` truncates the read (no future leak); and the response carries no
call-shaped key (the ADR-0029 conditions-side guard).

Fixtures: a clean uptrend (high quality), a clean downtrend (low quality), a short
history (skipped), and a missing symbol (skipped).
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta

from market_analyser.api.mcp_tools.quality_rank import _quality_rank_response
from market_analyser.data.types import (
    Bar,
    MacroContext,
    MarketSentimentSample,
    NewsItem,
    Quote,
    ScreenerRow,
    SentimentSample,
    SymbolInfo,
)

_END = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _bars(symbol: str, closes: Sequence[float], *, volume: float = 1_000_000.0) -> list[Bar]:
    n = len(closes)
    return [
        Bar(
            symbol=symbol,
            timeframe="1d",
            event_ts=_END - timedelta(days=n - 1 - i),
            open=closes[i],
            high=closes[i] + 0.5,
            low=closes[i] - 0.5,
            close=closes[i],
            volume=volume,
            source="fixture",
        )
        for i in range(n)
    ]


def _uptrend(symbol: str, n: int = 160) -> list[Bar]:
    return _bars(symbol, [100.0 + i for i in range(n)])


def _downtrend(symbol: str, n: int = 160) -> list[Bar]:
    return _bars(symbol, [100.0 + (n - i) for i in range(n)])


class _SeededProvider:
    """Returns canned bars keyed by `(symbol, timeframe)`, filtered to the window
    and truncated at `as_of`. Symbols in `error_symbols` raise; every non-OHLCV
    Protocol method raises (this tool only reads bars)."""

    def __init__(
        self,
        bars_by_key: dict[tuple[str, str], Sequence[Bar]],
        error_symbols: Iterable[str] = (),
    ) -> None:
        self._by_key = {k: list(v) for k, v in bars_by_key.items()}
        self._errors = set(error_symbols)

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        if symbol in self._errors:
            raise RuntimeError(f"simulated fetch failure for {symbol}")
        bars = [b for b in self._by_key.get((symbol, timeframe), []) if start <= b.event_ts <= end]
        if as_of is not None:
            bars = [b for b in bars if b.event_ts <= as_of]
        return bars

    def get_quote(self, symbol: str, as_of: datetime | None = None) -> Quote:
        raise NotImplementedError

    def search_symbols(self, query: str, as_of: datetime | None = None) -> Sequence[SymbolInfo]:
        raise NotImplementedError

    def get_screener(
        self,
        filters: dict[str, str | float | None],
        market: str = "america",
        exchange: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
    ) -> Sequence[ScreenerRow]:
        raise NotImplementedError

    def get_sentiment(
        self, symbol: str, window: str, source: str = "rss-vader", as_of: datetime | None = None
    ) -> SentimentSample:
        raise NotImplementedError

    def get_market_sentiment(
        self, market: str, window: str = "current", as_of: datetime | None = None
    ) -> MarketSentimentSample:
        raise NotImplementedError

    def get_macro_context(
        self, market: str = "crypto", as_of: datetime | None = None
    ) -> MacroContext:
        raise NotImplementedError

    def get_news(
        self,
        symbol: str | None = None,
        window: str = "24h",
        limit: int = 50,
        with_sentiment: bool = False,
        as_of: datetime | None = None,
    ) -> Sequence[NewsItem]:
        raise NotImplementedError


def test_quality_rank_orders_by_score_descending_and_skips() -> None:
    provider = _SeededProvider(
        {
            ("UP", "1d"): _uptrend("UP"),
            ("DOWN", "1d"): _downtrend("DOWN"),
            ("SHORT", "1d"): _bars("SHORT", [100.0, 101.0]),  # too few bars to score
        }
    )
    resp = asyncio.run(
        _quality_rank_response(
            provider=provider,
            symbols=["DOWN", "UP", "SHORT", "MISSING"],
            timeframe="1d",
            as_of=None,
        )
    )

    # The whole scorable watchlist is ranked by score descending — the uptrend (a
    # higher-quality setup) ranks above the downtrend.
    assert [m.symbol for m in resp.matches] == ["UP", "DOWN"]
    assert resp.matches[0].score > resp.matches[1].score
    # Uncomputable / absent symbols are skipped, never fail the scan.
    assert sorted(resp.skipped) == ["MISSING", "SHORT"]


def test_quality_rank_is_no_lookahead() -> None:
    """A scan at `as_of=t` reads only bars[..t]: it equals the same scan on a
    provider whose bars are already truncated to `t` (no future leak)."""

    full = _uptrend("UP")
    cutoff = full[120].event_ts

    at_t = asyncio.run(
        _quality_rank_response(
            provider=_SeededProvider({("UP", "1d"): full}),
            symbols=["UP"],
            timeframe="1d",
            as_of=cutoff,
        )
    )
    truncated = asyncio.run(
        _quality_rank_response(
            provider=_SeededProvider({("UP", "1d"): [b for b in full if b.event_ts <= cutoff]}),
            symbols=["UP"],
            timeframe="1d",
            as_of=None,
        )
    )

    assert at_t.matches  # the uptrend actually scored at t
    assert [(m.symbol, m.score, m.factors) for m in at_t.matches] == [
        (m.symbol, m.score, m.factors) for m in truncated.matches
    ]


def test_quality_rank_boundary_validation() -> None:
    provider = _SeededProvider({})
    bad_calls: list[dict[str, object]] = [
        {"symbols": [], "timeframe": "1d"},  # empty
        {"symbols": ["A", "B"], "timeframe": "5m"},  # unsupported timeframe
        {"symbols": [f"S{i}" for i in range(26)], "timeframe": "1d"},  # over the cap
    ]
    for kw in bad_calls:
        try:
            asyncio.run(_quality_rank_response(provider=provider, as_of=None, **kw))  # type: ignore[arg-type]
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {kw!r}")


def test_quality_rank_response_carries_no_call_shaped_key() -> None:
    provider = _SeededProvider({("UP", "1d"): _uptrend("UP")})
    resp = asyncio.run(
        _quality_rank_response(provider=provider, symbols=["UP"], timeframe="1d", as_of=None)
    )
    blob = json.dumps(resp.model_dump(mode="json")).lower()
    for token in (
        "recommend",
        "buy",
        "sell",
        "short",
        "hold",
        "action",
        "grade",
        "conviction",
        "entry",
        "stop",
        "target",
        "should",
    ):
        assert not re.search(rf"\b{token}\b", blob), f"call-shaped token {token!r} leaked"
    # And no call-shaped field on the per-symbol match model.
    fields = set(resp.matches[0].model_dump().keys())
    for forbidden in ("action", "signal", "recommendation", "grade", "direction"):
        assert forbidden not in fields
