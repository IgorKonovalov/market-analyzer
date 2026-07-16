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

from market_analyser.data.metric_series import MetricPoint
from market_analyser.data.types import (
    AccountHoldings,
    Bar,
    MarketSentimentSample,
    NewsItem,
    PredictionMarket,
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
    from market_analyser.defi.models import (
        Chain,
        DefiFundamentals,
        DefiPosition,
        ExecutableQuote,
        LpPositionDetail,
        RewardAmount,
    )
    from market_analyser.defi.tx_models import DecodedTx


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
class PredictionMarketSource(Protocol):
    """A read-only source of prediction-market odds (Plan 0040 / ADR-0041): markets
    whose outcome prices ARE market-implied probabilities in `[0, 1]` — a new
    signal class distinct from OHLCV or NLP sentiment (a money-weighted probability
    of a discrete event). Read-only by charter: a conforming source holds no key,
    signs nothing, and moves no funds (ADR-0041; Polymarket *trading* is the
    deferred execution-pillar concern, not this).

    Members of the provider's prediction-market selector registry, keyed by source
    name ("polymarket"), built in the composition root (ADR-0031) — adding a second
    prediction-market source is then one registry entry.
    """

    def search_markets(self, query: str, *, limit: int = 20) -> Sequence[PredictionMarket]:
        """Resolve a free-text query to matching markets with their current odds.
        An empty/whitespace query returns `[]`; a zero-match query returns `[]`
        (not an error). `limit` bounds the result count."""
        ...

    def fetch_market(self, market_id: str) -> PredictionMarket:
        """Fetch one market's current outcomes + implied probabilities by id.
        Raises the typed error taxonomy on an unknown id or a shape-broken
        payload — never silently returns a fabricated probability."""
        ...


@runtime_checkable
class ExecutableQuoteSource(Protocol):
    """A read-only source of **executable** per-pool DEX quotes for a canonical pair
    across one or more venues at a specific trade size (Plan 0086 / ADR-0080) — the
    cross-pool discrepancy scanner v2's input, unifying constant-product and
    concentrated liquidity behind one contract: a conforming source returns each
    pool's *net-of-cost* `buy_cost` (exact-output) and `sell_proceeds` (exact-input)
    — fee + slippage already inside — instead of a marginal price the screener must
    add an estimated cost to.

    Read-only by charter: a conforming source holds no private
    key, signs nothing, submits no state-changing RPC, and moves no funds;
    its only credential is a read-only JSON-RPC endpoint URL (ADR-0038 — a read URL,
    not a trade key). The concentrated-liquidity implementation prices via the DEX
    **Quoter**, which is reached by `eth_call` (a staticcall simulation) and so stays
    read-only exactly like `getReserves()` (ADR-0080). It reports quotes as facts,
    never a trade instruction.

    `fetch_executable_quotes` returns one `ExecutableQuote` per pool the source has
    configured for `pair` that can executably fill `trade_size` — each carrying
    `buy_cost`, `sell_proceeds`, and the `marginal_price` zero-size reference the
    screener reconstructs the fee/slippage breakdown from. An unknown or unconfigured
    pair returns `[]` (not an error); a pool that cannot source the size is omitted
    rather than fabricating a number — for the CL Quoter this is a quote-leg revert
    (ADR-0086), the same "cannot source the size → omit" the constant-product adapter
    applies when the trade exceeds reserve depth; a shape-broken on-chain read or a
    structural-read revert raises the source's typed error taxonomy, never a
    fabricated/zeroed quote.

    Members of the executable-quote selector registry, keyed by source name
    ("onchain" for constant-product, "concentrated" for CL), built in the
    composition root (ADR-0031) — adding a venue is one registry entry.
    """

    def fetch_executable_quotes(
        self, pair: str, *, trade_size: float
    ) -> Sequence[ExecutableQuote]: ...


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


@runtime_checkable
class UnclaimedRewardsSource(Protocol):
    """A read-only source of a position's **currently owed-but-unclaimed** gauge
    rewards (Plan 0084 / ADR-0079), read on-chain via the gauge's `earned()`.
    Transaction replay is structurally blind to unclaimed emissions — there is no
    claim tx yet — so this is a separate, labeled *current-state* read that
    augments the P&L output without entering the deterministic replay figures.

    Read-only by charter: a conforming source holds no key, signs nothing, and
    issues only `eth_call`. Best-effort by contract: it returns the reward amounts
    it can read (empty when the position is not gauge-staked or owes nothing), and
    a read failure is the caller's to swallow — an unclaimed read must never fail
    the P&L reconstruction. `owner` is the wallet address (the `earned()` account).

    Members of an unclaimed-rewards selector registry keyed by source name ("rpc"),
    built in the composition root (ADR-0031)."""

    def fetch_unclaimed(self, *, position: DefiPosition, owner: str) -> Sequence[RewardAmount]: ...


@runtime_checkable
class GaugeResolutionSource(Protocol):
    """Resolves an Aerodrome/Velodrome **gauge** contract address to the DEX
    **pool** address it distributes emissions for (Plan 0084 / ADR-0079) — the
    seam the P&L classifier consults so a gauge `getReward` transaction joins the
    pool position it belongs to. Aerodrome routes emissions through a per-pool
    gauge distinct from the pool, so without this mapping a reward cannot be
    attributed to the position that earned it (ADR-0079: the gauge indirection
    breaks the *join*, not the vocabulary).

    Read-only by charter: a conforming source holds no private key, signs
    nothing, submits no state-changing RPC, and issues only a read `eth_call`
    (`gauge.pool()`); its sole credential is a read-only JSON-RPC URL (ADR-0038).
    Resolution is precision-first — an address that is not a gauge, or an
    unreachable read, returns `None` (never a raise, never a guess), so the
    classifier degrades to an honest `unclassified` rather than a wrong
    attribution (ADR-0036 "an ambiguous join is worse than an honest gap").

    Members of a gauge-resolution selector registry keyed by source name ("rpc"),
    built in the composition root (ADR-0031)."""

    def resolve_pool(self, *, chain: Chain, gauge_address: str) -> str | None: ...


@runtime_checkable
class TxHistorySource(Protocol):
    """A source of a wallet's decoded transaction history across the target EVM
    chains (ADR-0035 / ADR-0036) — the P&L replay engine's input. `address` is a
    raw `0x…` EVM address; the source returns normalized, boundary-validated
    transactions in deterministic order (block number, then in-block index).

    `min_mined_at` is the gap-fetch seam (Plan 0035 phase 3): the ingestion
    facade passes the newest cached transaction's timestamp so a re-scan pulls
    only what's newer, instead of re-paging the full history. `None` means the
    full history. Members of the DeFi tx-history selector registry, keyed by
    source name ("zerion"), built in the composition root (ADR-0031)."""

    def fetch_transactions(
        self,
        address: str,
        *,
        min_mined_at: datetime | None = None,
    ) -> Sequence[DecodedTx]: ...


@runtime_checkable
class PnlCrosscheckSource(Protocol):
    """A source of a third-party wallet-P&L total, used only as the ADR-0036
    **advisory cross-check** (Zerion's FIFO `total_gain`): a gross divergence
    from our reconstruction flags a likely bug; small method-driven
    differences are expected and ignored. Never the source of truth. `None`
    means the source carries no usable figure."""

    def fetch_pnl_total(self, address: str) -> float | None: ...


@runtime_checkable
class HistoricalPriceSource(Protocol):
    """A source of a token's USD price at a past timestamp (ADR-0034/0036) —
    the P&L engine's block-time valuation input. `address` is the token's
    contract address on `chain`, or `None` for the chain's native coin (all
    four target chains are ETH-native). `ts` is the UTC epoch-second block
    timestamp.

    Returns the price, or `None` when the source has no coverage for that
    token at that timestamp — the typed "no price" the engine must surface as
    an *incomplete* position, never coerce to zero (ADR-0036 loud failure).
    A conforming source snapshots every resolved price on first lookup and
    re-reads it thereafter (the ADR-0036 determinism mechanism): an upstream
    revision must not change a re-run."""

    def fetch_price(
        self,
        *,
        chain: Chain,
        address: str | None,
        ts: int,
    ) -> float | None: ...


@runtime_checkable
class AccountHoldingsSource(Protocol):
    """A source of one venue account's holdings — spot balances plus open
    derivative positions — for the cross-venue portfolio (Plan 0041 /
    ADR-0042). Read-only by charter: a conforming source authenticates with a
    read-only credential (ADR-0038) and exposes no order/write path (the
    Plan 0041 done-when pins this at the source level). Members of the
    portfolio holdings registry, keyed by venue name ("binance"), built in the
    composition root (ADR-0031)."""

    def fetch_account_holdings(self) -> AccountHoldings: ...


@runtime_checkable
class DefiFundamentalsSource(Protocol):
    """A read-only source of DeFi-native token/protocol fundamentals (Plan 0107 /
    ADR-0102) — TVL + short history, DEX volume, fee/reward APR, token mcap/FDV,
    and the unlock/dilution calendar — surfaced as a `DefiFundamentals` **condition
    read** (ADR-0029: conditions only, never a call/score).

    Honest-degrade by charter (ADR-0019): a field the source cannot cover comes
    back `None` with a `notes` entry, and a whole-source failure (rate-limit, 4xx,
    transport exhaustion) degrades to a `DefiFundamentals` of honest nulls + a
    note — **never** an exception, never a fabricated number. Wall-clock-sensitive
    with **no `as_of`** parameter: these are current-state reads with no
    reconstructable point-in-time series (ADR-0102), so `query` is the only input
    and each result stamps its own read time.

    `query` is a token symbol or a protocol slug ("AERO", "aerodrome",
    "uniswap"); the source resolves it to the upstream's key. Members of the
    DeFi-fundamentals selector registry, keyed by source name ("defillama"), built
    in the composition root (ADR-0031)."""

    def fetch_fundamentals(self, query: str) -> DefiFundamentals: ...


@runtime_checkable
class MetricSeriesSource(Protocol):
    """A source of historized scalar metric points for one or more registered
    series ids (ADR-0051). Sources that expose history implement a real backfill
    here; snapshot-only sources (e.g. CoinGecko dominance) accrue instead by
    appending the current value at poll time and need not implement this.

    `start` / `end` are UTC epoch seconds (the `MetricPoint.ts` currency),
    inclusive on both ends. The returned points are sorted by `ts` ascending and
    carry only registered series ids — the repository re-checks registration at
    its own boundary, but a conforming source never produces an orphan id."""

    def fetch_series(
        self,
        series_id: str,
        start: int | None = None,
        end: int | None = None,
    ) -> Sequence[MetricPoint]: ...
