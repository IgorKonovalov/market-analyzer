"""Plan 0035 phase 7: the wallet-P&L job + `defi.pnl_*` SSE lifecycle.

Driving the job with fake sources over a real in-memory cache emits, in order,
`pnl_started` → `pnl_completed` (with honest totals), returns the engine's
result carrying the advisory cross-check, and on failure emits `pnl_failed`
with a typed reason and re-raises — never a zeroed result. The masked wallet —
never the full address — is what reaches the payloads. The cross-check is
best-effort (a failing cross-check source never fails the reconstruction) and
`crosscheck_warning` flips only on gross divergence.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from market_analyser.data.adapters.defillama import token_key
from market_analyser.data.errors import RateLimitedError
from market_analyser.defi.models import Chain, DefiPosition, PositionToken
from market_analyser.defi.pnl import WalletPnl
from market_analyser.defi.pnl_job import run_wallet_pnl
from market_analyser.defi.tx_models import DecodedTx
from market_analyser.events import Envelope, EventBus
from market_analyser.persistence.defi_tx_repository import DefiTxRepository
from market_analyser.persistence.engine import (
    apply_migrations,
    make_engine,
    make_session_factory,
)

_WALLET = "0x2222222222222222222222222222222222222222"
_MASKED = "0x2222…2222"
_POOL = "0xpool0000000000000000000000000000000000001"
_WETH = "0x4200000000000000000000000000000000000006"
_USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
_TS1 = datetime(2025, 1, 1, tzinfo=UTC)


@pytest.fixture
def session_factory() -> Iterator[sessionmaker[Session]]:
    engine = make_engine(":memory:")
    apply_migrations(engine)
    yield make_session_factory(engine)
    engine.dispose()


_DEPOSIT_TX = DecodedTx.model_validate(
    {
        "chain": "base",
        "hash": "0xadd",
        "operation_type": "deposit",
        "mined_at": _TS1,
        "mined_at_block": 100,
        "status": "confirmed",
        "transfers": [
            {"direction": "out", "symbol": "WETH", "address": _WETH, "amount": 0.2},
            {"direction": "out", "symbol": "USDC", "address": _USDC, "amount": 700.0},
        ],
        "acts": [{"act_id": "a1", "type": "deposit", "contract_address": _POOL}],
    }
)

_LP = DefiPosition(
    position_id="base:aerodrome:lp-1",
    chain="base",
    protocol="aerodrome",
    kind="lp",
    tokens=[
        PositionToken(symbol="WETH", address=_WETH, amount=0.2),
        PositionToken(symbol="USDC", address=_USDC, amount=700.0),
    ],
    usd_value=1500.0,
    pool="WETH / USDC",
    pool_address=_POOL,
)


class _FakeTxSource:
    def __init__(self, txs: list[DecodedTx] | None = None, error: Exception | None = None) -> None:
        self._txs = txs or []
        self._error = error

    def fetch_transactions(
        self, address: str, *, min_mined_at: datetime | None = None
    ) -> list[DecodedTx]:
        if self._error is not None:
            raise self._error
        return self._txs


class _FakePositionsSource:
    def __init__(self, positions: list[DefiPosition]) -> None:
        self._positions = positions

    def fetch_positions(self, address: str) -> list[DefiPosition]:
        return self._positions


class _TablePriceSource:
    def __init__(self, table: dict[tuple[str, int], float]) -> None:
        self._table = table

    def fetch_price(self, *, chain: Chain, address: str | None, ts: int) -> float | None:
        return self._table.get((token_key(chain, address), ts))


class _FakeCrosscheck:
    def __init__(self, total: float | None = None, error: Exception | None = None) -> None:
        self._total = total
        self._error = error

    def fetch_pnl_total(self, address: str) -> float | None:
        if self._error is not None:
            raise self._error
        return self._total


_PRICES = _TablePriceSource(
    {
        (f"base:{_WETH}", int(_TS1.timestamp())): 3500.0,
        (f"base:{_USDC}", int(_TS1.timestamp())): 1.0,
    }
)


def _drain(queue: asyncio.Queue[Envelope]) -> list[Envelope]:
    drained: list[Envelope] = []
    while not queue.empty():
        drained.append(queue.get_nowait())
    return drained


def _run_job(
    session_factory: sessionmaker[Session],
    *,
    tx_source: Any | None = None,
    crosscheck: Any | None = None,
) -> tuple[WalletPnl | None, BaseException | None, list[Envelope]]:
    async def run() -> tuple[WalletPnl | None, BaseException | None, list[Envelope]]:
        bus = EventBus()
        sub = bus.subscribe()
        result: WalletPnl | None = None
        raised: BaseException | None = None
        try:
            result = await run_wallet_pnl(
                tx_source=tx_source if tx_source is not None else _FakeTxSource([_DEPOSIT_TX]),
                positions_source=_FakePositionsSource([_LP]),
                price_source=_PRICES,
                tx_repository=DefiTxRepository(session_factory),
                event_bus=bus,
                address=_WALLET,
                crosscheck_source=crosscheck,
            )
        except BaseException as err:
            raised = err
        return result, raised, _drain(sub.queue)

    return asyncio.run(run())


def test_success_emits_started_then_completed_with_honest_totals(
    session_factory: sessionmaker[Session],
) -> None:
    result, raised, events = _run_job(session_factory)
    assert raised is None
    assert result is not None
    assert [e.type for e in events] == ["defi.pnl_started", "defi.pnl_completed"]
    completed = events[-1]
    # Basis 1400 (0.2*3500 + 700*1); usd_value 1500 → unrealized 100.
    assert completed.payload["position_count"] == 1
    assert completed.payload["incomplete_count"] == 0
    assert completed.payload["realized_usd"] == 0.0
    assert completed.payload["unrealized_usd"] == 100.0
    assert result.positions[0].cost_basis_usd == 1400.0
    # vs-HODL anchors at the newest cached tx timestamp (input-derived, no
    # wall clock): contributed amounts at _TS1 are worth exactly the basis.
    assert result.positions[0].vs_hodl_usd == 100.0


def test_events_carry_masked_wallet_never_full_address(
    session_factory: sessionmaker[Session],
) -> None:
    _result, _raised, events = _run_job(session_factory)
    for event in events:
        assert event.payload["wallet"] == _MASKED
        assert _WALLET not in str(event.payload)


def test_failure_emits_pnl_failed_with_typed_reason_and_reraises(
    session_factory: sessionmaker[Session],
) -> None:
    result, raised, events = _run_job(
        session_factory, tx_source=_FakeTxSource(error=RateLimitedError("throttled"))
    )
    assert result is None, "a failed reconstruction must never return a zeroed result"
    assert isinstance(raised, RateLimitedError)
    assert [e.type for e in events] == ["defi.pnl_started", "defi.pnl_failed"]
    assert events[-1].payload["reason"] == "rate_limited"


def test_crosscheck_rides_along_and_close_totals_do_not_warn(
    session_factory: sessionmaker[Session],
) -> None:
    result, _raised, _events = _run_job(session_factory, crosscheck=_FakeCrosscheck(total=105.0))
    assert result is not None
    assert result.crosscheck_zerion_total == 105.0
    # Ours = 0 + 100 = 100; 105 vs 100 is method noise, not gross divergence.
    assert result.crosscheck_warning is False


def test_gross_divergence_sets_the_warning(session_factory: sessionmaker[Session]) -> None:
    result, _raised, _events = _run_job(session_factory, crosscheck=_FakeCrosscheck(total=5000.0))
    assert result is not None
    assert result.crosscheck_warning is True


def test_sign_flip_on_material_totals_sets_the_warning(
    session_factory: sessionmaker[Session],
) -> None:
    result, _raised, _events = _run_job(session_factory, crosscheck=_FakeCrosscheck(total=-500.0))
    assert result is not None
    assert result.crosscheck_warning is True


def test_crosscheck_failure_is_best_effort_never_fails_the_reconstruction(
    session_factory: sessionmaker[Session],
) -> None:
    result, raised, events = _run_job(
        session_factory, crosscheck=_FakeCrosscheck(error=RuntimeError("pnl endpoint down"))
    )
    assert raised is None
    assert result is not None
    assert result.crosscheck_zerion_total is None
    assert result.crosscheck_warning is False
    assert [e.type for e in events] == ["defi.pnl_started", "defi.pnl_completed"]


def test_second_run_replays_the_cache_with_zero_source_fetches(
    session_factory: sessionmaker[Session],
) -> None:
    """The deterministic re-run path end to end: the second job run reads the
    immutable cache (default refresh=False) and produces byte-identical output."""

    class _CountingTxSource(_FakeTxSource):
        def __init__(self) -> None:
            super().__init__([_DEPOSIT_TX])
            self.calls = 0

        def fetch_transactions(
            self, address: str, *, min_mined_at: datetime | None = None
        ) -> list[DecodedTx]:
            self.calls += 1
            return super().fetch_transactions(address, min_mined_at=min_mined_at)

    source = _CountingTxSource()
    first, _r1, _e1 = _run_job(session_factory, tx_source=source)
    second, _r2, _e2 = _run_job(session_factory, tx_source=source)
    assert source.calls == 1, "the warm cache must serve the second run untouched"
    assert first is not None and second is not None
    assert first.model_dump_json() == second.model_dump_json()
