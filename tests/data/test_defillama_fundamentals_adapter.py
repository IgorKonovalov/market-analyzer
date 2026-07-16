"""Plan 0107 phase 1 — offline tests for the keyless DefiLlama fundamentals adapter.

Committed fixtures (the real DefiLlama protocol / dexs / yields shapes) plus inline
payloads drive `DefiLlamaFundamentalsAdapter` through a `ResilientHttpClient` whose
transport seam (`_perform_request`) is monkeypatched to a per-URL router, so the suite
never touches the network. `_now` is frozen for a deterministic `as_of`.

Pins the phase-1 done-when:
(a) each field parses with correct units off the fixtures,
(b) a missing field → honest `None` with a note, never a zero / fabricated value,
(c) a resilient-path failure / rate-limit on any (or every) endpoint → an empty result
    with notes, never an exception,
(d) mcap vs FDV are distinguished (mcap parsed; FDV honest-null at this tier).

Plus a conditions-only guard (no `action`/`signal`/`recommendation` field) and the
best-effort resolver's degrade for an unregistered query. The single live call is
isolated behind `@pytest.mark.network`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from market_analyser.data._http import HttpResponse, ResilientHttpClient
from market_analyser.data.adapters import defillama_fundamentals
from market_analyser.data.adapters.defillama_fundamentals import DefiLlamaFundamentalsAdapter

_FIXTURES = Path(__file__).parent / "fixtures"
_PROTOCOL_BYTES = (_FIXTURES / "defillama_protocol_aerodrome.json").read_bytes()
_DEXS_BYTES = (_FIXTURES / "defillama_dexs_aerodrome.json").read_bytes()
_YIELDS_BYTES = (_FIXTURES / "defillama_yields_pools.json").read_bytes()

_FROZEN_NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)

# URL fragments that identify each DefiLlama endpoint the adapter calls.
_PROTOCOL = "/protocol/"
_DEXS = "/summary/dexs/"
_EMISSION = "/emission/"
_YIELDS = "yields.llama.fi/pools"

# The keyless emissions endpoint is Pro-gated for AERO (HTTP 402) — model that as
# the default so the happy path exercises the real "unlocks not covered" degrade.
_HAPPY = {
    _PROTOCOL: (200, _PROTOCOL_BYTES),
    _DEXS: (200, _DEXS_BYTES),
    _YIELDS: (200, _YIELDS_BYTES),
    _EMISSION: (402, b'{"message":"Payment required"}'),
}

# Hand-computed off the fixtures.
# TVL history's last point; DEX-volume windows; TVL-weighted APRs over the two
# aerodrome projects (uniswap-v3 excluded): fee = (10*1e6 + 5*3e6)/4e6 = 6.25,
# reward = (20*1e6 + 8*3e6)/4e6 = 11.0.
_EXPECT_TVL = 1_000_000_000.0
_EXPECT_MCAP = 500_000_000.0
_EXPECT_FEE_APR = 6.25
_EXPECT_REWARD_APR = 11.0


def _freeze(monkeypatch: pytest.MonkeyPatch, now: datetime = _FROZEN_NOW) -> None:
    monkeypatch.setattr(defillama_fundamentals, "_now", lambda: now)


def _router(responses: dict[str, tuple[int, bytes]]) -> Callable[..., HttpResponse]:
    def fake(method: str, url: str, body_: Any, headers: Any, *, proxy: Any) -> HttpResponse:
        for fragment, (status, body) in responses.items():
            if fragment in url:
                return HttpResponse(status_code=status, headers={}, body=body, elapsed_seconds=0.0)
        return HttpResponse(status_code=404, headers={}, body=b"{}", elapsed_seconds=0.0)

    return fake


def _adapter(
    monkeypatch: pytest.MonkeyPatch,
    responses: dict[str, tuple[int, bytes]],
) -> DefiLlamaFundamentalsAdapter:
    client = ResilientHttpClient(source_name="defillama-fundamentals-test", max_retries=0)
    monkeypatch.setattr(client, "_perform_request", _router(responses))
    return DefiLlamaFundamentalsAdapter(http_client=client)


# -- (a) each field parses with correct units -------------------------------


def test_happy_path_parses_every_field(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    adapter = _adapter(monkeypatch, _HAPPY)

    f = adapter.fetch_fundamentals("AERO")

    assert f.query == "AERO"
    assert f.source == "defillama"
    assert f.protocol_slug == "aerodrome-v1"
    assert f.as_of == _FROZEN_NOW

    assert f.tvl == pytest.approx(_EXPECT_TVL)
    assert f.tvl_trend is not None and len(f.tvl_trend) == 3
    assert f.tvl_trend[-1].value == pytest.approx(_EXPECT_TVL)
    assert f.tvl_trend[0].date == 1783900000  # epoch seconds, chronological

    assert f.dex_volume is not None
    assert f.dex_volume.volume_24h == pytest.approx(5_000_000.0)
    assert f.dex_volume.volume_7d == pytest.approx(40_000_000.0)
    assert f.dex_volume.volume_30d == pytest.approx(270_000_000.0)
    assert f.dex_volume.change_1d_pct == pytest.approx(-17.5)

    assert f.fee_apr == pytest.approx(_EXPECT_FEE_APR)
    assert f.reward_apr == pytest.approx(_EXPECT_REWARD_APR)

    assert f.mcap == pytest.approx(_EXPECT_MCAP)


def test_yields_excludes_non_matching_projects(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    adapter = _adapter(monkeypatch, _HAPPY)

    f = adapter.fetch_fundamentals("AERO")

    # The uniswap-v3 pool (apyBase/apyReward 99, huge TVL) must not leak into the
    # aerodrome APRs — proves the project filter, not just an average of everything.
    assert f.fee_apr == pytest.approx(_EXPECT_FEE_APR)
    assert f.fee_apr < 99.0


# -- (d) mcap vs FDV distinguished ------------------------------------------


def test_mcap_and_fdv_are_distinct_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    adapter = _adapter(monkeypatch, _HAPPY)

    f = adapter.fetch_fundamentals("AERO")

    # mcap comes from the protocol payload; FDV has no keyless source at this tier,
    # so it is honest-null with a note — never conflated with mcap.
    assert f.mcap == pytest.approx(_EXPECT_MCAP)
    assert f.fdv is None
    assert any("fdv" in note.lower() for note in f.notes)


# -- (b) missing field → honest None + note ---------------------------------


def test_absent_mcap_is_honest_null_with_note(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    # A protocol payload with no `mcap` (AERO's real state: gecko_id-less) — the
    # field must be None with a note, not a zero.
    payload = json.dumps(
        {"symbol": "AERO", "tvl": [{"date": 1784072800, "totalLiquidityUSD": 1000000000.0}]}
    ).encode()
    responses = {**_HAPPY, _PROTOCOL: (200, payload)}
    adapter = _adapter(monkeypatch, responses)

    f = adapter.fetch_fundamentals("AERO")

    assert f.tvl == pytest.approx(_EXPECT_TVL)  # other protocol fields still parse
    assert f.mcap is None
    assert any("mcap" in note.lower() for note in f.notes)


def test_single_endpoint_failure_leaves_others_intact(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    # The dexs endpoint 500s; TVL/mcap and APR must still come through.
    responses = {**_HAPPY, _DEXS: (500, b"boom")}
    adapter = _adapter(monkeypatch, responses)

    f = adapter.fetch_fundamentals("AERO")

    assert f.dex_volume is None
    assert any("dex_volume" in note.lower() for note in f.notes)
    assert f.tvl == pytest.approx(_EXPECT_TVL)
    assert f.fee_apr == pytest.approx(_EXPECT_FEE_APR)


def test_unlocks_pro_gate_degrades_to_honest_null(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    adapter = _adapter(monkeypatch, _HAPPY)  # emission endpoint returns 402

    f = adapter.fetch_fundamentals("AERO")

    assert f.unlocks is None
    assert any("unlocks" in note.lower() for note in f.notes)


def test_unlocks_present_when_emissions_endpoint_carries_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze(monkeypatch)
    emission = json.dumps(
        {
            "metadata": {
                "events": [
                    {"timestamp": 1790000000, "noOfTokens": [1000000], "category": "team"},
                    {"timestamp": 1795000000, "noOfTokens": [2000000], "description": "liquidity"},
                ]
            }
        }
    ).encode()
    responses = {**_HAPPY, _EMISSION: (200, emission)}
    adapter = _adapter(monkeypatch, responses)

    f = adapter.fetch_fundamentals("AERO")

    assert f.unlocks is not None and len(f.unlocks) == 2
    assert f.unlocks[0].date == 1790000000
    assert f.unlocks[0].tokens == pytest.approx(1000000.0)
    assert f.unlocks[0].category == "team"
    assert f.unlocks[1].category == "liquidity"


# -- (c) whole-source failure → empty, never an exception -------------------


def test_all_endpoints_failing_returns_empty_not_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze(monkeypatch)
    responses = {
        _PROTOCOL: (429, b"rate limited"),
        _DEXS: (500, b"boom"),
        _YIELDS: (503, b"down"),
        _EMISSION: (402, b"pay"),
    }
    adapter = _adapter(monkeypatch, responses)

    f = adapter.fetch_fundamentals("AERO")  # must not raise

    assert f.query == "AERO"
    assert f.tvl is None
    assert f.tvl_trend is None
    assert f.dex_volume is None
    assert f.fee_apr is None
    assert f.reward_apr is None
    assert f.mcap is None
    assert f.fdv is None
    assert f.unlocks is None
    # Every gap is explained, never silent.
    assert len(f.notes) >= 4


def test_rate_limited_yields_is_honest_null(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    responses = {**_HAPPY, _YIELDS: (429, b"slow down")}
    adapter = _adapter(monkeypatch, responses)

    f = adapter.fetch_fundamentals("AERO")

    assert f.fee_apr is None
    assert f.reward_apr is None
    assert f.tvl == pytest.approx(_EXPECT_TVL)  # unaffected


def test_non_json_body_degrades_that_field(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    # A 200 with an HTML body on the protocol endpoint: expect_json makes the client
    # exhaust to a ResilientHttpError, which the adapter turns into an honest-null.
    responses = {**_HAPPY, _PROTOCOL: (200, b"<html>blocked</html>")}
    adapter = _adapter(monkeypatch, responses)

    f = adapter.fetch_fundamentals("AERO")

    assert f.tvl is None
    assert f.mcap is None
    assert f.dex_volume is not None  # other endpoints unaffected


# -- resolver degrade for an unregistered query -----------------------------


def test_unregistered_query_uses_slug_and_nulls_apr(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    # "uniswap" is not in the registry → best-effort guessed ref: slug=query, and no
    # yields project configured, so APR is honest-null with a note; TVL/volume still
    # resolve via the guessed slug.
    adapter = _adapter(monkeypatch, _HAPPY)

    f = adapter.fetch_fundamentals("uniswap")

    assert f.protocol_slug == "uniswap"
    assert f.fee_apr is None
    assert f.reward_apr is None
    assert any("yields project" in note.lower() for note in f.notes)


# -- conditions-only guard (ADR-0029) ---------------------------------------


def test_model_carries_no_call_shaped_field(monkeypatch: pytest.MonkeyPatch) -> None:
    _freeze(monkeypatch)
    adapter = _adapter(monkeypatch, _HAPPY)

    dumped = adapter.fetch_fundamentals("AERO").model_dump()

    for forbidden in ("action", "signal", "recommendation", "direction", "conviction"):
        assert forbidden not in dumped


# -- input validation -------------------------------------------------------


def test_empty_query_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _adapter(monkeypatch, _HAPPY)

    with pytest.raises(ValueError, match="non-empty"):
        adapter.fetch_fundamentals("   ")


# -- live smoke -------------------------------------------------------------


@pytest.mark.network
def test_live_fetch_returns_coherent_fundamentals() -> None:
    f = DefiLlamaFundamentalsAdapter().fetch_fundamentals("AERO")

    assert f.query == "AERO"
    assert f.source == "defillama"
    # TVL/volume are covered keyless; any gap is noted, never fabricated.
    if f.tvl is not None:
        assert f.tvl > 0
    assert f.fdv is None  # never fabricated at this tier
