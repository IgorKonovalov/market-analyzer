"""Done-when for Plan 0109 phase 1: the unified `scan_watchlist(rank_by=…)` tool (ADR-0104).

The six retired single-mode scanners (`squeeze_scan`, `gainers_losers`, `momentum_scan`,
`quality_rank`, `volume_breakout`, `smart_volume`) fold into one tool with a `rank_by`
discriminator. This file folds each retired tool's standalone assertions into one mode
section, exercising the factored `_scan_watchlist_response` on a single event loop (no
live MCP server). Two extra guards cover the consolidation itself:

- **byte-equivalence** — each mode's ranked `matches`, serialized, equal what the
  *unchanged* underlying compute (`_scan_symbols` with the mode's scorer, or the
  `analysis/volume` condition) produces; the surface refactor changed no computation.
- **discriminated serialization** — the union `matches` list serializes each mode's
  match with that mode's own fields (the response is discriminated by `rank_by`).

One live-MCP-server test covers registration + transport under the new name.
"""

from __future__ import annotations

import asyncio
import json
import re
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterable, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel

from market_analyser.analysis.quality import score_quality
from market_analyser.analysis.scanners import (
    GainersLosersMatch,
    MomentumScanMatch,
    SqueezeScanMatch,
    _scan_symbols,
    score_squeeze,
)
from market_analyser.analysis.types import QualityScore, SmartVolumeHit, VolumeBreakout
from market_analyser.api.app import create_app
from market_analyser.api.mcp_secret import load_or_generate_mcp_secret
from market_analyser.api.mcp_tools.scan_watchlist import (
    MomentumOpts,
    SmartVolumeOpts,
    VolumeBreakoutOpts,
    _scan_watchlist_response,
)
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
from market_analyser.persistence.annotations_repository import AnnotationsRepository
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)

RENDERER_SECRET = "renderer-test-secret"
_END = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _bars(
    symbol: str,
    closes: Sequence[float],
    *,
    volume: float = 100.0,
    volumes: Sequence[float] | None = None,
    hl_pad: float = 0.0,
    highs: Sequence[float] | None = None,
    lows: Sequence[float] | None = None,
) -> list[Bar]:
    """Daily bars ending today from an explicit close series. `hl_pad` brackets the
    close for indicators that need a real OHLC band (ADX/ATR); `volumes`/`highs`/`lows`
    override per-bar when a fixture needs an explicit surge or a cleared level."""

    n = len(closes)
    vol = list(volumes) if volumes is not None else [volume] * n
    hi = list(highs) if highs is not None else [c + hl_pad for c in closes]
    lo = list(lows) if lows is not None else [c - hl_pad for c in closes]
    return [
        Bar(
            symbol=symbol,
            timeframe="1d",
            event_ts=_END - timedelta(days=n - 1 - i),
            open=closes[i],
            high=hi[i],
            low=lo[i],
            close=closes[i],
            volume=vol[i],
            source="fixture",
        )
        for i in range(n)
    ]


def _as[M: BaseModel](matches: Sequence[BaseModel], cls: type[M]) -> list[M]:
    """Narrow a discriminated `matches` list to one mode's concrete match type — the
    response union is homogeneous within a single `rank_by`, so every item is `cls`."""

    result: list[M] = []
    for m in matches:
        assert isinstance(m, cls), f"expected {cls.__name__}, got {type(m).__name__}"
        result.append(m)
    return result


class _SeededProvider:
    """Returns canned bars keyed by `(symbol, timeframe)`, filtered to the window and
    truncated at `as_of`. Symbols in `error_symbols` raise; every non-OHLCV Protocol
    method raises (these scans only read bars)."""

    def __init__(
        self,
        bars_by_key: Mapping[tuple[str, str], Sequence[Bar]],
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


# --------------------------------------------------------------------------- #
# rank_by="squeeze" (was squeeze_scan)                                          #
# --------------------------------------------------------------------------- #


def _volatile_then_flat(symbol: str, *, switch: int = 80, n: int = 100) -> list[Bar]:
    closes = [100.0 + (5.0 if i % 2 == 0 else -5.0) if i < switch else 100.0 for i in range(n)]
    return _bars(symbol, closes)


def _flat_then_volatile(symbol: str, *, switch: int = 80, n: int = 100) -> list[Bar]:
    closes = [100.0 if i < switch else 100.0 + (5.0 if i % 2 == 0 else -5.0) for i in range(n)]
    return _bars(symbol, closes)


def test_squeeze_ranks_tightest_first_and_skips_uncomputable() -> None:
    provider = _SeededProvider(
        {
            ("WIDE", "1d"): _flat_then_volatile("WIDE"),
            ("TIGHT", "1d"): _volatile_then_flat("TIGHT"),
            ("SHORT", "1d"): _bars("SHORT", [100.0] * 10),
        }
    )
    resp = asyncio.run(
        _scan_watchlist_response(
            provider=provider,
            symbols=["WIDE", "TIGHT", "SHORT", "MISSING"],
            timeframe="1d",
            rank_by="squeeze",
            as_of=None,
        )
    )
    assert resp.rank_by == "squeeze"
    matches = _as(resp.matches, SqueezeScanMatch)
    assert [m.symbol for m in matches] == ["TIGHT", "WIDE"]
    assert matches[0].bb_width_pct90 < matches[1].bb_width_pct90
    assert sorted(resp.skipped) == ["MISSING", "SHORT"]
    assert resp.scanned_at.tzinfo is not None


def test_squeeze_is_truncation_invariant() -> None:
    full_wide = _flat_then_volatile("WIDE")
    full_tight = _volatile_then_flat("TIGHT")
    cutoff = full_wide[90].event_ts

    at_t = asyncio.run(
        _scan_watchlist_response(
            provider=_SeededProvider({("WIDE", "1d"): full_wide, ("TIGHT", "1d"): full_tight}),
            symbols=["WIDE", "TIGHT"],
            timeframe="1d",
            rank_by="squeeze",
            as_of=cutoff,
        )
    )
    truncated = asyncio.run(
        _scan_watchlist_response(
            provider=_SeededProvider(
                {
                    ("WIDE", "1d"): [b for b in full_wide if b.event_ts <= cutoff],
                    ("TIGHT", "1d"): [b for b in full_tight if b.event_ts <= cutoff],
                }
            ),
            symbols=["WIDE", "TIGHT"],
            timeframe="1d",
            rank_by="squeeze",
            as_of=None,
        )
    )
    assert at_t.model_dump(exclude={"scanned_at"}) == truncated.model_dump(exclude={"scanned_at"})


def test_squeeze_matches_are_byte_equivalent_to_underlying_scorer() -> None:
    """The surface refactor changed no computation: the ranked squeeze payload equals a
    direct `_scan_symbols` call with the same scorer + sort — the retired tool's body."""

    bars_by_key = {
        ("WIDE", "1d"): _flat_then_volatile("WIDE"),
        ("TIGHT", "1d"): _volatile_then_flat("TIGHT"),
    }
    symbols = ["WIDE", "TIGHT"]
    resp = asyncio.run(
        _scan_watchlist_response(
            provider=_SeededProvider(bars_by_key),
            symbols=symbols,
            timeframe="1d",
            rank_by="squeeze",
            as_of=None,
        )
    )
    direct_matches, direct_skipped, _ = asyncio.run(
        _scan_symbols(
            provider=_SeededProvider(bars_by_key),
            symbols=symbols,
            timeframe="1d",
            score=lambda bars: score_squeeze(bars, "1d"),
            sort_key=lambda m: (m.bb_width_pct90, m.symbol),
            as_of=None,
            tool_name="squeeze_scan",
        )
    )
    assert [m.model_dump() for m in resp.matches] == [m.model_dump() for m in direct_matches]
    assert resp.skipped == direct_skipped
    # Discriminated serialization: each squeeze match carries its own fields.
    assert set(resp.matches[0].model_dump().keys()) == {
        "symbol",
        "bb_width",
        "bb_width_pct90",
        "squeeze_on",
    }


# --------------------------------------------------------------------------- #
# rank_by="gainers" / "losers" (was gainers_losers)                             #
# --------------------------------------------------------------------------- #


def test_gainers_order_sign_and_single_bar_skip() -> None:
    provider = _SeededProvider(
        {
            ("GAIN", "1d"): _bars("GAIN", [100.0, 110.0]),
            ("LOSE", "1d"): _bars("LOSE", [100.0, 90.0]),
            ("SMALL", "1d"): _bars("SMALL", [100.0, 101.0]),
            ("ONEBAR", "1d"): _bars("ONEBAR", [100.0]),
        }
    )
    resp = asyncio.run(
        _scan_watchlist_response(
            provider=provider,
            symbols=["LOSE", "SMALL", "GAIN", "ONEBAR", "MISSING"],
            timeframe="1d",
            rank_by="gainers",
            as_of=None,
        )
    )
    matches = _as(resp.matches, GainersLosersMatch)
    assert [m.symbol for m in matches] == ["GAIN", "SMALL", "LOSE"]
    by_symbol = {m.symbol: m for m in matches}
    assert by_symbol["GAIN"].change_pct == 10.0
    assert by_symbol["GAIN"].direction == "up"
    assert by_symbol["LOSE"].change_pct == -10.0
    assert by_symbol["LOSE"].direction == "down"
    assert sorted(resp.skipped) == ["MISSING", "ONEBAR"]


def test_losers_is_the_mirror_of_gainers() -> None:
    """Same scorer, opposite sort — biggest loser first, biggest gainer last."""

    provider = _SeededProvider(
        {
            ("GAIN", "1d"): _bars("GAIN", [100.0, 110.0]),
            ("LOSE", "1d"): _bars("LOSE", [100.0, 90.0]),
            ("SMALL", "1d"): _bars("SMALL", [100.0, 101.0]),
        }
    )
    resp = asyncio.run(
        _scan_watchlist_response(
            provider=provider,
            symbols=["GAIN", "SMALL", "LOSE"],
            timeframe="1d",
            rank_by="losers",
            as_of=None,
        )
    )
    assert resp.rank_by == "losers"
    assert [m.symbol for m in resp.matches] == ["LOSE", "SMALL", "GAIN"]


def test_gainers_is_no_lookahead() -> None:
    full = _bars("X", [100.0, 110.0, 10.0])
    cutoff = full[1].event_ts
    at_t = asyncio.run(
        _scan_watchlist_response(
            provider=_SeededProvider({("X", "1d"): full}),
            symbols=["X"],
            timeframe="1d",
            rank_by="gainers",
            as_of=cutoff,
        )
    )
    assert [
        (m.symbol, m.change_pct, m.direction) for m in _as(at_t.matches, GainersLosersMatch)
    ] == [("X", 10.0, "up")]


# --------------------------------------------------------------------------- #
# rank_by="momentum" (was momentum_scan)                                        #
# --------------------------------------------------------------------------- #


def _uptrend(symbol: str, n: int = 160) -> list[Bar]:
    return _bars(symbol, [100.0 + i for i in range(n)], volume=1_000_000.0, hl_pad=0.5)


def _downtrend(symbol: str, n: int = 160) -> list[Bar]:
    return _bars(symbol, [100.0 + (n - i) for i in range(n)], volume=1_000_000.0, hl_pad=0.5)


def _oscillating(symbol: str, n: int = 120) -> list[Bar]:
    closes: list[float] = []
    close = 100.0
    for i in range(n):
        close += 1.0 if i % 2 == 0 else -1.0
        closes.append(close)
    return _bars(symbol, closes, hl_pad=0.5)


def test_momentum_band_is_boundary_inclusive() -> None:
    provider = _SeededProvider({("MID", "1d"): _oscillating("MID")})
    wide = asyncio.run(
        _scan_watchlist_response(
            provider=provider,
            symbols=["MID"],
            timeframe="1d",
            rank_by="momentum",
            as_of=None,
        )
    )
    assert [m.symbol for m in wide.matches] == ["MID"]
    match0 = wide.matches[0]
    assert isinstance(match0, MomentumScanMatch)
    rsi = match0.rsi

    exact = asyncio.run(
        _scan_watchlist_response(
            provider=provider,
            symbols=["MID"],
            timeframe="1d",
            rank_by="momentum",
            momentum=MomentumOpts(rsi_min=rsi, rsi_max=rsi),
            as_of=None,
        )
    )
    assert [m.symbol for m in exact.matches] == ["MID"]

    above = asyncio.run(
        _scan_watchlist_response(
            provider=provider,
            symbols=["MID"],
            timeframe="1d",
            rank_by="momentum",
            momentum=MomentumOpts(rsi_min=rsi + 0.01, rsi_max=100.0),
            as_of=None,
        )
    )
    assert above.matches == []


def test_momentum_trend_filter_and_sort() -> None:
    provider = _SeededProvider(
        {
            ("UP", "1d"): _uptrend("UP"),
            ("DOWN", "1d"): _downtrend("DOWN"),
            ("MID", "1d"): _oscillating("MID"),
        }
    )
    everything = asyncio.run(
        _scan_watchlist_response(
            provider=provider,
            symbols=["MID", "DOWN", "UP"],
            timeframe="1d",
            rank_by="momentum",
            as_of=None,
        )
    )
    everything_matches = _as(everything.matches, MomentumScanMatch)
    assert [m.symbol for m in everything_matches] == ["UP", "MID", "DOWN"]
    by_symbol = {m.symbol: m for m in everything_matches}
    assert by_symbol["UP"].trend == "up"
    assert by_symbol["DOWN"].trend == "down"

    up_only = asyncio.run(
        _scan_watchlist_response(
            provider=provider,
            symbols=["MID", "DOWN", "UP"],
            timeframe="1d",
            rank_by="momentum",
            momentum=MomentumOpts(trend="up"),
            as_of=None,
        )
    )
    assert [m.symbol for m in up_only.matches] == ["UP"]
    assert up_only.skipped == []


def test_momentum_opts_validation() -> None:
    provider = _SeededProvider({})
    for opts in (MomentumOpts(rsi_min=70.0, rsi_max=30.0), MomentumOpts(trend="up_and_right")):
        try:
            asyncio.run(
                _scan_watchlist_response(
                    provider=provider,
                    symbols=["A"],
                    timeframe="1d",
                    rank_by="momentum",
                    momentum=opts,
                    as_of=None,
                )
            )
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {opts!r}")


# --------------------------------------------------------------------------- #
# rank_by="quality" (was quality_rank)                                          #
# --------------------------------------------------------------------------- #


def test_quality_orders_by_score_descending_and_skips() -> None:
    provider = _SeededProvider(
        {
            ("UP", "1d"): _uptrend("UP"),
            ("DOWN", "1d"): _downtrend("DOWN"),
            ("SHORT", "1d"): _bars("SHORT", [100.0, 101.0], volume=1_000_000.0, hl_pad=0.5),
        }
    )
    resp = asyncio.run(
        _scan_watchlist_response(
            provider=provider,
            symbols=["DOWN", "UP", "SHORT", "MISSING"],
            timeframe="1d",
            rank_by="quality",
            as_of=None,
        )
    )
    matches = _as(resp.matches, QualityScore)
    assert [m.symbol for m in matches] == ["UP", "DOWN"]
    assert matches[0].score > matches[1].score
    assert sorted(resp.skipped) == ["MISSING", "SHORT"]


def test_quality_matches_are_byte_equivalent_to_underlying_scorer() -> None:
    bars_by_key = {("UP", "1d"): _uptrend("UP"), ("DOWN", "1d"): _downtrend("DOWN")}
    symbols = ["UP", "DOWN"]
    resp = asyncio.run(
        _scan_watchlist_response(
            provider=_SeededProvider(bars_by_key),
            symbols=symbols,
            timeframe="1d",
            rank_by="quality",
            as_of=None,
        )
    )
    direct_matches, _, _ = asyncio.run(
        _scan_symbols(
            provider=_SeededProvider(bars_by_key),
            symbols=symbols,
            timeframe="1d",
            score=lambda bars: score_quality(bars, "1d"),
            sort_key=lambda m: (-m.score, m.symbol),
            as_of=None,
            tool_name="quality_rank",
        )
    )
    assert [m.model_dump() for m in resp.matches] == [m.model_dump() for m in direct_matches]


def test_quality_response_carries_no_call_shaped_key() -> None:
    provider = _SeededProvider({("UP", "1d"): _uptrend("UP")})
    resp = asyncio.run(
        _scan_watchlist_response(
            provider=provider,
            symbols=["UP"],
            timeframe="1d",
            rank_by="quality",
            as_of=None,
        )
    )
    blob = json.dumps(resp.model_dump(mode="json")).lower()
    for token in ("buy", "sell", "short", "hold", "action", "grade", "conviction", "entry", "stop"):
        assert not re.search(rf"\b{token}\b", blob), f"call-shaped token {token!r} leaked"
    fields = set(resp.matches[0].model_dump().keys())
    for forbidden in ("action", "signal", "recommendation", "grade", "direction"):
        assert forbidden not in fields


# --------------------------------------------------------------------------- #
# rank_by="volume_breakout" (was volume_breakout)                               #
# --------------------------------------------------------------------------- #


def _breakout(symbol: str, *, last_volume: float) -> list[Bar]:
    closes = [100.0] * 20 + [110.0]
    volumes = [100.0] * 20 + [last_volume]
    highs = [101.0] * 20 + [111.0]
    lows = [99.0] * 20 + [109.0]
    return _bars(symbol, closes, volumes=volumes, highs=highs, lows=lows)


def _drift(symbol: str) -> list[Bar]:
    return _bars(
        symbol,
        [100.0] * 20 + [100.5],
        volumes=[100.0] * 21,
        highs=[101.0] * 21,
        lows=[99.0] * 21,
    )


def test_volume_breakout_returns_only_breakouts_sorted() -> None:
    provider = _SeededProvider(
        {
            ("A", "1d"): _breakout("A", last_volume=300.0),
            ("B", "1d"): _drift("B"),
            ("C", "1d"): _breakout("C", last_volume=400.0),
        }
    )
    resp = asyncio.run(
        _scan_watchlist_response(
            provider=provider,
            symbols=["A", "B", "C"],
            timeframe="1d",
            rank_by="volume_breakout",
            as_of=None,
        )
    )
    matches = _as(resp.matches, VolumeBreakout)
    assert [m.symbol for m in matches] == ["C", "A"]
    for m in matches:
        assert m.is_breakout is True
        assert m.direction == "bullish"
        assert m.broken_level == 101.0
    assert resp.skipped == []


def test_volume_breakout_skips_missing_and_failed() -> None:
    provider = _SeededProvider(
        {("A", "1d"): _breakout("A", last_volume=300.0)},
        error_symbols={"BOOM"},
    )
    resp = asyncio.run(
        _scan_watchlist_response(
            provider=provider,
            symbols=["A", "MISSING", "BOOM"],
            timeframe="1d",
            rank_by="volume_breakout",
            as_of=None,
        )
    )
    assert [m.symbol for m in resp.matches] == ["A"]
    assert sorted(resp.skipped) == ["BOOM", "MISSING"]


def test_volume_breakout_honours_opts() -> None:
    """A very high `vol_multiple` threshold drops an otherwise-breaking symbol."""

    provider = _SeededProvider({("A", "1d"): _breakout("A", last_volume=300.0)})
    resp = asyncio.run(
        _scan_watchlist_response(
            provider=provider,
            symbols=["A"],
            timeframe="1d",
            rank_by="volume_breakout",
            volume_breakout=VolumeBreakoutOpts(vol_multiple=100.0),
            as_of=None,
        )
    )
    assert resp.matches == []


# --------------------------------------------------------------------------- #
# rank_by="smart_volume" (was smart_volume)                                     #
# --------------------------------------------------------------------------- #


def _surge_oscillating(symbol: str, *, last_volume: float, n: int = 30) -> list[Bar]:
    closes: list[float] = []
    close = 100.0
    for i in range(n):
        close += 1.0 if i % 2 == 0 else -1.0
        closes.append(close)
    volumes = [last_volume if i == n - 1 else 100.0 for i in range(n)]
    return _bars(symbol, closes, volumes=volumes)


def _surge_uptrend(symbol: str, *, last_volume: float, n: int = 30) -> list[Bar]:
    closes = [100.0 + i for i in range(n)]
    volumes = [last_volume if i == n - 1 else 100.0 for i in range(n)]
    return _bars(symbol, closes, volumes=volumes)


def test_smart_volume_returns_only_qualifying() -> None:
    provider = _SeededProvider(
        {
            ("A", "1d"): _surge_oscillating("A", last_volume=200.0),  # surge + RSI in band
            ("B", "1d"): _surge_uptrend("B", last_volume=200.0),  # surge but RSI above band
        }
    )
    resp = asyncio.run(
        _scan_watchlist_response(
            provider=provider,
            symbols=["A", "B"],
            timeframe="1d",
            rank_by="smart_volume",
            as_of=None,
        )
    )
    matches = _as(resp.matches, SmartVolumeHit)
    assert [m.symbol for m in matches] == ["A"]
    assert matches[0].qualifies is True
    assert resp.skipped == []


def test_smart_volume_rejects_bad_band() -> None:
    provider = _SeededProvider({})
    with pytest.raises(ValueError):
        asyncio.run(
            _scan_watchlist_response(
                provider=provider,
                symbols=["A"],
                timeframe="1d",
                rank_by="smart_volume",
                smart_volume=SmartVolumeOpts(rsi_low=70.0, rsi_high=30.0),
                as_of=None,
            )
        )


# --------------------------------------------------------------------------- #
# Shared boundary validation (the _scan_symbols cap/timeframe guard)            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("symbols", "timeframe"),
    [
        ([], "1d"),
        (["A", "B"], "5m"),
        ([f"S{i}" for i in range(26)], "1d"),
    ],
)
def test_scan_watchlist_boundary_validation(symbols: list[str], timeframe: str) -> None:
    provider = _SeededProvider({})
    with pytest.raises(ValueError):
        asyncio.run(
            _scan_watchlist_response(
                provider=provider,
                symbols=symbols,
                timeframe=timeframe,
                rank_by="squeeze",
                as_of=None,
            )
        )


# --------------------------------------------------------------------------- #
# Live MCP server: registration + transport under the new name                  #
# --------------------------------------------------------------------------- #


@pytest.fixture
def mcp_secret_path(tmp_path: Path) -> Path:
    return tmp_path / "mcp-secret.json"


@pytest.fixture
def mcp_secret(mcp_secret_path: Path) -> str:
    return load_or_generate_mcp_secret(mcp_secret_path)


@pytest.fixture
def annotations_repo() -> Iterator[AnnotationsRepository]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield AnnotationsRepository(make_session_factory(engine))
    engine.dispose()


@pytest.fixture
def app(mcp_secret: str, annotations_repo: AnnotationsRepository) -> FastAPI:
    provider = _SeededProvider(
        {
            ("A", "1d"): _breakout("A", last_volume=300.0),
            ("TIGHT", "1d"): _volatile_then_flat("TIGHT"),
        }
    )
    return create_app(
        secret=RENDERER_SECRET,
        mcp_secret=mcp_secret,
        provider=provider,
        annotations_repository=annotations_repo,
    )


@pytest.fixture
def live_server(app: FastAPI) -> Iterator[str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    config = uvicorn.Config(app, log_level="error", access_log=False)
    server = uvicorn.Server(config)

    def _run() -> None:
        asyncio.run(server.serve(sockets=[sock]))

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=2)
        raise RuntimeError("uvicorn server failed to start within 5s")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)


@asynccontextmanager
async def _mcp_session(url: str, bearer: str) -> AsyncIterator[ClientSession]:
    async with (
        httpx.AsyncClient(
            headers={"Authorization": f"Bearer {bearer}"},
            timeout=httpx.Timeout(30.0),
        ) as http_client,
        streamable_http_client(f"{url}/mcp", http_client=http_client) as (read, write, _sid),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


def test_scan_watchlist_registered_and_callable(live_server: str, mcp_secret: str) -> None:
    """`scan_watchlist` is registered under its documented name; the six retired
    scanner names are gone from the surface; a per-mode call returns a discriminated
    payload over the real MCP transport."""

    async def _run() -> None:
        async with _mcp_session(live_server, mcp_secret) as session:
            listed = {t.name for t in (await session.list_tools()).tools}
            assert "scan_watchlist" in listed
            assert listed.isdisjoint(
                {
                    "squeeze_scan",
                    "gainers_losers",
                    "momentum_scan",
                    "quality_rank",
                    "volume_breakout",
                    "smart_volume",
                }
            )

            breakout = await session.call_tool(
                "scan_watchlist",
                {"symbols": ["A"], "timeframe": "1d", "rank_by": "volume_breakout"},
            )
            squeeze = await session.call_tool(
                "scan_watchlist",
                {"symbols": ["TIGHT"], "timeframe": "1d", "rank_by": "squeeze"},
            )
            for result in (breakout, squeeze):
                assert not result.isError, f"tool errored: {result.content}"
                assert result.structuredContent is not None

            breakout_sc = breakout.structuredContent
            assert breakout_sc is not None
            assert breakout_sc["rank_by"] == "volume_breakout"
            assert {m["symbol"] for m in breakout_sc["matches"]} == {"A"}
            assert breakout_sc["scanned_at"]

    asyncio.run(_run())
