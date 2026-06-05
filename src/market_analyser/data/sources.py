"""Per-capability data-source contracts — the producer-side mirror of ADR-0007.

The *consumer* side of the data layer has a single stable Protocol
(`MarketDataProvider`, `provider.py`); this module is its *producer* counterpart.
Each adapter implements one or more of these narrow, `@runtime_checkable`
Protocols, so a new source has a shape to conform to — the type checker enforces
the contract before runtime — instead of inventing its own method name (ADR-0031).

The two operations that select among interchangeable sources
(`get_sentiment(source=...)`, `get_market_sentiment(market=...)`) are typed
against `SentimentSource` / `MarketSentimentSource`: the provider's selector
registries hold those Protocols, so adding a source is one registry entry, not a
dispatch-body edit.

Unlike strategies (ADR-0004), adapters are stateful wired objects (they hold an
HTTP client, proxy config, TTLs), so there is deliberately no auto-discovery
package walk — registration stays explicit in the composition root. The seam this
module adds is the *typed contract*, not auto-wiring.

`OhlcvSource` covers only the raw gap fetch the provider delegates to; the
cache / gap / `as_of` anti-lookahead orchestration legitimately lives in the
provider (ADR-0007), so it is absent here.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from market_analyser.data.types import (
    Bar,
    MarketSentimentSample,
    NewsItem,
    Quote,
    ScreenerRow,
    SentimentSample,
    SymbolInfo,
)

if TYPE_CHECKING:
    # Type-only reference so `data/` carries no runtime import of the `defi/`
    # domain (the adapter that *produces* DefiPosition imports it at runtime;
    # this Protocol only names the return type). `defi/models` imports nothing
    # from `data/`, so there is no import cycle.
    from market_analyser.defi.models import Chain, DefiPosition, LpPositionDetail


@runtime_checkable
class OhlcvSource(Protocol):
    """A source of raw OHLCV bars for a `[start, end]` window. No `as_of`: the
    provider owns the anti-lookahead orchestration and delegates only the fetch.

    `now` is the provider's recency reference (its `_now`/`as_of` seam), passed so
    the source classifies an empty upstream response by window recency rather than
    by reading the wall clock itself (ADR-0033): an empty *leading-edge* window is
    an unknown symbol, an empty *historical* window is a legitimate end-of-history.
    Defaulted so a caller without a reference keeps the conservative leading-edge
    reading; the provider always supplies it."""

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        *,
        now: datetime | None = None,
    ) -> Sequence[Bar]: ...


@runtime_checkable
class SymbolSearchSource(Protocol):
    """A source that resolves a free-text query to chartable symbols."""

    def search(self, query: str) -> Sequence[SymbolInfo]: ...


@runtime_checkable
class QuoteSource(Protocol):
    """A source of live single-symbol quotes."""

    def get_quote(self, symbol: str) -> Quote: ...


@runtime_checkable
class ScreenerSource(Protocol):
    """A source that screens a market against column filters."""

    def query(
        self,
        filters: Mapping[str, Any],
        *,
        market: str = "america",
        exchange: str | None = None,
        limit: int = 50,
    ) -> Sequence[ScreenerRow]: ...


@runtime_checkable
class NewsSource(Protocol):
    """A source of recent news items, optionally VADER-scored."""

    def fetch(
        self,
        symbol: str | None = None,
        window: str = "24h",
        limit: int = 50,
        with_sentiment: bool = False,
    ) -> Sequence[NewsItem]: ...


@runtime_checkable
class SentimentSource(Protocol):
    """A per-symbol sentiment source, selected by the `source` key of
    `get_sentiment`. Members of the provider's sentiment registry."""

    def fetch_sentiment(self, symbol: str, window: str = "24h") -> SentimentSample: ...


@runtime_checkable
class MarketSentimentSource(Protocol):
    """A whole-market sentiment source (e.g. crypto Fear & Greed), selected by
    the `market` key of `get_market_sentiment`. Members of the provider's
    market-sentiment registry."""

    def fetch_current(self) -> MarketSentimentSample: ...


@runtime_checkable
class WalletPositionsSource(Protocol):
    """A source that discovers a wallet's interpreted DeFi positions across the
    target EVM chains (ADR-0035 / ADR-0034). `address` is a raw `0x…` EVM
    address; the source returns the normalized, boundary-validated positions it
    can decode. Members of the DeFi wallet-positions selector registry, keyed by
    source name ("zerion"), built in the composition root (ADR-0031)."""

    def fetch_positions(self, address: str) -> Sequence[DefiPosition]: ...


@runtime_checkable
class LpPositionDetailSource(Protocol):
    """A source of the deep on-chain state of a single concentrated-liquidity LP
    position — the *depth* half of the DeFi program (Plan 0034) that enriches the
    interpreted positions `WalletPositionsSource` discovers (ADR-0034: deep state
    comes from our own RPC + The Graph, not the discovery aggregator).

    The position is identified by what the discovery payload exposes: `chain` +
    `pool_address` is sufficient for the Velodrome/Aerodrome class (the LP is an
    ERC-20 LP token — one hop). Uniswap-v3 positions are NFTs and two positions
    can share a pool with different ranges, so `token_id` (the position NFT) is
    the finer key for that class (two hops). `token_id` is therefore optional:
    omitted for the one-hop class, supplied for Uni-v3.

    Members of the DeFi LP-detail selector registry, keyed by source name, built
    in the composition root (ADR-0031)."""

    def fetch_lp_detail(
        self,
        *,
        chain: Chain,
        pool_address: str,
        token_id: int | None = None,
    ) -> LpPositionDetail: ...

    def resolve_univ3_token_id(
        self,
        *,
        chain: Chain,
        pool_address: str,
        owner: str,
    ) -> int | None:
        """Resolve a wallet's Uniswap-v3 position NFT `token_id` for a pool (the
        two-hop first hop), or `None` when the wallet holds no matching position.
        A one-hop-only source returns `None`; the enrichment step calls this only
        for Uni-v3-class positions, then passes the id to `fetch_lp_detail`."""
        ...
