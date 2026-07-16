"""Keyless DefiLlama DeFi-fundamentals adapter — Plan 0107 (ADR-0102, ADR-0031, ADR-0019).

Implements `DefiFundamentalsSource.fetch_fundamentals` against DefiLlama's keyless
endpoints, folding a token/protocol's fundamentals into one `DefiFundamentals`
condition read (ADR-0029: conditions only, never a call):

- **TVL + short history + mcap** — `GET api.llama.fi/protocol/{slug}` (the root
  `tvl` history array + the `mcap` field where DefiLlama joins a `gecko_id`).
- **DEX volume** — `GET api.llama.fi/summary/dexs/{slug}` (`total24h/7d/30d`,
  `change_1d`).
- **Fee + reward APR** — `GET yields.llama.fi/pools`, filtered to the asset's
  DefiLlama `project`(s) and TVL-weighted across the protocol's pools
  (`apyBase` → fee APR, `apyReward` → reward APR). This is the one heavy read
  (the pools list is large); a longer TTL amortizes it.
- **Unlock / dilution calendar** — `GET api.llama.fi/emission/{slug}`, best-effort:
  the keyless endpoint is frequently DefiLlama-Pro-gated (AERO returns HTTP 402),
  so a miss degrades to an honest "unlocks not covered" note (ADR-0102 risk #1),
  never a fabricated schedule.

**Honest-degrade, per endpoint (ADR-0019).** Each upstream read is wrapped
independently: a failure (rate-limit, 4xx incl. the Pro-gate 402, transport
exhaustion) or a shape-broken payload leaves *that* field `None` with a `notes`
entry and never touches the others — a single flaky endpoint never fails the
whole read, and nothing is coerced to zero. A field DefiLlama simply does not
cover (AERO's mcap, absent `gecko_id`; FDV, which has no keyless source here)
returns honest-null with a note, so the caller sees the gap explicitly.

**No `as_of`.** These are current-state reads with no reconstructable
point-in-time series (ADR-0102); the result stamps its own wall-clock read time.

Conforms to `DefiFundamentalsSource` (ADR-0031) and is package-internal per
ADR-0007: downstream reaches it through the selector registry + the
`defi_fundamentals` tool, never by importing it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from market_analyser.data._http import ResilientHttpClient, ResilientHttpError
from market_analyser.data.sources import DefiFundamentalsSource
from market_analyser.defi.models import (
    DefiFundamentals,
    FundamentalsPoint,
    UnlockEvent,
    VolumeSummary,
)

_SOURCE = "defillama"

_PROTOCOL_URL = "https://api.llama.fi/protocol/{slug}"
_DEXS_URL = "https://api.llama.fi/summary/dexs/{slug}"
_EMISSION_URL = "https://api.llama.fi/emission/{slug}"
_YIELDS_URL = "https://yields.llama.fi/pools"

# Fundamentals move on a slow (daily-ish) clock; a 5-minute TTL keeps repeat
# reads cheap without going stale. The yields list is large but rides the same
# TTL, so at most one heavy pull per window.
_DEFAULT_TTL_SECONDS = 300.0
_DEFAULT_MAX_CONCURRENCY = 2

# How many trailing TVL-history points to carry as the trend (about a month of
# daily points) — enough to read the shape without shipping the full series.
_TVL_TREND_POINTS = 30

# _USER_AGENT: DefiLlama is lenient, but a descriptive UA is polite (ADR-0019).
_USER_AGENT = "market-analyser/1.0 (keyless DeFi-fundamentals research)"


@dataclass(frozen=True)
class _AssetRef:
    """How one token/protocol maps onto DefiLlama's several keys (ADR-0102: the
    endpoint set becomes a maintained config, like the RSS feed catalog).

    `tvl_slug` / `volume_slug` / `emissions_slug` are the protocol slugs the
    respective endpoints key on; `yields_projects` are the `project` values the
    pools list uses for this protocol (an AMM often spans more than one, e.g.
    Aerodrome's v1 AMM + Slipstream CL)."""

    tvl_slug: str
    volume_slug: str
    emissions_slug: str
    yields_projects: tuple[str, ...] = field(default_factory=tuple)


# Maintained registry keyed by uppercased symbol. AERO is the plan's target; the
# resolver falls back to a best-effort guessed ref for anything else, so the tool
# is chain-agnostic without a per-asset entry (ADR-0102).
_REGISTRY: dict[str, _AssetRef] = {
    "AERO": _AssetRef(
        tvl_slug="aerodrome-v1",
        volume_slug="aerodrome-v1",
        emissions_slug="aerodrome",
        yields_projects=("aerodrome-v1", "aerodrome-slipstream"),
    ),
}


def _resolve(query: str) -> _AssetRef:
    """Map a symbol/protocol `query` to its DefiLlama keys. A known symbol uses
    its registry entry; anything else is a best-effort guess that uses the query
    (lowercased) directly as the slug — so `defi_fundamentals("uniswap")` still
    returns TVL/volume with no registry entry, degrading APR (no `project`s) to a
    note."""
    ref = _REGISTRY.get(query.strip().upper())
    if ref is not None:
        return ref
    slug = query.strip().lower()
    return _AssetRef(tvl_slug=slug, volume_slug=slug, emissions_slug=slug)


class DefiLlamaFundamentalsAdapter(DefiFundamentalsSource):
    """Fetches a token/protocol's fundamentals from DefiLlama's keyless endpoints."""

    def __init__(self, http_client: ResilientHttpClient | None = None) -> None:
        self._http = (
            http_client
            if http_client is not None
            else ResilientHttpClient(
                source_name=_SOURCE,
                cache_ttl_seconds=_DEFAULT_TTL_SECONDS,
                max_concurrency=_DEFAULT_MAX_CONCURRENCY,
                user_agent=_USER_AGENT,
            )
        )

    def fetch_fundamentals(self, query: str) -> DefiFundamentals:
        """Return the DefiLlama-tier fundamentals for `query`, honest-null +
        noted wherever a field is uncovered or an endpoint fails (ADR-0019).
        Never raises for an upstream failure."""
        if not query or not query.strip():
            raise ValueError("query must be a non-empty symbol or protocol slug")
        ref = _resolve(query)
        notes: list[str] = []

        tvl, tvl_trend, mcap = self._read_protocol(ref, notes)
        dex_volume = self._read_volume(ref, notes)
        fee_apr, reward_apr = self._read_apr(ref, notes)
        unlocks = self._read_unlocks(ref, notes)

        # FDV has no keyless DefiLlama source at this tier (it needs total supply);
        # the Aerodrome-native deep tier can compute it from on-chain supply x price
        # (Plan 0107 phases 4-5). Honest-null, noted — never guessed.
        notes.append("fdv: no keyless DefiLlama source at this tier (honest-null)")

        return DefiFundamentals(
            query=query.strip(),
            protocol_slug=ref.tvl_slug,
            tvl=tvl,
            tvl_trend=tvl_trend,
            dex_volume=dex_volume,
            fee_apr=fee_apr,
            reward_apr=reward_apr,
            mcap=mcap,
            fdv=None,
            unlocks=unlocks,
            as_of=_now(),
            source=_SOURCE,
            notes=notes,
        )

    # -- per-endpoint reads, each degrading independently -------------------

    def _read_protocol(
        self, ref: _AssetRef, notes: list[str]
    ) -> tuple[float | None, list[FundamentalsPoint] | None, float | None]:
        payload = self._get_json(_PROTOCOL_URL.format(slug=ref.tvl_slug), "tvl/mcap", notes)
        if payload is None:
            return None, None, None
        trend = _parse_tvl_trend(payload)
        tvl = trend[-1].value if trend else None
        if tvl is None:
            notes.append("tvl: no history in DefiLlama protocol payload (honest-null)")
        mcap = _parse_mcap(payload)
        if mcap is None:
            notes.append("mcap: not covered by DefiLlama for this token (honest-null)")
        return tvl, (trend or None), mcap

    def _read_volume(self, ref: _AssetRef, notes: list[str]) -> VolumeSummary | None:
        payload = self._get_json(_DEXS_URL.format(slug=ref.volume_slug), "dex_volume", notes)
        if payload is None:
            return None
        return _parse_volume(payload)

    def _read_apr(self, ref: _AssetRef, notes: list[str]) -> tuple[float | None, float | None]:
        if not ref.yields_projects:
            notes.append(
                "fee_apr/reward_apr: no DefiLlama yields project configured for "
                f"'{ref.tvl_slug}' (honest-null)"
            )
            return None, None
        payload = self._get_json(_YIELDS_URL, "fee_apr/reward_apr", notes)
        if payload is None:
            return None, None
        fee_apr, reward_apr = _parse_apr(payload, set(ref.yields_projects))
        if fee_apr is None and reward_apr is None:
            notes.append("fee_apr/reward_apr: no matching pools in DefiLlama yields (honest-null)")
        return fee_apr, reward_apr

    def _read_unlocks(self, ref: _AssetRef, notes: list[str]) -> list[UnlockEvent] | None:
        payload = self._get_json(_EMISSION_URL.format(slug=ref.emissions_slug), "unlocks", notes)
        if payload is None:
            # The keyless emissions endpoint is commonly Pro-gated (402) — the
            # miss is already noted by `_get_json`; make the coverage gap explicit.
            notes.append("unlocks: not covered by keyless DefiLlama (honest-null)")
            return None
        events = _parse_unlocks(payload)
        if not events:
            notes.append("unlocks: DefiLlama emissions payload carried no events (honest-null)")
            return None
        return events

    def _get_json(self, url: str, label: str, notes: list[str]) -> Any | None:
        """Fetch + JSON-decode one endpoint, degrading to `None` + a `notes` entry
        on any resilient-path failure (incl. the Pro-gate 402) — never raising."""
        try:
            response = self._http.get(url, expect_json=True)
        except ResilientHttpError as err:
            notes.append(f"{label}: DefiLlama unavailable ({_reason(err)}) — honest-null")
            return None
        try:
            return response.json()
        except ValueError:
            notes.append(f"{label}: DefiLlama returned a non-JSON body — honest-null")
            return None


def _now() -> datetime:
    """Wall-clock seam, monkeypatched by tests to freeze time."""
    return datetime.now(tz=UTC)


def _reason(err: ResilientHttpError) -> str:
    resp = err.last_response
    if resp is not None:
        return f"HTTP {resp.status_code}"
    return type(err.last_exception).__name__ if err.last_exception is not None else "unknown"


def _finite_number(value: Any) -> float | None:
    """A finite float, or `None` for a bool / non-numeric / NaN / Inf value."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    return out if math.isfinite(out) else None


def _non_negative(value: Any) -> float | None:
    """A finite, non-negative float, or `None` (garbage → no coverage, never a
    negative measurement snapshotted into the model)."""
    out = _finite_number(value)
    if out is None or out < 0:
        return None
    return out


def _parse_tvl_trend(payload: Any) -> list[FundamentalsPoint]:
    """The trailing TVL history from a DefiLlama protocol payload's root `tvl`
    array (`[{date, totalLiquidityUSD}, …]`), most-recent last. Malformed points
    are skipped, not fabricated; an absent/short array yields `[]`."""
    if not isinstance(payload, dict):
        return []
    raw = payload.get("tvl")
    if not isinstance(raw, list):
        return []
    points: list[FundamentalsPoint] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        date = entry.get("date")
        value = _non_negative(entry.get("totalLiquidityUSD"))
        if not isinstance(date, (int, float)) or isinstance(date, bool) or value is None:
            continue
        points.append(FundamentalsPoint(date=int(date), value=value))
    return points[-_TVL_TREND_POINTS:]


def _parse_mcap(payload: Any) -> float | None:
    if not isinstance(payload, dict):
        return None
    mcap = _non_negative(payload.get("mcap"))
    return mcap if mcap is not None and mcap > 0 else None


def _parse_volume(payload: Any) -> VolumeSummary | None:
    if not isinstance(payload, dict):
        return None
    v24 = _non_negative(payload.get("total24h"))
    v7 = _non_negative(payload.get("total7d"))
    v30 = _non_negative(payload.get("total30d"))
    change = _finite_number(payload.get("change_1d"))
    if v24 is None and v7 is None and v30 is None:
        return None
    return VolumeSummary(
        volume_24h=v24,
        volume_7d=v7,
        volume_30d=v30,
        change_1d_pct=change,
    )


def _parse_apr(payload: Any, projects: set[str]) -> tuple[float | None, float | None]:
    """TVL-weighted `apyBase` (fee APR) and `apyReward` (reward APR) across the
    protocol's pools in the DefiLlama yields list. A pool contributes to a given
    APR only when both its `tvlUsd` and that APR component are finite; a component
    with no contributing pool returns `None` (honest-null)."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return None, None
    base_num = base_den = reward_num = reward_den = 0.0
    for pool in data:
        if not isinstance(pool, dict) or pool.get("project") not in projects:
            continue
        tvl = _non_negative(pool.get("tvlUsd"))
        if tvl is None or tvl <= 0:
            continue
        base = _finite_number(pool.get("apyBase"))
        if base is not None:
            base_num += base * tvl
            base_den += tvl
        reward = _finite_number(pool.get("apyReward"))
        if reward is not None:
            reward_num += reward * tvl
            reward_den += tvl
    fee_apr = base_num / base_den if base_den > 0 else None
    reward_apr = reward_num / reward_den if reward_den > 0 else None
    return fee_apr, reward_apr


def _parse_unlocks(payload: Any) -> list[UnlockEvent]:
    """Best-effort parse of a DefiLlama emissions payload's discrete unlock
    `events` (`[{timestamp, noOfTokens|tokens, category|description}, …]`). A
    shape we do not recognize yields `[]` (folded into the honest-null note),
    never a raise — the keyless endpoint's shape is not guaranteed and is often
    Pro-gated entirely (ADR-0102 risk #1)."""
    events_raw: Any = None
    if isinstance(payload, dict):
        meta = payload.get("metadata")
        if isinstance(meta, dict) and isinstance(meta.get("events"), list):
            events_raw = meta["events"]
        elif isinstance(payload.get("events"), list):
            events_raw = payload["events"]
    if not isinstance(events_raw, list):
        return []
    events: list[UnlockEvent] = []
    for entry in events_raw:
        if not isinstance(entry, dict):
            continue
        ts = entry.get("timestamp")
        if not isinstance(ts, (int, float)) or isinstance(ts, bool):
            continue
        tokens = _non_negative(_unlock_token_count(entry))
        if tokens is None:
            continue
        category = entry.get("category") or entry.get("description")
        events.append(
            UnlockEvent(
                date=int(ts),
                tokens=tokens,
                category=category if isinstance(category, str) else None,
            )
        )
    return events


def _unlock_token_count(entry: Mapping[str, Any]) -> Any:
    """DefiLlama has used both `noOfTokens` (sometimes a `[value]` list) and a
    flat `tokens`; return the first numeric it finds."""
    raw = entry.get("noOfTokens")
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) and raw:
        return raw[0]
    if raw is not None:
        return raw
    return entry.get("tokens")


__all__ = ["DefiLlamaFundamentalsAdapter"]
