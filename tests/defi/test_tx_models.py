"""Plan 0035 phase 1: `DecodedTx` / `TxTransfer` / `TxFee` / `TxAct` boundary
validation + the `TxHistorySource` Protocol.

The model is the "no garbage past the boundary" gate for the P&L pipeline
(ADR-0036 "loud failure"): a NaN / negative transfer `usd_value` and a
non-positive / non-finite `amount` are rejected at construction, never coerced
to zero. `operation_type` is a closed vocabulary — an unlisted upstream string
is a `ValidationError` here, so the adapter must normalize to `"unknown"`
rather than pass raw values through.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from market_analyser.data.sources import TxHistorySource
from market_analyser.defi.tx_models import DecodedTx, TxAct, TxFee, TxTransfer

# A representative decoded transaction in the survey's §3-group-B shape, already
# normalized to the adapter's output currency (Plan 0035 phase 2 does Zerion's
# JSON:API → this mapping; the model consumes this).
_SURVEY_SHAPED_TX: dict[str, Any] = {
    "chain": "base",
    "hash": "0xabc123",
    "operation_type": "deposit",
    "mined_at": "2025-11-02T14:31:07+00:00",
    "mined_at_block": 22412345,
    "in_block_index": 0,
    "status": "confirmed",
    "transfers": [
        {
            "direction": "out",
            "symbol": "USDC",
            "address": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "amount": 1500.0,
            "usd_value": 1500.12,
            "price": 1.0001,
        },
        {
            "direction": "out",
            "symbol": "WETH",
            "address": "0x4200000000000000000000000000000000000006",
            "amount": 0.42,
            "usd_value": 1499.88,
            "price": 3571.14,
        },
    ],
    "fee": {"symbol": "ETH", "amount": 0.000021, "usd_value": 0.07},
    "acts": [
        {
            "act_id": "act-1",
            "type": "deposit",
            "contract_address": "0xcdac0d6c6c59727a65f871236188350531885c43",
            "method_name": "mint",
        },
    ],
}


def _transfer(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "direction": "in",
        "symbol": "AERO",
        "address": "0x940181a94a35a4569e4529a3cdfb74e38fd98631",
        "amount": 10.0,
        "usd_value": 12.5,
    }
    base.update(overrides)
    return base


def test_decoded_tx_constructs_from_survey_shaped_dict() -> None:
    tx = DecodedTx.model_validate(_SURVEY_SHAPED_TX)
    assert tx.chain == "base"
    assert tx.operation_type == "deposit"
    assert tx.mined_at == datetime(2025, 11, 2, 14, 31, 7, tzinfo=UTC)
    assert tx.mined_at_block == 22412345
    assert [t.symbol for t in tx.transfers] == ["USDC", "WETH"]
    assert tx.transfers[0].direction == "out"
    assert tx.fee is not None
    assert tx.fee.symbol == "ETH"
    assert tx.acts[0].method_name == "mint"


def test_transfers_fee_and_acts_are_optional() -> None:
    """An approve carries no transfers; some sources omit fee/acts. The model
    defaults them empty/None rather than requiring padding."""
    tx = DecodedTx(
        chain="ethereum",
        hash="0xdef",
        operation_type="approve",
        mined_at=datetime(2025, 1, 1, tzinfo=UTC),
        mined_at_block=100,
        status="confirmed",
    )
    assert tx.transfers == []
    assert tx.fee is None
    assert tx.acts == []
    assert tx.in_block_index == 0


@pytest.mark.parametrize("bad_usd_value", [float("nan"), float("inf"), -1.0])
def test_transfer_rejects_nan_inf_negative_usd_value(bad_usd_value: float) -> None:
    with pytest.raises(ValidationError):
        TxTransfer.model_validate(_transfer(usd_value=bad_usd_value))


@pytest.mark.parametrize("bad_amount", [0.0, -5.0, float("nan"), float("inf")])
def test_transfer_rejects_non_positive_or_non_finite_amount(bad_amount: float) -> None:
    with pytest.raises(ValidationError):
        TxTransfer.model_validate(_transfer(amount=bad_amount))


def test_transfer_usd_value_none_is_allowed() -> None:
    """Zerion has no price for some tokens; `None` is honest (the engine
    re-prices at block time anyway). Zero-as-placeholder is what's forbidden."""
    transfer = TxTransfer.model_validate(_transfer(usd_value=None))
    assert transfer.usd_value is None


def test_transfer_native_coin_address_none_is_allowed() -> None:
    transfer = TxTransfer.model_validate(_transfer(symbol="ETH", address=None))
    assert transfer.address is None


def test_operation_type_vocabulary_is_closed() -> None:
    """An unlisted upstream operation type must be normalized to `unknown` by
    the adapter — the model rejects a raw passthrough."""
    with pytest.raises(ValidationError):
        DecodedTx.model_validate({**_SURVEY_SHAPED_TX, "operation_type": "burn"})
    tx = DecodedTx.model_validate({**_SURVEY_SHAPED_TX, "operation_type": "unknown"})
    assert tx.operation_type == "unknown"


@pytest.mark.parametrize("bad_fee_amount", [-0.1, float("nan"), float("inf")])
def test_fee_rejects_negative_or_non_finite_amount(bad_fee_amount: float) -> None:
    with pytest.raises(ValidationError):
        TxFee(symbol="ETH", amount=bad_fee_amount, usd_value=0.05)


def test_fee_zero_amount_is_allowed() -> None:
    fee = TxFee(symbol="ETH", amount=0.0, usd_value=None)
    assert fee.amount == 0.0


def test_act_requires_non_empty_id_and_type() -> None:
    with pytest.raises(ValidationError):
        TxAct(act_id="", type="deposit")
    with pytest.raises(ValidationError):
        TxAct(act_id="act-1", type="")


def test_decoded_tx_is_frozen() -> None:
    tx = DecodedTx.model_validate(_SURVEY_SHAPED_TX)
    with pytest.raises(ValidationError):
        tx.mined_at_block = 1


class _FakeTxHistorySource:
    """Structurally satisfies `TxHistorySource` without inheriting from it."""

    def fetch_transactions(
        self,
        address: str,
        *,
        min_mined_at: datetime | None = None,
    ) -> Sequence[DecodedTx]:
        return [DecodedTx.model_validate(_SURVEY_SHAPED_TX)]


def test_tx_history_source_is_runtime_checkable() -> None:
    assert isinstance(_FakeTxHistorySource(), TxHistorySource)
    assert not isinstance(object(), TxHistorySource)
