"""Done-when for Plan 0102 phase 3: the `sector_rotation` MCP tool (ADR-0097).

Exercises the factored `_sector_rotation_response` on a single event loop with an
injected fixture taxonomy (no live MCP server): the tool ranks sectors by momentum
descending, reports leaders + skipped + `scanned_at`, honours `as_of`, validates its
inputs, and — the conditions-only guarantee (ADR-0029) — carries no call-shaped key
anywhere in the response. Registration + `EXPECTED_FULL_TOOLSET` membership are pinned
by `test_mcp_tools.py::test_full_toolset_registration_is_exhaustive`.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta

import pytest

from market_analyser.analysis.sector_taxonomy import SectorTaxonomy, load_taxonomy
from market_analyser.api.mcp_tools.sector_rotation import (
    DEFAULT_LOOKBACK,
    SectorRotationResponse,
    _sector_rotation_response,
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

_END = datetime.now(tz=UTC).replace(hour=0, minute=0, second=0, microsecond=0)
_LOOKBACK = 2


def _series(symbol: str, closes: Sequence[float]) -> list[Bar]:
    n = len(closes)
    return [
        Bar(
            symbol=symbol,
            timeframe="1d",
            event_ts=_END - timedelta(days=n - 1 - i),
            open=closes[i],
            high=closes[i],
            low=closes[i],
            close=closes[i],
            volume=100.0,
            source="fixture",
        )
        for i in range(n)
    ]


def _ret(symbol: str, return_pct: float) -> list[Bar]:
    return _series(symbol, [100.0, 100.0, 100.0 + return_pct])


class _SeededProvider:
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


def _fixture_taxonomy() -> SectorTaxonomy:
    return load_taxonomy(
        "fixture-2026",
        (
            ("Hot", ("HOTA", "HOTB")),
            ("Cold", ("COLDA", "COLDB")),
            ("Thin", ("THINA", "THINMISS")),
        ),
    )


def _fixture_provider() -> _SeededProvider:
    return _SeededProvider(
        {
            ("HOTA", "1d"): _ret("HOTA", 20.0),
            ("HOTB", "1d"): _ret("HOTB", 10.0),
            ("COLDA", "1d"): _ret("COLDA", -5.0),
            ("COLDB", "1d"): _ret("COLDB", -15.0),
            ("THINA", "1d"): _ret("THINA", 3.0),
            # THINMISS: no cached bars -> skipped.
        }
    )


def _run() -> SectorRotationResponse:
    return asyncio.run(
        _sector_rotation_response(
            provider=_fixture_provider(),
            taxonomy=_fixture_taxonomy(),
            timeframe="1d",
            lookback=_LOOKBACK,
            as_of=None,
        )
    )


def test_ranks_sectors_by_momentum_descending() -> None:
    resp = _run()
    assert resp.taxonomy_version == "fixture-2026"
    assert resp.timeframe == "1d"
    assert resp.lookback == _LOOKBACK
    # Complete sectors first by momentum descending (Hot +15, Cold -10), then the
    # incomplete Thin (one priced constituent, below the >=2 floor).
    assert [s.sector for s in resp.sectors] == ["Hot", "Cold", "Thin"]
    hot = resp.sectors[0]
    assert hot.momentum == 15.0
    assert hot.complete is True
    assert resp.scanned_at.tzinfo is not None


def test_reports_leaders_and_skipped() -> None:
    resp = _run()
    by_name = {s.sector: s for s in resp.sectors}
    hot = by_name["Hot"]
    assert [c.symbol for c in hot.leaders] == ["HOTA"]  # +20 leads +10
    assert [c.symbol for c in hot.laggards] == ["HOTB"]
    thin = by_name["Thin"]
    assert thin.skipped == ["THINMISS"]
    assert thin.complete is False


def test_honours_as_of() -> None:
    """A future bar that would change a constituent's return must not leak in when
    `as_of` predates it."""

    taxonomy = load_taxonomy("t", (("S", ("A", "B")),))
    full_a = _series("A", [100.0, 100.0, 120.0, 999.0])  # to cutoff: +20%
    full_b = _series("B", [100.0, 100.0, 110.0, 1.0])  # to cutoff: +10%
    cutoff = full_a[2].event_ts
    resp = asyncio.run(
        _sector_rotation_response(
            provider=_SeededProvider({("A", "1d"): full_a, ("B", "1d"): full_b}),
            taxonomy=taxonomy,
            timeframe="1d",
            lookback=_LOOKBACK,
            as_of=cutoff,
        )
    )
    assert resp.sectors[0].momentum == 15.0  # mean(+20, +10), not contaminated


def test_rejects_bad_timeframe_and_lookback() -> None:
    with pytest.raises(ValueError):
        asyncio.run(
            _sector_rotation_response(
                provider=_fixture_provider(),
                taxonomy=_fixture_taxonomy(),
                timeframe="5m",  # not a supported timeframe
                lookback=_LOOKBACK,
                as_of=None,
            )
        )
    with pytest.raises(ValueError):
        asyncio.run(
            _sector_rotation_response(
                provider=_fixture_provider(),
                taxonomy=_fixture_taxonomy(),
                timeframe="1d",
                lookback=0,  # must be >= 1
                as_of=None,
            )
        )


def test_default_lookback_is_thirty() -> None:
    assert DEFAULT_LOOKBACK == 30


def test_response_carries_no_call_shaped_key() -> None:
    """Conditions only (ADR-0029): no call-shaped token anywhere in the serialized
    response, and no call-shaped field on the response or any sector."""

    resp = _run()
    blob = json.dumps(resp.model_dump(mode="json")).lower()
    for token in (
        "buy",
        "sell",
        "short",
        "long",
        "hold",
        "action",
        "signal",
        "recommendation",
        "conviction",
        "entry",
        "stop",
        "target",
    ):
        assert not re.search(rf"\b{token}\b", blob), f"call-shaped token {token!r} leaked"
    response_fields = set(resp.model_dump().keys())
    for forbidden in ("action", "signal", "recommendation", "buy", "sell"):
        assert forbidden not in response_fields
    sector_fields = set(resp.sectors[0].model_dump().keys())
    for forbidden in ("action", "signal", "recommendation", "buy", "sell", "direction"):
        assert forbidden not in sector_fields
