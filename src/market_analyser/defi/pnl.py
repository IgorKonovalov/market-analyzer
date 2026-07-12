"""P&L replay engine — average-cost lots, block-time valuation, vs-HODL
(Plan 0035 phase 6, implementing ADR-0036).

Replays each position's ordered `PositionEvent`s (block, then in-block index)
and values **every leg at its own block timestamp** via the
`HistoricalPriceSource` — never at "now", never at Zerion's inline figures
(the no-lookahead corollary). With the phase-4 snapshot cache behind the
source, a re-run on the same cached inputs is **byte-identical** — there is
deliberately no wall-clock read anywhere in this module; run provenance is the
phase-7 job's concern.

**Accounting (average-cost pot, documented here because future-you will ask
"why is my P&L this number"):**

- *Contribution* (`add_liquidity` / `supply`; out-legs): the position's cost
  basis grows by the legs' block-time USD value; per-token contributed
  amounts accrue.
- *Extraction* (`remove_liquidity` / `withdraw_supply`; in-legs): the
  extracted fraction is value-weighted — `f = V_extracted / V_holdings`,
  both priced at the extraction block, where holdings are the net contributed
  amounts before the event. It realizes `V_extracted - f * basis` and
  releases `f * basis` (capped at 1.0 — an extraction exceeding holdings
  realizes against the whole remaining basis).
- *Income* (`fee_claim` / `reward_claim`; in-legs): realized income at
  claim-time price, exactly as ADR-0036 books it.
- *Swap* (`swap`; average-cost lot conversion — Plan 0084 / ADR-0079): a swap
  reshuffles value **within** the position rather than adding or removing
  capital, so it leaves the aggregate `basis` untouched (the invariant that
  keeps a mis-booked swap from corrupting cost basis). It drains the sold
  (out-leg) token amounts and accrues the bought (in-leg) token amounts, and
  realizes the swap's own execution delta `V_in - V_out` at block-time prices —
  for a fair atomic swap that is ≈ 0 (the fee/slippage cost), so a swap-inclusive
  lifecycle reconciles instead of failing loud.
- *Custody move* (`custody_move`; Plan 0084): staking / unstaking the LP token or
  NFT into or out of the gauge moves the position's *receipt*, not its underlying
  tokens, so it is a no-op — no basis, realized, or contributed change.
- *Debt* (`borrow` / `repay`, lending_borrow positions): a borrow draws
  `V_in` onto a debt pot; a repay releases `min(V_out, debt)` and realizes
  `released - V_out` (<= 0 — the interest cost surfaces as the debt closes).
  Unrealized is sign-flipped for debt: remaining pot minus current debt value.
- *Unrealized* (supply side): the position's current `usd_value` (discovery's
  figure, per the plan's "consumes it as-is") minus the remaining basis.
- *vs-HODL* (LP only): `(current value + claimed income) - (net contributed
  amounts valued at `as_of`)` — impermanent loss plus earned fees as one
  signed fact. `as_of` is caller-supplied (the phase-7 job passes the newest
  cached transaction's timestamp), keeping the benchmark deterministic.

**Loud failure (ADR-0036):** a missing price for any required leg, or an
event kind the pot cannot book (`liquidation` and `unclassified` — `swap` is
now booked as of Plan 0084), fails *that position*: every numeric field is
`None`, `incomplete=True`, and `notes` names the offending leg or event.
Nothing is coerced to zero. An incomplete position is **excluded** from the
wallet total — not zeroed, and no longer nulling the whole wallet (Plan 0088 /
ADR-0082, amending ADR-0036's "any incomplete ⇒ null total"): the totals are
the sum over the *complete* positions, carried with `partial=True` and an
`incomplete_position_count`. This keeps "never fabricate a value for a leg we
cannot price" while letting one unpriceable exotic position stop hiding a
fully-reconstructed portfolio.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from market_analyser.data.adapters.defillama import token_key
from market_analyser.data.sources import HistoricalPriceSource
from market_analyser.defi.discovery import mask_wallet
from market_analyser.defi.models import DefiPosition, RewardAmount
from market_analyser.defi.pnl_events import PositionEvent

# Event kinds the pot books. `swap` (average-cost conversion) and `custody_move`
# (a no-op) join the booked set as of Plan 0084; `liquidation` and `unclassified`
# remain unbooked and mark the position incomplete — inventing arithmetic for them
# would produce plausible wrong numbers (the honest gap, ADR-0036 fallback posture).
_CONTRIBUTION_KINDS = frozenset({"add_liquidity", "supply"})
_EXTRACTION_KINDS = frozenset({"remove_liquidity", "withdraw_supply"})
_INCOME_KINDS = frozenset({"fee_claim", "reward_claim"})
_DEBT_KINDS = frozenset({"borrow", "repay"})

# Fixed rolling-window set (Plan 0088 / ADR-0082): realized P&L attributable to
# the events dated inside each window, anchored to the run's `now`. `all` has no
# lower bound (every delta). Ordered shortest → longest for a natural report.
Window = Literal["7d", "30d", "90d", "all"]
_WINDOW_SPANS: tuple[tuple[Window, timedelta | None], ...] = (
    ("7d", timedelta(days=7)),
    ("30d", timedelta(days=30)),
    ("90d", timedelta(days=90)),
    ("all", None),
)


class WindowPnl(BaseModel):
    """Realized P&L over one rolling window (Plan 0088 / ADR-0082). Exact: the
    sum of the per-event realized deltas whose `mined_at` falls inside the window
    (`all` = every delta), so `all` always equals the position's all-time
    `realized_usd`. Deterministic given the run's `now`."""

    model_config = ConfigDict(frozen=True)

    window: Window
    realized_usd: float


class PositionPnl(BaseModel):
    """One position's reconstructed P&L. `None` numerics always travel with
    `incomplete=True` and a naming note — never silently-zeroed data."""

    model_config = ConfigDict(frozen=True)

    position_id: str = Field(min_length=1)
    realized_usd: float | None
    unrealized_usd: float | None
    cost_basis_usd: float | None
    vs_hodl_usd: float | None  # LP only; None for other kinds
    incomplete: bool
    notes: list[str] = Field(default_factory=list)
    # Per-window realized P&L (Plan 0088 / ADR-0082): exact, anchored to the run's
    # `now`. Empty for an incomplete position (no reconstructable figures). The
    # `all` window equals `realized_usd`.
    windows: list[WindowPnl] = Field(default_factory=list)
    # Current-state, on-chain `earned()` read (Plan 0084 / ADR-0079): rewards owed
    # right now but not yet claimed, which tx-replay cannot see. `None` when the
    # position is not gauge-staked or owes nothing. Deliberately OUTSIDE the
    # replay figures and the determinism guarantee (a live read, like `usd_value`);
    # the engine never sets it — the job attaches it after replay.
    unclaimed_rewards: list[RewardAmount] | None = None


class WalletPnl(BaseModel):
    """The wallet's reconstructed P&L. Totals sum over the **complete** positions
    only (Plan 0088 / ADR-0082, amending ADR-0036): an incomplete position is
    excluded — never zeroed, never nulling the whole wallet — and `partial` flags
    the exclusion. `partial` is true iff any position is incomplete, so it
    coincides with `incomplete`; it is carried explicitly because it labels the
    *totals* as a partial sum, which the tool and UI surface prominently."""

    model_config = ConfigDict(frozen=True)

    wallet: str  # masked (0x1234…abcd)
    positions: list[PositionPnl]
    realized_usd: float | None
    unrealized_usd: float | None
    incomplete: bool
    # Plan 0088 / ADR-0082: the totals above are a sum over the complete positions;
    # `partial` is true when at least one position was excluded, and
    # `incomplete_position_count` says how many. A fully-complete wallet reports
    # `partial=False`, `incomplete_position_count=0`, and the same totals as before.
    partial: bool = False
    incomplete_position_count: int = 0
    crosscheck_zerion_total: float | None = None  # advisory (phase 7 wires it)
    crosscheck_warning: bool = False
    # Wallet roll-up of the per-position `unclaimed_rewards`, summed by symbol
    # (Plan 0084). Same current-state / outside-determinism status as the
    # per-position field; `None` when no position owes anything.
    unclaimed_rewards: list[RewardAmount] | None = None


class _MissingPrice(Exception):
    """Internal control flow: a leg's block-time price is unavailable."""

    def __init__(self, token: str, ts: int) -> None:
        super().__init__(token)
        self.token = token
        self.ts = ts


def compute_wallet_pnl(
    *,
    wallet: str,
    positions: list[DefiPosition],
    events: list[PositionEvent],
    price_source: HistoricalPriceSource,
    as_of: datetime,
    now: datetime,
) -> WalletPnl:
    """Replay every position's events into per-position and wallet P&L.

    `events` must already be in replay order (the phase-5 mapper preserves the
    adapter/cache order); they are grouped per position without re-sorting.

    Two distinct time anchors, both injected (the engine never reads the wall
    clock — see the module note): `as_of` anchors the LP HODL benchmark to the
    last-tx time (input-derived, keeping the vs-HODL mark byte-identical), while
    `now` is the analysis-time anchor for the rolling windows (Plan 0088 /
    ADR-0082) — a wall-clock read the *job* captures once and passes in, so "last
    30 days" means 30 calendar days. Windowed figures are deterministic given a
    fixed `now`; they are not part of the cross-calendar-time guarantee."""
    per_position: dict[str, list[PositionEvent]] = {}
    for event in events:
        per_position.setdefault(event.position_id, []).append(event)
    results = [
        _replay_position(
            position,
            per_position.get(position.position_id, []),
            price_source,
            as_of,
            now,
        )
        for position in positions
    ]
    # Partial totals (Plan 0088 / ADR-0082): sum over the COMPLETE positions
    # only. An incomplete position contributes nothing — it is excluded, not
    # zeroed, and no longer nulls the whole wallet. A complete position always
    # carries non-None figures (the `is not None` guard is for the type checker).
    complete = [p for p in results if not p.incomplete]
    incomplete_count = len(results) - len(complete)
    realized = sum(p.realized_usd for p in complete if p.realized_usd is not None)
    unrealized = sum(p.unrealized_usd for p in complete if p.unrealized_usd is not None)
    return WalletPnl(
        wallet=mask_wallet(wallet),
        positions=results,
        realized_usd=realized,
        unrealized_usd=unrealized,
        incomplete=incomplete_count > 0,
        partial=incomplete_count > 0,
        incomplete_position_count=incomplete_count,
    )


def _replay_position(
    position: DefiPosition,
    events: list[PositionEvent],
    price_source: HistoricalPriceSource,
    as_of: datetime,
    now: datetime,
) -> PositionPnl:
    notes: list[str] = []
    basis = 0.0  # supply-side average-cost pot (USD)
    debt = 0.0  # drawn-debt pot (USD), lending_borrow only
    realized = 0.0
    # Per-event realized deltas tagged with the event's block time, for the
    # rolling-window bucketer (Plan 0088 / ADR-0082). Every place `realized`
    # grows records one `(mined_at, delta)`, so the deltas sum (in event order)
    # to `realized` exactly — the `all` window equals the all-time figure.
    realized_deltas: list[tuple[datetime, float]] = []
    contributed: dict[str, float] = {}  # net contributed amount per token key
    try:
        for event in events:
            kind = event.kind
            if kind in _CONTRIBUTION_KINDS:
                value = _legs_value(event, "out", price_source)
                basis += value
                _accrue(contributed, event, "out")
            elif kind in _EXTRACTION_KINDS:
                extracted = _legs_value(event, "in", price_source)
                holdings_value = _holdings_value(contributed, event, price_source)
                fraction = 1.0 if holdings_value <= 0 else min(1.0, extracted / holdings_value)
                released = fraction * basis
                delta = extracted - released
                realized += delta
                realized_deltas.append((event.mined_at, delta))
                basis -= released
                _drain(contributed, event, "in")
            elif kind in _INCOME_KINDS:
                delta = _legs_value(event, "in", price_source)
                realized += delta
                realized_deltas.append((event.mined_at, delta))
            elif kind in _DEBT_KINDS:
                if kind == "borrow":
                    debt += _legs_value(event, "in", price_source)
                else:
                    repaid = _legs_value(event, "out", price_source)
                    released = min(repaid, debt)
                    delta = released - repaid
                    realized += delta
                    realized_deltas.append((event.mined_at, delta))
                    debt -= released
            elif kind == "swap":
                # Average-cost conversion (Plan 0084): reshuffle value within the
                # position — realize the block-time execution delta, drain the sold
                # token, accrue the bought token — but leave `basis` untouched (no
                # capital entered or left), so a swap can't corrupt cost basis.
                value_out = _legs_value(event, "out", price_source)
                value_in = _legs_value(event, "in", price_source)
                delta = value_in - value_out
                realized += delta
                realized_deltas.append((event.mined_at, delta))
                _drain(contributed, event, "out")
                _accrue(contributed, event, "in")
            elif kind == "custody_move":
                # Staking/unstaking the LP receipt in/out of the gauge moves no
                # underlying value — a no-op (Plan 0084 / ADR-0079).
                pass
            else:
                # liquidation / unclassified: classified, not booked — an honest
                # incomplete beats invented arithmetic (ADR-0036).
                notes.append(f"unbooked {kind} event {event.tx_hash}")
        if notes:
            return _incomplete(position, notes)
        if position.kind == "lending_borrow":
            unrealized = debt - position.usd_value
            remaining: float | None = debt
        else:
            unrealized = position.usd_value - basis
            remaining = basis
        vs_hodl: float | None = None
        if position.kind == "lp":
            income = _income_total(events, price_source)
            hodl = _amounts_value(contributed, position, as_of, price_source)
            vs_hodl = (position.usd_value + income) - hodl
        for figure in (realized, unrealized, remaining, vs_hodl):
            if figure is not None and not math.isfinite(figure):
                return _incomplete(position, ["non-finite figure in replay arithmetic"])
        return PositionPnl(
            position_id=position.position_id,
            realized_usd=realized,
            unrealized_usd=unrealized,
            cost_basis_usd=remaining,
            vs_hodl_usd=vs_hodl,
            incomplete=False,
            notes=notes,
            windows=_windowed_realized(realized_deltas, now),
        )
    except _MissingPrice as gap:
        return _incomplete(
            position,
            [f"no block-time price for {gap.token} at ts={gap.ts}"],
        )


def _windowed_realized(deltas: list[tuple[datetime, float]], now: datetime) -> list[WindowPnl]:
    """Bucket the per-event realized deltas into the fixed windows relative to
    `now`. A window sums the deltas whose `mined_at` is on or after `now - span`
    (`all` = every delta), in event order — so `all` reproduces the running
    `realized` total exactly. Pure given `(deltas, now)`."""
    windows: list[WindowPnl] = []
    for label, span in _WINDOW_SPANS:
        if span is None:
            total = sum((delta for _, delta in deltas), 0.0)
        else:
            cutoff = now - span
            total = sum((delta for ts, delta in deltas if ts >= cutoff), 0.0)
        windows.append(WindowPnl(window=label, realized_usd=total))
    return windows


def _incomplete(position: DefiPosition, notes: list[str]) -> PositionPnl:
    return PositionPnl(
        position_id=position.position_id,
        realized_usd=None,
        unrealized_usd=None,
        cost_basis_usd=None,
        vs_hodl_usd=None,
        incomplete=True,
        notes=notes,
    )


def _price_at(
    price_source: HistoricalPriceSource,
    event: PositionEvent,
    address: str | None,
    ts: int,
) -> float:
    price = price_source.fetch_price(chain=event.chain, address=address, ts=ts)
    if price is None:
        raise _MissingPrice(token_key(event.chain, address), ts)
    return price


def _legs_value(
    event: PositionEvent,
    direction: str,
    price_source: HistoricalPriceSource,
) -> float:
    ts = int(event.mined_at.timestamp())
    total = 0.0
    for leg in event.legs:
        if leg.direction != direction:
            continue
        total += leg.amount * _price_at(price_source, event, leg.address, ts)
    return total


def _accrue(contributed: dict[str, float], event: PositionEvent, direction: str) -> None:
    for leg in event.legs:
        if leg.direction != direction:
            continue
        key = token_key(event.chain, leg.address)
        contributed[key] = contributed.get(key, 0.0) + leg.amount


def _drain(contributed: dict[str, float], event: PositionEvent, direction: str) -> None:
    for leg in event.legs:
        if leg.direction != direction:
            continue
        key = token_key(event.chain, leg.address)
        contributed[key] = max(0.0, contributed.get(key, 0.0) - leg.amount)


def _holdings_value(
    contributed: dict[str, float],
    event: PositionEvent,
    price_source: HistoricalPriceSource,
) -> float:
    """The net contributed amounts valued at the event's block — the
    denominator of the value-weighted extraction fraction. Insertion-ordered
    dict iteration (accrual order), never set iteration."""
    ts = int(event.mined_at.timestamp())
    total = 0.0
    for key, amount in contributed.items():
        if amount <= 0:
            continue
        address = None if key.startswith("coingecko:") else key.split(":", 1)[1]
        total += amount * _price_at(price_source, event, address, ts)
    return total


def _income_total(events: list[PositionEvent], price_source: HistoricalPriceSource) -> float:
    total = 0.0
    for event in events:
        if event.kind in _INCOME_KINDS:
            total += _legs_value(event, "in", price_source)
    return total


def _amounts_value(
    contributed: dict[str, float],
    position: DefiPosition,
    as_of: datetime,
    price_source: HistoricalPriceSource,
) -> float:
    """The HODL benchmark: net contributed amounts valued at `as_of`."""
    ts = int(as_of.timestamp())
    total = 0.0
    for key, amount in contributed.items():
        if amount <= 0:
            continue
        address = None if key.startswith("coingecko:") else key.split(":", 1)[1]
        price = price_source.fetch_price(chain=position.chain, address=address, ts=ts)
        if price is None:
            raise _MissingPrice(key, ts)
        total += amount * price
    return total


__all__ = ["PositionPnl", "WalletPnl", "Window", "WindowPnl", "compute_wallet_pnl"]
