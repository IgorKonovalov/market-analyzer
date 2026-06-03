"""Plan 0032 phase 3 done-when: the wallet-scan job + SSE progress.

Driving the scan with a mocked multi-chain source returns a normalized position
set and emits, in order, `scan_started` → ≥1 `scan_progress` → `scan_completed`
with the position count. A malformed position field fails the scan loud
(`scan_failed`, never a zeroed result) and the call re-raises. The masked wallet
— never the full address — is what reaches the payloads.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest
from pydantic import ValidationError

from market_analyser.data.errors import RateLimitedError, UpstreamUnavailableError
from market_analyser.defi.models import DefiPosition, PositionToken
from market_analyser.defi.scan_job import WalletScanResult, run_wallet_scan
from market_analyser.events import Envelope, EventBus

_WALLET = "0x1111111111111111111111111111111111111111"
_MASKED = "0x1111…1111"


class _FakeSource:
    def __init__(
        self,
        positions: Sequence[DefiPosition] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._positions = list(positions or [])
        self._error = error

    def fetch_positions(self, address: str) -> Sequence[DefiPosition]:
        if self._error is not None:
            raise self._error
        return self._positions


def _position(chain: str, symbol: str = "USDC", usd_value: float = 100.0) -> DefiPosition:
    return DefiPosition(
        position_id=f"{chain}:aave-v3:{symbol}",
        chain=chain,  # type: ignore[arg-type]  # tests pass known-good chains
        protocol="aave-v3",
        kind="lending_supply",
        tokens=[PositionToken(symbol=symbol, address="0xabc", amount=1.0)],
        usd_value=usd_value,
    )


_MULTI_CHAIN = [
    _position("ethereum", "USDC", 1000.0),
    _position("ethereum", "WETH", 500.0),
    _position("base", "cbBTC", 250.0),
    _position("arbitrum", "ARB", 100.0),
]


def _drain(queue: asyncio.Queue[Envelope]) -> list[Envelope]:
    drained: list[Envelope] = []
    while not queue.empty():
        drained.append(queue.get_nowait())
    return drained


def _run_scan(
    source: _FakeSource,
) -> tuple[WalletScanResult | None, BaseException | None, list[Envelope]]:
    async def run() -> tuple[WalletScanResult | None, BaseException | None, list[Envelope]]:
        bus = EventBus()
        sub = bus.subscribe()
        result: WalletScanResult | None = None
        raised: BaseException | None = None
        try:
            result = await run_wallet_scan(source=source, address=_WALLET, event_bus=bus)
        except BaseException as err:  # test captures any failure to assert on it
            raised = err
        return result, raised, _drain(sub.queue)

    return asyncio.run(run())


def test_success_emits_started_progress_completed_in_order() -> None:
    result, raised, events = _run_scan(_FakeSource(_MULTI_CHAIN))
    assert raised is None
    assert result is not None
    types = [e.type for e in events]
    assert types[0] == "defi.scan_started"
    assert types[-1] == "defi.scan_completed"
    progress = [t for t in types if t == "defi.scan_progress"]
    assert len(progress) >= 1
    # exactly one progress per chain that returned positions (3 chains here)
    assert len(progress) == 3


def test_completed_carries_total_position_count_and_chains() -> None:
    _result, _raised, events = _run_scan(_FakeSource(_MULTI_CHAIN))
    completed = next(e for e in events if e.type == "defi.scan_completed")
    assert completed.payload["position_count"] == 4
    assert completed.payload["chains"] == ["ethereum", "base", "arbitrum"]


def test_result_holds_normalized_positions() -> None:
    result, _raised, _events = _run_scan(_FakeSource(_MULTI_CHAIN))
    assert result is not None
    assert len(result.positions) == 4
    assert result.total_usd_value == pytest.approx(1850.0)
    assert result.chains == ["ethereum", "base", "arbitrum"]


def test_events_carry_masked_wallet_never_full_address() -> None:
    _result, _raised, events = _run_scan(_FakeSource(_MULTI_CHAIN))
    for event in events:
        assert event.payload["wallet"] == _MASKED
        assert _WALLET not in str(event.payload)


def test_progress_is_per_chain() -> None:
    _result, _raised, events = _run_scan(_FakeSource(_MULTI_CHAIN))
    progress = [e for e in events if e.type == "defi.scan_progress"]
    by_chain = {e.payload["chain"]: e.payload["position_count"] for e in progress}
    assert by_chain == {"ethereum": 2, "base": 1, "arbitrum": 1}


def test_malformed_position_fails_loud_and_does_not_zero() -> None:
    """A NaN usd_value is rejected by the DefiPosition boundary; the source raises
    a ValidationError, which the scan surfaces as scan_failed (malformed_response)
    and re-raises — it never returns a zeroed/empty success."""
    try:
        _position("ethereum", usd_value=float("nan"))
    except ValidationError as exc:
        malformed = exc
    else:  # pragma: no cover - the model must reject NaN
        pytest.fail("DefiPosition should reject a NaN usd_value")

    result, raised, events = _run_scan(_FakeSource(error=malformed))
    assert result is None  # no zeroed success
    assert isinstance(raised, ValidationError)
    types = [e.type for e in events]
    assert types == ["defi.scan_started", "defi.scan_failed"]
    failed = next(e for e in events if e.type == "defi.scan_failed")
    assert failed.payload["reason"] == "malformed_response"


def test_rate_limit_maps_to_rate_limited_reason() -> None:
    _result, raised, events = _run_scan(_FakeSource(error=RateLimitedError("zerion: 429")))
    assert isinstance(raised, RateLimitedError)
    failed = next(e for e in events if e.type == "defi.scan_failed")
    assert failed.payload["reason"] == "rate_limited"


def test_upstream_error_maps_to_unavailable_reason() -> None:
    _result, raised, events = _run_scan(_FakeSource(error=UpstreamUnavailableError("zerion: 500")))
    assert isinstance(raised, UpstreamUnavailableError)
    failed = next(e for e in events if e.type == "defi.scan_failed")
    assert failed.payload["reason"] == "upstream_unavailable"


def test_no_completed_event_on_failure() -> None:
    _result, _raised, events = _run_scan(_FakeSource(error=UpstreamUnavailableError("down")))
    assert all(e.type != "defi.scan_completed" for e in events)
