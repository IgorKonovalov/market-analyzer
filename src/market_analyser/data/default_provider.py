"""`DefaultMarketDataProvider` — the production implementation of the Protocol.

Phase 3 wires the cache: `get_ohlcv` reads from the `BarRepository`, computes
the gaps between the cached coverage and the requested `[start, end]` window,
and asks the adapter only for those gaps before returning the merged result.

With `as_of` set (backtest mode), the cache is the sole source of truth: any
gap in coverage is a hard error, never a silent remote fetch. This is the
anti-lookahead seam declared in ADR-0007.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any, Literal

from market_analyser.data.adapters.binance_klines import BinanceKlinesAdapter
from market_analyser.data.adapters.coingecko import CoinGeckoAdapter
from market_analyser.data.adapters.crypto_fear_greed import CryptoFearGreedAdapter
from market_analyser.data.adapters.rss_news import RssNewsAdapter
from market_analyser.data.adapters.rss_vader_sentiment import RssVaderSentimentAdapter
from market_analyser.data.adapters.stocktwits import StockTwitsAdapter
from market_analyser.data.adapters.tradingview_screener import TradingViewScreenerAdapter
from market_analyser.data.adapters.yahoo import YahooAdapter
from market_analyser.data.adapters.yahoo_quote import YahooQuoteAdapter
from market_analyser.data.errors import (
    HistoryExceededError,
    UpstreamDataError,
    failure_reason,
)
from market_analyser.data.resample import resample_ohlcv
from market_analyser.data.sources import (
    MarketSentimentSource,
    OhlcvSource,
    SentimentSource,
)
from market_analyser.data.timeframes import (
    OhlcvSourceName,
    bar_duration,
    max_history,
    source_max_history,
    source_resampled_from,
)
from market_analyser.data.types import (
    BackfillResult,
    Bar,
    Coverage,
    MacroContext,
    MarketSentimentSample,
    NewsItem,
    Quote,
    ScreenerRow,
    SentimentSample,
    SymbolInfo,
)
from market_analyser.persistence.repository import BarRepository

# A between-bars gap counts as a real hole (worth a fetch) only when it spans
# more than this many bars of the timeframe's own cadence; below it the gap is a
# legitimate closure (weekend, holiday, overnight) and is skipped. Edge gaps below
# this width are still fetched but widened to at least this span so each fetch
# justifies a round-trip — Yahoo picks the smallest period that fits anyway, so a
# 1-bar window would over-fetch and discard the rest. Scaling by the registry bar
# duration (Plan 0025) makes the threshold cadence-correct: 10 daily bars = 10
# days preserves the Plan 0004 baseline for 1d, while 10 fifteen-minute bars =
# 2.5h lets an intraday hole surface that a flat 10-day floor would have masked.
_GAP_THRESHOLD_BARS = 10

# Timeframes at or above this bar duration (monthly and coarser) have NO
# within-cadence market closures — every period reliably has exactly one bar — so
# the x10 closure tolerance does not apply. 28 days is below the shortest month
# (so it captures `1mo`) and above the longest sub-monthly cadence (`1w` = 7d), so
# only month-scale timeframes take the tight branch.
_MONTH_SCALE = timedelta(days=28)


def _min_fetch_span(timeframe: str) -> timedelta:
    """The fetch/gap-detection threshold for `timeframe`, derived from its registry
    bar duration.

    Sub-monthly timeframes scale by `_GAP_THRESHOLD_BARS` to tolerate within-
    cadence closures (weekends, holidays, overnight). Month-scale timeframes have
    no such closures, so the x10 tolerance (310d for `1mo`) would mask a genuinely
    missing month; they instead use a tight `1.5 * bar_duration` (~46.5d for
    `1mo`), which reads `bar_duration` as the max adjacent spacing (ADR-0047): a
    28-31d month-to-month step stays below it (no false February gap), while a
    ~59d two-month hole exceeds it and is flagged as missing."""
    dur = bar_duration(timeframe)
    if dur >= _MONTH_SCALE:
        return dur + dur // 2
    return dur * _GAP_THRESHOLD_BARS


def _exceeds_history_cap(
    timeframe: str, start: datetime, end: datetime, source: OhlcvSourceName
) -> bool:
    """Whether `[start, end]` reaches further back than `timeframe`'s
    `max_history` cap *for the routed source* (Plan 0058 phase 2: Yahoo's
    intraday caps come from the registry; Binance is uncapped — history to
    listing date). Span-based and deterministic (no wall-clock)."""
    cap = source_max_history(timeframe, source)
    return cap is not None and (end - start) > cap


def _history_cap_message(timeframe: str, start: datetime, end: datetime) -> str:
    cap = max_history(timeframe)
    cap_days = cap.days if cap is not None else 0
    return (
        f"requested {timeframe} window spans {(end - start).days}d but Yahoo serves "
        f"only ~{cap_days}d of {timeframe} history — narrow the window or use a "
        "coarser timeframe"
    )


class DefaultMarketDataProvider:
    """Dispatches across per-source adapters with optional cache. See ADR-0007."""

    def __init__(
        self,
        *,
        yahoo: YahooAdapter | None = None,
        yahoo_quote: YahooQuoteAdapter | None = None,
        screener: TradingViewScreenerAdapter | None = None,
        news: RssNewsAdapter | None = None,
        crypto_fng: CryptoFearGreedAdapter | None = None,
        coingecko: CoinGeckoAdapter | None = None,
        stocktwits: StockTwitsAdapter | None = None,
        binance: BinanceKlinesAdapter | None = None,
        bar_repository: BarRepository | None = None,
    ) -> None:
        self._yahoo = yahoo if yahoo is not None else YahooAdapter()
        # Deliberately NOT default-constructed (unlike the adapters above):
        # the membership check may lazily fetch exchangeInfo over the network,
        # so only the composition root (api/app.py) wires the real adapter —
        # an unwired provider routes everything to Yahoo, exactly as before
        # Plan 0058, and offline tests never reach api.binance.com.
        self._binance = binance
        self._yahoo_quote = yahoo_quote if yahoo_quote is not None else YahooQuoteAdapter()
        self._screener = screener if screener is not None else TradingViewScreenerAdapter()
        self._news = news if news is not None else RssNewsAdapter()
        self._crypto_fng = crypto_fng if crypto_fng is not None else CryptoFearGreedAdapter()
        self._coingecko = coingecko if coingecko is not None else CoinGeckoAdapter()
        self._stocktwits = stocktwits if stocktwits is not None else StockTwitsAdapter()
        self._repo = bar_repository

        # Selector registries (ADR-0031): adding a sentiment source or a
        # market-sentiment market is one dict entry, not a dispatch-body branch.
        # Plain dict literals (no set iteration) keep the dispatch deterministic.
        # The rss-vader adapter reads its `as_of` through this module's `_now`
        # (resolved at call time) so the provider stays the single owner of that
        # determinism seam — tests freeze `default_provider._now` and the sample
        # observes the frozen value.
        self._sentiment_sources: dict[str, SentimentSource] = {
            "rss-vader": RssVaderSentimentAdapter(self._news, now=lambda: _now()),
            "stocktwits": self._stocktwits,
        }
        self._market_sentiment_sources: dict[str, MarketSentimentSource] = {
            "crypto": self._crypto_fng,
        }

    def _ohlcv_route(self, symbol: str) -> tuple[OhlcvSourceName, OhlcvSource]:
        """ADR-0052 membership routing: a symbol goes to Binance iff it is in
        the cached `exchangeInfo` symbol set (no prefixes, no format
        heuristics); everything else goes to Yahoo, exactly as before Plan
        0058. A symbol therefore resolves to one source for its whole life and
        cached bars stay single-provenance by construction."""
        if self._binance is not None and self._binance.is_known_symbol(symbol):
            return "binance", self._binance
        return "yahoo", self._yahoo

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        as_of: datetime | None = None,
    ) -> Sequence[Bar]:
        source, adapter = self._ohlcv_route(symbol)

        # Source-derived timeframe (Yahoo 4h): fetch the native base (1h) over
        # the same window through the normal cache/gap/as_of path, then resample
        # on read (ADR-0028 derive-on-read); the base call inherits the
        # anti-lookahead `as_of` guard, and the resample is trailing, so
        # historical replay stays leak-free. Binance serves 4h natively
        # (Plan 0058 phase 2), so Binance-routed symbols never take this branch
        # — their 4h bars are fetched and cached as 4h.
        base = source_resampled_from(timeframe, source)
        if base is not None:
            base_bars = self.get_ohlcv(symbol, base, start, end, as_of)
            return resample_ohlcv(list(base_bars), target=timeframe)

        # History cap (per source): a window beyond what the routed source
        # serves for this timeframe is a typed, non-retryable failure here (the
        # fail-loud path); get_ohlcv_with_status surfaces it as a partial_reason
        # instead. Checked before the cache so a doomed fetch is never attempted
        # (Plan 0025 ph3 / ADR-0028). Binance is uncapped (Plan 0058 phase 2).
        if _exceeds_history_cap(timeframe, start, end, source):
            raise HistoryExceededError(_history_cap_message(timeframe, start, end))

        # No cache wired: live-only (phase-2 fallback for tests that don't need persistence).
        if self._repo is None:
            if as_of is not None:
                raise ValueError(
                    "as_of requires a configured BarRepository — no remote fetch when as_of is set",
                )
            return adapter.fetch_ohlcv(symbol, timeframe, start, end, now=_now())

        if as_of is not None:
            cached = self._repo.get_bars(symbol, timeframe, start, end, as_of=as_of)
            gaps = _coverage_gaps(cached, start, end, _min_fetch_span(timeframe))
            if gaps:
                raise ValueError(
                    f"as_of={as_of.isoformat()}: cached coverage incomplete for "
                    f"[{start.isoformat()}, {end.isoformat()}] — "
                    f"refusing remote fetch (anti-lookahead)",
                )
            return cached

        cached = self._repo.get_bars(symbol, timeframe, start, end)
        gaps = _coverage_gaps(cached, start, end, _min_fetch_span(timeframe))
        if not gaps:
            return cached
        merged: dict[datetime, Bar] = {bar.event_ts: bar for bar in cached}
        for gap_start, gap_end in gaps:
            fetched = adapter.fetch_ohlcv(symbol, timeframe, gap_start, gap_end, now=_now())
            if not fetched:
                continue
            self._repo.upsert_bars(list(fetched))
            for bar in fetched:
                if start <= bar.event_ts <= end:
                    merged[bar.event_ts] = bar
        return sorted(merged.values(), key=lambda b: b.event_ts)

    def coverage(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> Coverage:
        """Cache-only read: the bars currently cached for ``[start, end]`` plus the
        gaps still needed to cover it — WITHOUT any upstream fetch. Backfill
        scheduling (Plan 0013) calls this to decide whether a fetch is needed and
        which windows to fetch; it reuses the same `_coverage_gaps` math as
        `get_ohlcv` but never reaches the adapter. With no cache wired, the whole
        window is one gap (and nothing is cached).

        For a source-derived timeframe (Yahoo 4h) coverage is reported against
        its native base (1h): 4h is never cached for Yahoo symbols, so its cache
        state IS the base's. Scheduling a 4h backfill therefore fills the 1h
        base, which the 4h read resamples. Binance-routed symbols cache 4h
        natively (Plan 0058 phase 2), so their 4h coverage is their own."""
        source, _ = self._ohlcv_route(symbol)
        base = source_resampled_from(timeframe, source)
        if base is not None:
            return self.coverage(symbol, base, start, end)
        if self._repo is None:
            return Coverage(cached=[], gaps=[(start, end)] if start < end else [])
        cached = list(self._repo.get_bars(symbol, timeframe, start, end))
        return Coverage(
            cached=cached,
            gaps=_coverage_gaps(cached, start, end, _min_fetch_span(timeframe)),
        )

    def get_ohlcv_with_status(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
    ) -> BackfillResult:
        """Like `get_ohlcv` but surfaces partial failures instead of failing loud.

        Same gap math and per-gap fetch+upsert as `get_ohlcv`, except a typed
        `UpstreamDataError` on a SUBSET of gaps is collected rather than raised:
        the merged bars fetched so far are returned with `partial_reason` set. If
        EVERY gap fails the typed error is re-raised (total failure stays loud).
        Live-mode only — backfill never runs under `as_of` (ADR-0007), so this
        method has no `as_of` parameter. The plain `get_ohlcv` keeps raising on
        any gap failure for the HTTP route + backtests that want loud failure."""
        source, adapter = self._ohlcv_route(symbol)

        # Source-derived timeframe (Yahoo 4h): run the partial-surfacing fetch
        # against the native base (1h), then resample the result — carrying the
        # base's partial_reason/message through so the agent still sees gap
        # failures. Binance-routed symbols never take this branch (native 4h).
        base = source_resampled_from(timeframe, source)
        if base is not None:
            base_result = self.get_ohlcv_with_status(symbol, base, start, end)
            return BackfillResult(
                bars=resample_ohlcv(list(base_result.bars), target=timeframe),
                partial_reason=base_result.partial_reason,
                message=base_result.message,
            )

        # History cap (per source): surface the cache-honest shape (cached bars
        # in-window + the typed reason + a human message) rather than a doomed
        # fetch that would return a misleading empty success (Plan 0025 ph3 /
        # ADR-0028). Binance is uncapped (Plan 0058 phase 2).
        if _exceeds_history_cap(timeframe, start, end, source):
            cached = list(self._repo.get_bars(symbol, timeframe, start, end)) if self._repo else []
            return BackfillResult(
                bars=cached,
                partial_reason="history_exceeded",
                message=_history_cap_message(timeframe, start, end),
            )

        if self._repo is None:
            bars = adapter.fetch_ohlcv(symbol, timeframe, start, end, now=_now())
            return BackfillResult(bars=list(bars), partial_reason=None, message=None)

        cached = self._repo.get_bars(symbol, timeframe, start, end)
        gaps = _coverage_gaps(cached, start, end, _min_fetch_span(timeframe))
        if not gaps:
            return BackfillResult(bars=list(cached), partial_reason=None, message=None)

        merged: dict[datetime, Bar] = {bar.event_ts: bar for bar in cached}
        failures: list[UpstreamDataError] = []
        for gap_start, gap_end in gaps:
            try:
                fetched = adapter.fetch_ohlcv(symbol, timeframe, gap_start, gap_end, now=_now())
            except UpstreamDataError as err:
                failures.append(err)
                continue
            if not fetched:
                continue
            self._repo.upsert_bars(list(fetched))
            for bar in fetched:
                if start <= bar.event_ts <= end:
                    merged[bar.event_ts] = bar

        result_bars = sorted(merged.values(), key=lambda b: b.event_ts)
        if failures and len(failures) == len(gaps):
            # Every gap failed — nothing was fetched. Stay loud.
            raise failures[0]
        if failures:
            first = failures[0]
            return BackfillResult(
                bars=result_bars,
                partial_reason=failure_reason(first),
                message=str(first),
            )
        return BackfillResult(bars=result_bars, partial_reason=None, message=None)

    def get_quote(
        self,
        symbol: str,
        as_of: datetime | None = None,
    ) -> Quote:
        # A live quote is wall-clock-sensitive with no replayable history to honour
        # `as_of` against (historical price replay is `get_ohlcv`'s job). Reject it
        # at the boundary, mirroring the screener/news/sentiment rejections above
        # (Plan 0019 / ADR-0019).
        if as_of is not None:
            raise ValueError(
                "as_of is not supported for live quotes — a quote is wall-clock-"
                "sensitive; use get_ohlcv for historical price (Plan 0019)",
            )
        # Membership-routed, mirroring `_ohlcv_route` (ADR-0052 / Plan 0058
        # follow-up): a Binance-only pair (e.g. BTCUSDT) quotes from Binance —
        # the Yahoo quote endpoint 404s on it. An unwired `self._binance`
        # (offline tests) keeps everything on Yahoo, exactly as before.
        if self._binance is not None and self._binance.is_known_symbol(symbol):
            return self._binance.get_quote(symbol)
        return self._yahoo_quote.get_quote(symbol)

    def search_symbols(
        self,
        query: str,
        as_of: datetime | None = None,
    ) -> Sequence[SymbolInfo]:
        # Symbol search is a live, wall-clock lookup against Yahoo's search
        # endpoint — there is no replayable historical source to honour `as_of`
        # against, so reject it at the boundary (Plan 0024 / ADR-0026; mirrors
        # the screener/quote/sentiment as_of rejections in this file). Every
        # hit is fetchable by get_ohlcv (the chartable-suggestion invariant of
        # ADR-0026): Yahoo results are in Yahoo's native namespace, and Binance
        # matches (Plan 0058 phase 3) come from the same exchangeInfo
        # membership set the OHLCV dispatch routes by, merged after Yahoo's
        # with their source labeled so `BTCUSDT` and `BTC-USD` are never
        # presented as interchangeable (ADR-0052).
        if as_of is not None:
            raise ValueError(
                "as_of is not supported for symbol search — search is a live "
                "lookup (Plan 0024 / ADR-0026)",
            )
        results = list(self._yahoo.search(query))
        if self._binance is not None:
            seen = {info.symbol for info in results}
            results.extend(info for info in self._binance.search(query) if info.symbol not in seen)
        return results

    def get_screener(
        self,
        filters: dict[str, Any],
        market: str = "america",
        exchange: str | None = None,
        limit: int = 50,
        as_of: datetime | None = None,
    ) -> Sequence[ScreenerRow]:
        # Screener results are wall-clock-sensitive: "RSI < 30 right now" is not
        # the same query five minutes ago. There is no cached/replayable source
        # to honour `as_of` against, so reject it at the boundary (Plan 0009 /
        # ADR-0019). A backtest-replay screener is a future plan with its own
        # snapshot table.
        if as_of is not None:
            raise ValueError(
                "as_of is not supported for screener queries — results are "
                "wall-clock-sensitive (Plan 0009 / ADR-0019)",
            )
        return self._screener.query(filters, market=market, exchange=exchange, limit=limit)

    def get_sentiment(
        self,
        symbol: str,
        window: str,
        source: Literal["rss-vader", "stocktwits"] = "rss-vader",
        as_of: datetime | None = None,
    ) -> SentimentSample:
        # Sentiment is wall-clock-sensitive like the news/posts it derives from
        # (Plan 0010 / ADR-0019); reject as_of at the boundary for every source.
        if as_of is not None:
            raise ValueError(
                "as_of is not supported for sentiment queries — results are "
                "wall-clock-sensitive (Plan 0010 / ADR-0019)",
            )
        # Registry lookup + delegate. The Literal guards callers at type-check
        # time; the None-check catches a runtime caller that bypassed the type
        # (Plan 0012 phase 2 done-when) and any future unregistered source.
        adapter = self._sentiment_sources.get(source)
        if adapter is None:
            raise ValueError(f"unknown sentiment source {source!r}")
        return adapter.fetch_sentiment(symbol=symbol, window=window)

    def get_news(
        self,
        symbol: str | None = None,
        window: str = "24h",
        limit: int = 50,
        with_sentiment: bool = False,
        as_of: datetime | None = None,
    ) -> Sequence[NewsItem]:
        # News is wall-clock-sensitive in the same way screener results are:
        # there is no cached/replayable source to honour `as_of` against, so
        # reject it at the boundary (Plan 0010 / ADR-0019).
        if as_of is not None:
            raise ValueError(
                "as_of is not supported for news queries — results are "
                "wall-clock-sensitive (Plan 0010 / ADR-0019)",
            )
        return self._news.fetch(
            symbol=symbol, window=window, limit=limit, with_sentiment=with_sentiment
        )

    def get_market_sentiment(
        self,
        market: Literal["crypto"],
        window: str = "current",
        as_of: datetime | None = None,
    ) -> MarketSentimentSample:
        # F&G is wall-clock-current: there is no replayable historical source to
        # honour `as_of` against, so reject it at the boundary (Plan 0011).
        if as_of is not None:
            raise ValueError(
                "as_of is not supported for market sentiment — the Fear & Greed "
                "index is wall-clock-sensitive (Plan 0011 / ADR-0019)",
            )
        adapter = self._market_sentiment_sources.get(market)
        if adapter is None:
            raise NotImplementedError(
                f"market {market!r} F&G not implemented; see Plan 0011 followups",
            )
        return adapter.fetch_current()

    def get_macro_context(
        self,
        market: Literal["crypto"] = "crypto",
        as_of: datetime | None = None,
    ) -> MacroContext:
        # The macro read is wall-clock-current (CoinGecko's `/global` snapshot has
        # no replayable history to honour `as_of` against), so reject `as_of` at
        # the boundary like the quote/screener/sentiment methods (Plan 0022 /
        # ADR-0027). `market` is crypto-only in v1 — the closed `Literal` enforces
        # that at type-check time; the parameter is accepted for forward-compat.
        if as_of is not None:
            raise ValueError(
                "as_of is not supported for macro context — the crypto macro read "
                "is wall-clock-current (Plan 0022 / ADR-0027)",
            )
        return self._coingecko.fetch_macro_context()


def _now() -> datetime:
    """Wall-clock seam for the rss-vader sentiment `as_of`. Injected into the
    `RssVaderSentimentAdapter` at construction (resolved at call time), so the
    provider remains the single owner of the seam; monkeypatched by tests to
    freeze time (cf. the adapters' own `_now`)."""
    return datetime.now(tz=UTC)


def _coverage_gaps(
    cached: Sequence[Bar],
    start: datetime,
    end: datetime,
    min_fetch_span: timedelta,
) -> list[tuple[datetime, datetime]]:
    """Return the fetch windows needed to combine with `cached` to cover [start, end].

    Head and tail gaps (uncached space at the edges of the requested window) are
    always returned. Gaps between consecutive cached bars are returned only when
    they exceed `min_fetch_span` — that filters out weekends, overnight breaks,
    and short holiday closures that the source legitimately has no bars for. The
    caller derives `min_fetch_span` from the timeframe's registry bar duration
    (`_min_fetch_span`), so the threshold is cadence-correct.

    Each returned window is widened to at least `min_fetch_span` (clamped to
    `[start, end]`) so the adapter does not generate one-bar round-trips.
    Windows that overlap after widening are merged.

    Inputs are expected to be sorted by `event_ts` (the repository guarantees this).
    """
    if start >= end:
        return []

    raw: list[tuple[datetime, datetime]] = []
    if not cached:
        raw.append((start, end))
    else:
        first_ts = cached[0].event_ts
        if first_ts > start:
            raw.append((start, first_ts))
        for prev, curr in pairwise(cached):
            if (curr.event_ts - prev.event_ts) >= min_fetch_span:
                raw.append((prev.event_ts, curr.event_ts))
        last_ts = cached[-1].event_ts
        if end > last_ts:
            raw.append((last_ts, end))

    widened: list[tuple[datetime, datetime]] = []
    for gap_start, gap_end in raw:
        span = gap_end - gap_start
        if span < min_fetch_span:
            extra = (min_fetch_span - span) / 2
            ws = max(start, gap_start - extra)
            we = min(end, gap_end + extra)
            widened.append((ws, we))
        else:
            widened.append((gap_start, gap_end))

    if not widened:
        return []
    widened.sort()
    merged: list[tuple[datetime, datetime]] = [widened[0]]
    for ws, we in widened[1:]:
        last_s, last_e = merged[-1]
        if ws <= last_e:
            merged[-1] = (last_s, max(last_e, we))
        else:
            merged.append((ws, we))
    return merged
