"""Plan 0107 phase 4 — offline tests for the Aerodrome-native fundamentals reader.

A fake JSON-RPC transport (the monkeypatched `ResilientHttpClient._perform_request`)
returns per-selector `eth_call` results, so the suite never touches the network and
never needs a real Base RPC. A fake secrets store supplies the Base RPC URL.

Pins the phase-4 done-when:
(a) emission-decay + ve/gauge (vote-weight) parse from mocked eth_call with correct
    units;
(b) a read failure (revert / absent RPC) degrades to DefiLlama depth — `None` + a
    note, never an exception, never a zero;
(c) determinism of the parse (same mocked reads → identical output).

Plus a selector self-check (each pinned selector == keccak256(signature)[:4], the
only ground truth) and a best-effort-partial case (some getters revert, the rest
still parse).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from market_analyser.data import adapters
from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data.adapters.aerodrome_native import AerodromeNativeReader
from tests.data.test_lp_detail_adapter import _selector

_RPC_URL = "https://base.example/rpc"
_WAD = 1_000_000_000_000_000_000  # 1e18

# Selectors, keyed by the getter they encode — used to route the fake transport.
_SEL = {
    "weekly": "0x26cfc17b",
    "decay": "0xea64743d",
    "epoch": "0x829965cc",
    "tail": "0x9ba6f976",
    "supply": "0x047fc9aa",
    "total_supply": "0x18160ddd",
    "total_weight": "0x96c82e57",
}

# A full happy-path read: raw on-chain uint256 values (pre-scaling).
_HAPPY_RAW = {
    "weekly": 10_000_000 * _WAD,
    "decay": 9_900,
    "epoch": 100,
    "tail": 67,
    "supply": 500_000_000 * _WAD,
    "total_supply": 300_000_000 * _WAD,
    "total_weight": 250_000_000 * _WAD,
}


class _FakeSecrets:
    """Minimal `SecretsStore` stand-in: returns the Base RPC URL (or None)."""

    def __init__(self, base_rpc_url: str | None = _RPC_URL) -> None:
        self._url = base_rpc_url

    def get(self, key: str) -> str | None:
        return self._url if key == "base_rpc_url" else None


def _word(value: int) -> str:
    return "0x" + value.to_bytes(32, "big").hex()


def _selector_of(body: bytes) -> str:
    payload = json.loads(body)
    return str(payload["params"][0]["data"])[:10]


def _reader(
    monkeypatch: pytest.MonkeyPatch,
    *,
    raw: dict[str, int],
    revert: set[str] = frozenset(),  # type: ignore[assignment]
    base_rpc_url: str | None = _RPC_URL,
) -> AerodromeNativeReader:
    """Wire a reader whose transport returns `raw[getter]` for each selector, or a
    JSON-RPC revert for getters listed in `revert` (or absent from `raw`)."""
    client = ResilientHttpClient(source_name="aero-native-test", max_retries=0)
    by_selector = {sel: name for name, sel in _SEL.items()}

    def fake(method: str, url: str, body: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        name = by_selector.get(_selector_of(body), "")
        if name in revert or name not in raw:
            payload = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "reverted"}}
        else:
            payload = {"jsonrpc": "2.0", "id": 1, "result": _word(raw[name])}
        return HttpResponse(
            status_code=200, headers={}, body=json.dumps(payload).encode(), elapsed_seconds=0.0
        )

    monkeypatch.setattr(client, "_perform_request", fake)
    return AerodromeNativeReader(
        secrets_store=_FakeSecrets(base_rpc_url),
        http_client=client,
        inter_request_seconds=0.0,
        sleep=lambda _s: None,
    )


# -- (a) parse with correct units -------------------------------------------


def test_happy_path_parses_emissions_and_ve_gauge(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = _reader(monkeypatch, raw=_HAPPY_RAW)
    notes: list[str] = []

    emissions, ve_gauge = reader.read_aerodrome(notes)

    assert emissions is not None
    assert emissions.weekly_emission == pytest.approx(10_000_000.0)  # scaled from wei
    assert emissions.weekly_decay_pct == pytest.approx(1.0)  # (10000 - 9900) / 100
    assert emissions.epoch == 100
    assert emissions.tail_emission_rate == pytest.approx(67.0)

    assert ve_gauge is not None
    assert ve_gauge.ve_total_locked == pytest.approx(500_000_000.0)
    assert ve_gauge.ve_total_voting_power == pytest.approx(300_000_000.0)
    assert ve_gauge.total_vote_weight == pytest.approx(250_000_000.0)

    assert notes == []  # a clean full read adds no degrade note


# -- (b) read failure degrades to DefiLlama depth ---------------------------


def test_absent_rpc_degrades_to_none_with_note(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = _reader(monkeypatch, raw=_HAPPY_RAW, base_rpc_url=None)
    notes: list[str] = []

    emissions, ve_gauge = reader.read_aerodrome(notes)  # must not raise

    assert emissions is None
    assert ve_gauge is None
    assert any("Base RPC not configured" in n for n in notes)


def test_minter_revert_degrades_emissions_only(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = _reader(monkeypatch, raw=_HAPPY_RAW, revert={"weekly"})
    notes: list[str] = []

    emissions, ve_gauge = reader.read_aerodrome(notes)

    assert emissions is None  # weekly() reverted → no emissions, no zero
    assert any("Minter weekly()" in n for n in notes)
    assert ve_gauge is not None  # ve/gauge unaffected


def test_all_ve_gauge_reverts_degrades_ve_only(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = _reader(monkeypatch, raw=_HAPPY_RAW, revert={"supply", "total_supply", "total_weight"})
    notes: list[str] = []

    emissions, ve_gauge = reader.read_aerodrome(notes)

    assert ve_gauge is None
    assert any("VotingEscrow/Voter" in n for n in notes)
    assert emissions is not None  # emissions unaffected


def test_zero_weekly_emission_is_honest_null(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = {**_HAPPY_RAW, "weekly": 0}
    reader = _reader(monkeypatch, raw=raw)
    notes: list[str] = []

    emissions, _ = reader.read_aerodrome(notes)

    assert emissions is None  # 0 is not an emission — honest-null, not a zeroed field
    assert any("returned 0" in n for n in notes)


# -- best-effort partial ----------------------------------------------------


def test_partial_optional_reads_still_parse_core(monkeypatch: pytest.MonkeyPatch) -> None:
    # weekly() succeeds but the optional decay/epoch/tail revert; supply() succeeds
    # but the other ve reads revert. The core facts survive; the rest are None.
    reader = _reader(
        monkeypatch,
        raw=_HAPPY_RAW,
        revert={"decay", "epoch", "tail", "total_supply", "total_weight"},
    )
    notes: list[str] = []

    emissions, ve_gauge = reader.read_aerodrome(notes)

    assert emissions is not None
    assert emissions.weekly_emission == pytest.approx(10_000_000.0)
    assert emissions.weekly_decay_pct is None
    assert emissions.epoch is None
    assert emissions.tail_emission_rate is None

    assert ve_gauge is not None
    assert ve_gauge.ve_total_locked == pytest.approx(500_000_000.0)
    assert ve_gauge.ve_total_voting_power is None
    assert ve_gauge.total_vote_weight is None


# -- (c) determinism --------------------------------------------------------


def test_parse_is_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = _reader(monkeypatch, raw=_HAPPY_RAW)

    first_e, first_v = reader.read_aerodrome([])
    second_e, second_v = reader.read_aerodrome([])

    assert first_e == second_e
    assert first_v == second_v


# -- selector self-check (keccak is the only ground truth) ------------------


def test_selectors_match_keccak_signatures() -> None:
    from market_analyser.data.adapters import aerodrome_native as mod

    assert _selector("weekly()") == mod._SEL_WEEKLY
    assert _selector("WEEKLY_DECAY()") == mod._SEL_WEEKLY_DECAY
    assert _selector("epochCount()") == mod._SEL_EPOCH_COUNT
    assert _selector("tailEmissionRate()") == mod._SEL_TAIL_RATE
    assert _selector("supply()") == mod._SEL_SUPPLY
    assert _selector("totalSupply()") == mod._SEL_TOTAL_SUPPLY
    assert _selector("totalWeight()") == mod._SEL_TOTAL_WEIGHT


def test_module_is_importable_via_adapters_package() -> None:
    # Guards the package export path the composition root imports from.
    assert hasattr(adapters, "__name__")
