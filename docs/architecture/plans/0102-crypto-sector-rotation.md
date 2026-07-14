# 0102 — Crypto sector rotation

> **Status:** approved
> **Created:** 2026-07-13
> **Owner skill(s):** dev, human
> **Related ADRs:** [0097](../adrs/0097-crypto-sector-taxonomy-and-baskets.md) (paired, accepts at close), [0095](../adrs/0095-watchlist-scan-fanout-harness.md), [0069](../adrs/0069-crypto-first-asset-class-positioning.md), [0023](../adrs/0023-technical-analysis-surface.md)
> **Depends on:** [Plan 0100](0100-watchlist-condition-scanners.md) (the `_scan_symbols` harness)

## TL;DR

A crypto **sector rotation** read — rank a self-defined set of crypto sectors (L1 / L2 / DeFi / memes / AI / …) by equal-weighted constituent momentum over cached bars, surfacing which sectors are hot vs cold and the leaders/laggards within each. The taxonomy lives in versioned config ([ADR-0097](../adrs/0097-crypto-sector-taxonomy-and-baskets.md)); momentum reuses the Plan 0100 harness. Conditions only ([ADR-0029](../adrs/0029-advisory-recommendation-boundary.md)). First user-visible behaviour: `sector_rotation("1d", lookback=30)` returns the sectors ranked by mean constituent 30-bar return, hottest first, each with its leader and laggard constituents.

## Context & problem

"Where is capital rotating" is a read the app can't currently answer for crypto. Unlike equities — where SPDR sector ETFs give a canonical, single-price-per-sector index — crypto has no canonical taxonomy and no fetchable sector index. We have the constituent OHLCV (USD-native, via Coinbase/Binance/Yahoo) but no notion of "sectors" or a tool that ranks them.

[ADR-0097](../adrs/0097-crypto-sector-taxonomy-and-baskets.md) settles the hard part: define the taxonomy ourselves as versioned config, synthesize sector momentum as the equal-weighted mean of constituent trailing returns, and treat the taxonomy as a maintained artifact.

## Decision

Ship a config-defined crypto sector taxonomy plus a momentum engine and a `sector_rotation` tool. Per-constituent trailing returns come through the Plan 0100 harness (our cached bars, anti-lookahead honoured); sector momentum is their equal-weighted mean; sectors rank by that momentum; each reports its leaders/laggards and any skipped constituents. We rejected an external taxonomy (ADR-0097 alt A), cap-weighting (alt B), and the US-ETF variant (alt C).

## Architecture diagram

```mermaid
flowchart LR
    subgraph sidecar [Python sidecar]
        T[sector_rotation tool]
        E["analysis/sectors.py<br/>equal-weight sector momentum + rank"]
        H["_scan_symbols harness (Plan 0100)"]
        CFG[[versioned taxonomy config<br/>sector → constituents]]
        P[(cached bars · USD-native sources)]
        T --> E
        E --> CFG
        E --> H --> P
    end
    Agent[market-analyst] --> T
```

## Implementation phases

### Phase 1 — Taxonomy config + loader
- **Owner skill:** dev
- **What:** Define the crypto sector taxonomy (sector → constituent symbols) as versioned config data, with a typed loader + validation.
- **Files touched:** a taxonomy data/config module under `src/market_analyser/` (e.g. `analysis/sector_taxonomy.py` or a config asset + loader), tests.
- **Done when:** the loader parses the shipped taxonomy into typed sectors, rejects a malformed/empty basket, and a unit test pins the ≥N-priced-constituents-to-report rule. The shipped taxonomy is the pinned initial set (final sector list decided here), documented as a maintained artifact.

### Phase 2 — Sector momentum engine
- **Owner skill:** dev
- **What:** `analysis/sectors.py` — per-constituent trailing return via the harness → equal-weighted sector momentum, skip+flag missing constituents, rank sectors, identify per-sector leaders/laggards. Pure, deterministic, trailing.
- **Files touched:** `src/market_analyser/analysis/sectors.py` (new), `analysis/types.py` (result model), tests.
- **Done when:** a unit test over a fixture taxonomy pins (a) the equal-weight mean math, (b) missing-constituent skip + the incomplete-sector rule, (c) the sector ranking order, (d) leader/laggard identification, and (e) no-lookahead via truncation-invariance.

### Phase 3 — `sector_rotation` MCP tool
- **Owner skill:** dev
- **What:** Expose `sector_rotation` — rank the taxonomy's sectors by momentum over a caller `timeframe` + `lookback`.
- **Files touched:** `api/mcp_tools/sector_rotation.py` (new), register in `mcp_app.py`, `EXPECTED_FULL_TOOLSET` +1, regenerate `docs/reference/`.
- **Done when:** the tool ranks fixture sectors by momentum descending, reports leaders + skipped constituents + `scanned_at`, honours `as_of`, and the response is asserted to carry **no** `action` / `signal` / `recommendation` / `buy` / `sell` key. The description states: rotation is a condition read, not a call.

### Phase 4 — Live smoke
- **Owner skill:** human
- **What:** Run `sector_rotation` over the shipped taxonomy on real bars via MCP.
- **Done when:** the ranking matches an eyeball of recent sector performance, incomplete sectors are honestly flagged (not silently ranked), and nothing reads as a buy call.

## Data shapes

```python
# illustrative — not the final interface
class SectorMomentum(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    sector: str
    momentum: float                       # equal-weighted mean of constituent returns
    n_priced: int
    complete: bool                        # n_priced >= configured floor
    leaders: list[dict]                   # [{"symbol":.., "return":..}, ...]
    laggards: list[dict]
    skipped: list[str]                    # constituents with no cached bars

# response: {sectors_sorted: list[SectorMomentum], scanned_at: datetime}
```

## Risks & open questions

- Risk: the taxonomy ages. Mitigation: documented operational handle — revisit the sector/constituent lists periodically; each revision is a config edit.
- Risk: an illiquid/delisted constituent distorts a sector. Mitigation: skip-and-flag + the ≥N-priced floor before a sector is ranked.
- Risk: overlapping membership (a token in two sectors). Mitigation: allowed and documented; the momentum is per-basket, so overlap is intentional.
- Risk: cross-source price alignment. Mitigation: constituents are USD-native (ADR-0076) — the same alignment the existing scanners already rely on.

## What this plan does NOT do

- **No cap-weighted baskets** (ADR-0097 alt B) — equal-weight only; cap-weight is a future refinement.
- **No external sector taxonomy** (ADR-0097 alt A) — the taxonomy is ours, in config.
- **No US sector ETFs** (ADR-0097 alt C) — a possible sibling plan if wanted later.
- **No UI rotation heatmap** — a `ui-builder` followup.
- **No rotation-based alerts or advice** — an advisor/watch followup.

## Followups (after this lands)

- A renderer rotation heatmap (ui-builder).
- Feed sector-momentum context into the advisor / a rotation `create_watch` alert.
- A cap-weighted variant if the equal-weight read proves useful.
