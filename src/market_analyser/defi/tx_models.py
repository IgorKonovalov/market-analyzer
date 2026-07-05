"""Normalized decoded-transaction models (ADR-0036, Plan 0035 phase 1).

`DecodedTx` is the shape the P&L pipeline consumes: faithful to Zerion's
`/transactions/` payload (the Zerion-API survey §3 group B) but source-neutral —
any `TxHistorySource` produces it, and nothing downstream knows which adapter
did. It carries **no accounting interpretation**: mapping a transaction onto the
ADR-0036 economic-event taxonomy (`add_liquidity`, `fee_claim`, …) is the
phase-5 classifier's job, deliberately kept out of the boundary model.

Boundary-validated like `DefiPosition` (`defi/models.py`): a NaN / Inf /
negative `usd_value` and a non-positive / non-finite transfer `amount` are
rejected at construction, never silently coerced to zero — a silently-zeroed
leg produces confident, wrong P&L (ADR-0036 "loud failure"). Downstream code
may trust the fields.

`operation_type` is a **closed** vocabulary: the survey's observed set plus an
explicit `"unknown"` fallback. An adapter maps any unrecognized upstream string
to `"unknown"` at parse time — a raw passthrough would make the taxonomy
classifier's exhaustiveness meaningless.

Transfer `usd_value` / `price` are Zerion's *point-in-time* figures, carried as
informational context only: the engine re-prices every leg at its block
timestamp via the `HistoricalPriceSource` (the ADR-0036 no-lookahead corollary),
so these fields never enter the P&L arithmetic.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from market_analyser.defi.models import Chain

# The survey's observed `operation_type` vocabulary (§3 group B) plus the
# explicit `unknown` fallback. Closed on purpose: an unlisted upstream value is
# normalized to `unknown` by the adapter, never passed through raw, so the
# phase-5 classifier can be exhaustive over this set.
TxOperationType = Literal[
    "receive",
    "send",
    "trade",
    "deposit",
    "withdraw",
    "mint",
    "execute",
    "approve",
    "borrow",
    "repay",
    "unknown",
]

# Zerion's transaction status vocabulary. `failed` transactions are carried (a
# failed tx still burned its fee) but move no assets; the classifier skips them.
TxStatus = Literal["confirmed", "failed", "pending"]


class TxTransfer(BaseModel):
    """One asset movement inside a transaction: direction relative to the
    scanned wallet, the token, and the moved amount. `amount` is finite and
    strictly positive — a zero/NaN/negative movement is a malformed decode, not
    a transfer worth carrying."""

    model_config = ConfigDict(frozen=True)

    direction: Literal["in", "out"]
    symbol: str = Field(min_length=1)
    # Token contract address on the transaction's chain; `None` for the native
    # coin (ETH has no contract). The engine's price lookups key on it.
    address: str | None = Field(default=None, min_length=1)
    amount: float = Field(gt=0)
    # Zerion's point-in-time USD figures — informational only (see module
    # docstring). `None` when Zerion has no price for the token.
    usd_value: float | None = None
    price: float | None = None

    @field_validator("amount")
    @classmethod
    def _amount_must_be_finite(cls, v: float) -> float:
        # `gt=0` already rejects NaN and negatives; this also rejects +Inf.
        if not math.isfinite(v):
            raise ValueError("transfer amount must be finite (no NaN/Inf)")
        return v

    @field_validator("usd_value", "price")
    @classmethod
    def _usd_figures_finite_and_non_negative(cls, v: float | None) -> float | None:
        if v is None:
            return v
        if not math.isfinite(v):
            raise ValueError("transfer USD figure must be finite (no NaN/Inf)")
        if v < 0:
            raise ValueError("transfer USD figure must be non-negative")
        return v


class TxFee(BaseModel):
    """The transaction's network fee: the fee token, the amount burned, and
    Zerion's point-in-time USD value (informational, like `TxTransfer`'s).
    `amount` may be zero (sponsored / zero-priced L2 transactions)."""

    model_config = ConfigDict(frozen=True)

    symbol: str = Field(min_length=1)
    amount: float = Field(ge=0)
    usd_value: float | None = None

    @field_validator("amount")
    @classmethod
    def _amount_must_be_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("fee amount must be finite (no NaN/Inf)")
        return v

    @field_validator("usd_value")
    @classmethod
    def _usd_value_finite_and_non_negative(cls, v: float | None) -> float | None:
        if v is None:
            return v
        if not math.isfinite(v):
            raise ValueError("fee usd_value must be finite (no NaN/Inf)")
        if v < 0:
            raise ValueError("fee usd_value must be non-negative")
        return v


class TxAct(BaseModel):
    """One semantic act inside a transaction — Zerion's decoded "what happened"
    (`acts[]`: contract + method). `type` is deliberately an open string: it is
    a classification *hint* for the phase-5 mapper, not a contract this boundary
    can close over (Zerion's act vocabulary is undocumented and growing)."""

    model_config = ConfigDict(frozen=True)

    act_id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    contract_address: str | None = Field(default=None, min_length=1)
    method_name: str | None = Field(default=None, min_length=1)


class DecodedTx(BaseModel):
    """A single decoded transaction, normalized and boundary-validated.

    `(mined_at_block, in_block_index)` is the deterministic ordering key
    (ADR-0036: block number then in-block index, never set-iteration).
    `in_block_index` is the adapter-assigned ordinal among the wallet's
    transactions within the same block — sources don't expose a chain-level
    transaction index, so the adapter derives a stable one from its own
    deterministic parse order.
    """

    model_config = ConfigDict(frozen=True)

    chain: Chain
    hash: str = Field(min_length=1)
    operation_type: TxOperationType
    mined_at: datetime
    mined_at_block: int = Field(ge=0)
    in_block_index: int = Field(default=0, ge=0)
    status: TxStatus
    transfers: list[TxTransfer] = Field(default_factory=list)
    fee: TxFee | None = None
    acts: list[TxAct] = Field(default_factory=list)


__all__ = [
    "DecodedTx",
    "TxAct",
    "TxFee",
    "TxOperationType",
    "TxStatus",
    "TxTransfer",
]
