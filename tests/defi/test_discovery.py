"""Plan 0032 phase 3: the discovery service + wallet masking.

The service normalizes a source's positions into one set with a deterministic
per-chain breakdown and total value, and propagates source errors unchanged
(never swallowing them into an empty/zeroed result).
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from market_analyser.data.errors import UpstreamUnavailableError
from market_analyser.defi.discovery import DiscoveryService, mask_wallet
from market_analyser.defi.models import DefiPosition, PositionToken

_WALLET = "0x1111111111111111111111111111111111111111"


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


def _position(chain: str, usd_value: float, symbol: str = "USDC") -> DefiPosition:
    return DefiPosition(
        position_id=f"{chain}:aave-v3:{symbol}",
        chain=chain,  # type: ignore[arg-type]  # tests pass known-good chains
        protocol="aave-v3",
        kind="lending_supply",
        tokens=[PositionToken(symbol=symbol, address="0xabc", amount=1.0)],
        usd_value=usd_value,
    )


def test_mask_wallet_keeps_head_and_tail() -> None:
    assert mask_wallet(_WALLET) == "0x1111…1111"


def test_mask_wallet_passes_through_short_string() -> None:
    assert mask_wallet("0x1234") == "0x1234"


def test_discover_returns_positions_chains_and_total() -> None:
    source = _FakeSource(
        [
            _position("ethereum", 1000.0),
            _position("ethereum", 500.0, symbol="WETH"),
            _position("base", 250.0),
            _position("arbitrum", 100.0),
        ]
    )
    result = DiscoveryService(source).discover(_WALLET)
    assert len(result.positions) == 4
    # first-seen order, deterministic
    assert result.chains == ["ethereum", "base", "arbitrum"]
    assert result.total_usd_value == pytest.approx(1850.0)


def test_discover_empty_wallet_is_empty_not_an_error() -> None:
    result = DiscoveryService(_FakeSource([])).discover(_WALLET)
    assert result.positions == []
    assert result.chains == []
    assert result.total_usd_value == 0.0


def test_discover_propagates_source_error() -> None:
    source = _FakeSource(error=UpstreamUnavailableError("zerion: down"))
    with pytest.raises(UpstreamUnavailableError):
        DiscoveryService(source).discover(_WALLET)
