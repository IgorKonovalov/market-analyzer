"""Plan 0099 phase 1 — the pure dwell reducer + position-watch boundary types
(ADR-0093).

Done-when claims pinned here:
(a) the reducer does not fire while in range;
(b) it does not fire on the first out-of-range observation (which is also the
    conservative post-restart rule: unknown downtime starts a fresh dwell);
(c) it fires exactly once after the position has been continuously out of
    range for >= dwell — later out-of-range observations do not re-fire;
(d) re-entry into range resets and re-arms, so a fresh excursion can fire
    again (and a transient one-tick excursion never fires);
(e) dwell state survives a simulated restart — the reducer fires from a
    `DwellState` reconstructed from persisted values, not process memory.

Plus the ADR-0029 boundary at the model level: `DefiPositionAlert` is
condition-facts-only and structurally rejects an advice-shaped field.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from market_analyser.defi.position_watch import (
    DefiPositionAlert,
    DefiPositionWatch,
    DwellState,
    evaluate_position_dwell,
    validate_evm_address,
)

# Synthetic placeholder addresses — never a real wallet (public repo).
WALLET = "0x" + "ab" * 20
POOL = "0x" + "cd" * 20

T0 = datetime(2026, 7, 16, 3, 0, tzinfo=UTC)
DWELL = timedelta(hours=6)


def _tick(state: DwellState, *, in_range: bool, at: datetime) -> tuple[DwellState, bool]:
    return evaluate_position_dwell(state, in_range=in_range, now=at, dwell=DWELL)


class TestReducerInRange:
    def test_in_range_never_fires_and_stays_reset(self) -> None:
        state = DwellState()
        for hours in range(0, 48, 6):
            state, fired = _tick(state, in_range=True, at=T0 + timedelta(hours=hours))
            assert fired is False
            assert state == DwellState()


class TestReducerFirstObservation:
    def test_first_out_of_range_observation_starts_dwell_without_firing(self) -> None:
        state, fired = _tick(DwellState(), in_range=False, at=T0)
        assert fired is False
        assert state == DwellState(out_since=T0, fired=False)

    def test_first_observation_does_not_fire_even_past_dwell_after_downtime(self) -> None:
        # Sidecar was down while the position drifted out: the first
        # post-restart observation starts a fresh dwell (conservative),
        # regardless of how long the outage was — it never fires immediately.
        state, fired = _tick(DwellState(), in_range=False, at=T0 + timedelta(days=3))
        assert fired is False
        assert state.out_since == T0 + timedelta(days=3)


class TestReducerFiresOnceAfterDwell:
    def test_fires_exactly_once_after_continuous_dwell(self) -> None:
        state = DwellState()
        fires: list[datetime] = []
        # 15-minute observation cadence across 8 hours, all out of range.
        for step in range(0, 8 * 4 + 1):
            at = T0 + timedelta(minutes=15 * step)
            state, fired = _tick(state, in_range=False, at=at)
            if fired:
                fires.append(at)
        assert fires == [T0 + DWELL]
        assert state == DwellState(out_since=T0, fired=True)

    def test_does_not_fire_before_dwell_elapsed(self) -> None:
        state, _ = _tick(DwellState(), in_range=False, at=T0)
        state, fired = _tick(state, in_range=False, at=T0 + DWELL - timedelta(seconds=1))
        assert fired is False
        assert state == DwellState(out_since=T0, fired=False)

    def test_fires_at_exactly_the_dwell_threshold(self) -> None:
        state, _ = _tick(DwellState(), in_range=False, at=T0)
        state, fired = _tick(state, in_range=False, at=T0 + DWELL)
        assert fired is True
        assert state == DwellState(out_since=T0, fired=True)

    def test_backwards_clock_does_not_fire(self) -> None:
        state, _ = _tick(DwellState(), in_range=False, at=T0)
        state, fired = _tick(state, in_range=False, at=T0 - timedelta(hours=7))
        assert fired is False
        assert state == DwellState(out_since=T0, fired=False)


class TestReducerReEntry:
    def test_re_entry_resets_and_re_arms(self) -> None:
        state, _ = _tick(DwellState(), in_range=False, at=T0)
        state, fired = _tick(state, in_range=False, at=T0 + DWELL)
        assert fired is True
        # Price re-enters the range: reset...
        state, fired = _tick(state, in_range=True, at=T0 + timedelta(hours=7))
        assert fired is False
        assert state == DwellState()
        # ...and a fresh excursion fires again after its own full dwell.
        t2 = T0 + timedelta(hours=8)
        state, _ = _tick(state, in_range=False, at=t2)
        state, fired = _tick(state, in_range=False, at=t2 + DWELL - timedelta(minutes=1))
        assert fired is False
        state, fired = _tick(state, in_range=False, at=t2 + DWELL)
        assert fired is True

    def test_transient_one_tick_excursion_never_fires(self) -> None:
        state, fired = _tick(DwellState(), in_range=False, at=T0)
        assert fired is False
        state, fired = _tick(state, in_range=True, at=T0 + timedelta(minutes=15))
        assert fired is False
        assert state == DwellState()


class TestReducerRestartPersistence:
    def test_dwell_state_survives_simulated_restart(self) -> None:
        state, _ = _tick(DwellState(), in_range=False, at=T0)
        # "Restart": rebuild the state from its persisted primitive values —
        # nothing carried in process memory.
        persisted = state.model_dump(mode="json")
        revived = DwellState.model_validate(persisted)
        revived, fired = _tick(revived, in_range=False, at=T0 + DWELL)
        assert fired is True
        assert revived.out_since == T0

    def test_fired_latch_survives_simulated_restart(self) -> None:
        state, _ = _tick(DwellState(), in_range=False, at=T0)
        state, _ = _tick(state, in_range=False, at=T0 + DWELL)
        revived = DwellState.model_validate(state.model_dump(mode="json"))
        _, fired = _tick(revived, in_range=False, at=T0 + DWELL + timedelta(hours=1))
        assert fired is False


class TestReducerBoundary:
    def test_naive_now_rejected(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            evaluate_position_dwell(
                DwellState(), in_range=False, now=datetime(2026, 7, 16, 3, 0), dwell=DWELL
            )

    def test_non_positive_dwell_rejected(self) -> None:
        with pytest.raises(ValueError, match="dwell must be positive"):
            evaluate_position_dwell(DwellState(), in_range=False, now=T0, dwell=timedelta(0))

    def test_fired_without_out_since_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DwellState(out_since=None, fired=True)


def _alert_kwargs() -> dict[str, Any]:
    return {
        "id": 1,
        "watch_id": 1,
        "wallet": WALLET,
        "chain": "base",
        "pool_address": POOL,
        "nft_token_id": 42,
        "fired_at": T0 + DWELL,
        "out_since": T0,
        "hours_out": 6.0,
        "tick_lower": -100,
        "tick_upper": 100,
        "current_tick": 150,
        "in_range": False,
        "uncollected_fees": None,
    }


class TestAlertModelBoundary:
    def test_alert_is_condition_facts_only_no_advice_field_possible(self) -> None:
        # ADR-0029: extra="forbid" structurally bars a directive riding along.
        with pytest.raises(ValidationError):
            DefiPositionAlert(**{**_alert_kwargs(), "recommendation": "recenter"})

    def test_alert_field_set_carries_no_directive_vocabulary(self) -> None:
        fields = set(DefiPositionAlert.model_fields)
        assert fields.isdisjoint({"action", "advice", "direction", "recommendation", "size"})

    def test_in_range_true_rejected_at_fire(self) -> None:
        with pytest.raises(ValidationError, match="in_range must be False"):
            DefiPositionAlert(**{**_alert_kwargs(), "in_range": True, "current_tick": 0})

    def test_inverted_ticks_rejected(self) -> None:
        with pytest.raises(ValidationError, match="tick_lower"):
            DefiPositionAlert(**{**_alert_kwargs(), "tick_lower": 100, "tick_upper": -100})


class TestWatchModelBoundary:
    def test_watch_defaults(self) -> None:
        watch = DefiPositionWatch(
            id=1,
            wallet=WALLET,
            chain="base",
            pool_address=POOL,
            nft_token_id=None,
            source="agent",
            created_at=T0,
        )
        assert watch.dwell_hours == 6.0
        assert watch.interval_seconds == 900
        assert watch.enabled is True
        assert watch.dwell_state == DwellState()
        assert watch.dwell == timedelta(hours=6)

    def test_malformed_wallet_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DefiPositionWatch(
                id=1,
                wallet="not-an-address",
                chain="base",
                pool_address=POOL,
                nft_token_id=None,
                source="agent",
                created_at=T0,
            )

    def test_validate_evm_address_helper(self) -> None:
        assert validate_evm_address(WALLET, field="wallet") == WALLET
        with pytest.raises(ValueError, match="pool_address must be an EVM address"):
            validate_evm_address("0x123", field="pool_address")
