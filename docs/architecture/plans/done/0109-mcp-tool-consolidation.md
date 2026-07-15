# 0109 — MCP tool consolidation (same-verb clusters → discriminated tools)

> **Status:** done — closed 2026-07-15. All six dev phases shipped (`9ac96c4` ph1 `scan_watchlist(rank_by)`, `9339093` ph2 `forecast(kind)`, `9f1eb0d` ph3 `sentiment(source)`, `37b1f6b` ph4 `price_structure(kind)`, `f4d44ee` ph5 `volume_read(kind)`, `5340d47` ph6 ledger+apiref+skill-doc sync); mid-implementation envelope amendment `c55d3d9`. MCP surface 62 → **50** (18 tools retired into 5 discriminated tools). **Mode 4 verdict: clean, no blockers.** Verified: `forecast(kind="direction")` `result` byte-identical + each kind publishes its own SSE event (`forecast.completed`/`volatility_forecast.completed`/`regime_forecast.completed`, payloads unchanged — the envelope is agent-facing only); ph4/ph5 shipped the flattened shared-wrapper shape `{kind, result, partial_reason, scanned_at}` (accepted as an improvement over the amendment's literal double-nest — reconciled in this plan); `EXPECTED_FULL_TOOLSET` = 50 and enforced against the live-wired server; `apiref --check` clean; no retired tool name in `.claude/skills`; `recommend`/advisor call the pure `analysis/`+`forecast/` cores, not retired tools; 788 api/apiref tests green. Realises [ADR-0104](../../adrs/0104-mcp-tool-surface-granularity.md) (accepted at close). **Phase 7 (human live MCP smoke) outstanding — deferrable, does not gate the close.**
> **Created:** 2026-07-15
> **Owner skill(s):** dev, human
> **Related ADRs:** [0104](../adrs/0104-mcp-tool-surface-granularity.md) (granularity rule + consolidation map); consumes [0014](../adrs/0014-mcp-as-second-sidecar-protocol.md), [0064](../adrs/0064-generated-sidecar-api-reference.md); preserves [0029](../adrs/0029-advisory-recommendation-boundary.md)/[0096](../adrs/0096-screening-quality-rank-conditions-side.md) boundaries

> **Amendment 2026-07-15 (mid-implementation, after phase 1 shipped).** Phase 1
> (`scan_watchlist`) is **shipped** (commit `9ac96c4`, surface 62 → 57): it folded its
> six tools into a single `{rank_by, matches, skipped, scanned_at}` object with the
> mode-union inside the `matches` **field**, so the top-level return stays one object and
> the shape is preserved. Phase 2 then surfaced a FastMCP constraint: a tool whose
> **return annotation** is a `Union`/generic is wrapped in a `{"result": …}` object
> (`func_metadata.wrap_output`), so the three clusters whose modes return **disjoint**
> top-level models — `forecast` (ph2), `price_structure` (ph4), `volume_read` (ph5) —
> cannot keep each mode's exact top-level shape. **Resolution (architect, ADR-0104
> amended):** those three tools return a purpose-built **discriminated envelope**
> `{kind, result: <mode payload>}` (a single object, so no generic wrap) — the mode's
> existing model rides byte-identical under `result`, nested one level. Full
> consolidation is retained (62 → 50); dropping the merges was rejected (it would leave
> `price_structure`'s four near-identical reads unconsolidated). `sentiment` (ph3) is
> unaffected — both tools already return `dict` (already `result`-wrapped), a
> zero-shape-change merge that may proceed as originally written. The ph2/4/5 done-whens
> below are reworded from "byte-equivalent to today's `<tool>`" to "the `result` payload
> is byte-identical, nested under the envelope."

> **Close reconciliation 2026-07-15 (architect, Mode 4).** As shipped, only **`forecast`
> (ph2)** is a genuinely disjoint cluster — its three retired tools returned *bare*
> models (`MultiHorizonForecastResult` / `VolatilityForecast` / `RegimeForecast`) with no
> shared wrapper — so it correctly took the `{kind, result}` envelope above.
> **`price_structure` (ph4) and `volume_read` (ph5) are NOT disjoint clusters**: their
> retired tools *already* shared a `{result, partial_reason, scanned_at}` wrapper, which
> makes them the same shared-wrapper case as phase 1's `scan_watchlist`. Dev therefore
> shipped them as a **flattened** `{kind, result, partial_reason, scanned_at}` object (the
> mode union inside the `result` *field*, `kind` added alongside), NOT the amendment's
> literal `{kind, result: <whole old Response>}` double-nest — which would have produced a
> pointless `result.result`. This is accepted as an improvement: `result` /
> `partial_reason` / `scanned_at` stay byte-identical to the retired tool (the done-when
> standard is met with *zero* extra nesting on those fields), and the single object still
> escapes FastMCP's union-return wrap. The Data-shapes section and the ph4/ph5 lines below
> reflect the shipped flattened shape; the envelope pattern applies to `forecast` alone.

## TL;DR

Collapse the five same-verb tool clusters identified in ADR-0104 into five discriminated tools — `scan_watchlist(rank_by=…)`, `forecast(kind=…)`, `sentiment(source=…)`, `price_structure(kind=…)`, `volume_read(kind=…)` — retiring 18 top-level MCP tools in favour of 5. The MCP surface drops **62 → 50**. No underlying analysis, forecast, or sentiment computation changes; only the tool entrypoints re-shape. The first visible result: `scan_watchlist` answers every watchlist ranking (squeeze/gainers/momentum/quality/volume-breakout/smart-volume) through one tool with a documented `rank_by` enum instead of six overlapping tools.

## Context & problem

The sidecar mounts 62 agent-callable MCP tools, growing toward ~70 as queued plans each add one. Per ADR-0104, the redundancy is concentrated in tools that are the *same verb differing only by a mode*: six watchlist scanners over the shared `_scan_symbols` harness, three forecast kinds, two sentiment sources, four single-symbol price reads, and two single-symbol volume reads. In Claude Code these tools are deferred/lazy-loaded, so the cost is not context bytes but **routing quality** — near-identical descriptions make the agent mis-select or miss capabilities. ADR-0104 adopts a "one tool per verb; modes are parameters" rule and mandates the consolidation this plan executes.

## Decision

Fold each same-verb cluster behind one tool that takes a discriminator parameter and returns a result discriminated by that mode. The underlying computations (`analysis/scanners.py`, `analysis/levels.py`, `analysis/volume.py`, `forecast/`, the sentiment adapters) are called unchanged — this is a surface refactor at the `mcp_tools/` layer. Internal consumers that today reach for a soon-retired tool (`recommend` → the quality-rank computation; the advisor → the forecast legs) call the underlying functions directly, never the retired MCP tool. We rejected doing nothing (ADR-0104 alt A — leaves routing and growth unfixed), per-skill tool mounts (alt B — heavier than the problem), and an aggressive fold-into-`analyze_symbol` re-taxonomy (alt C — merges distinct verbs).

## Architecture diagram

```mermaid
flowchart LR
    subgraph before["Before — 18 tools"]
        s1[squeeze_scan]:::g
        s2[gainers_losers]:::g
        s3[momentum_scan]:::g
        s4[quality_rank]:::g
        s5[volume_breakout]:::g
        s6[smart_volume]:::g
        f1[forecast]:::g
        f2[forecast_volatility]:::g
        f3[forecast_regime]:::g
        n1[sentiment_for_news]:::g
        n2[stocktwits_sentiment]:::g
        p1[fibonacci_levels]:::g
        p2[pivot_points]:::g
        p3[anchored_vwap]:::g
        p4[market_structure]:::g
        v1[volume_confirmation]:::g
        v2[counter_trend_volume]:::g
    end
    subgraph after["After — 5 tools"]
        A["scan_watchlist(rank_by)"]
        B["forecast(kind)"]
        C["sentiment(source)"]
        D["price_structure(kind)"]
        E["volume_read(kind)"]
    end
    s1 & s2 & s3 & s4 & s5 & s6 --> A
    f1 & f2 & f3 --> B
    n1 & n2 --> C
    p1 & p2 & p3 & p4 --> D
    v1 & v2 --> E
    subgraph deps["Unchanged compute (called directly)"]
        H[analysis/scanners._scan_symbols]
        L[analysis/levels + volume]
        FC[forecast/*]
        SN[sentiment adapters]
    end
    A --> H
    D --> L
    E --> L
    B --> FC
    C --> SN
    classDef g fill:#eee,stroke:#999,color:#333;
```

## Implementation phases

Each phase folds one cluster, ships as its own commit, and keeps the tree green (the retired tools' tests migrate to the new tool's mode). Phases 1–5 are independent and may land in any order; phase 6 (ledger + docs + skill-doc sync) runs after the merges it accounts for; phase 7 is the live smoke. A cluster whose internal-consumer rewiring proves costly can be dropped without blocking the others — the plan degrades to a partial consolidation, and phase 6 accounts for whatever landed.

### Phase 1 — `scan_watchlist(rank_by=…)` — SHIPPED (`9ac96c4`)
- **Owner skill:** dev
> **All six folded tools are already shipped and live** — `squeeze_scan`/`gainers_losers`/`momentum_scan` (Plan 0100, closed), `volume_breakout`/`smart_volume` (Plan 0021, refactored onto the harness by 0100), and **`quality_rank` (Plan 0101, fully shipped — `e6816e4` + the follow-up tool commit)**. This phase **retires** all six into modes; **no 0101 amendment is needed** (0109 owns the retirement). Each mode's compute already exists as a pure function and is called directly: the scanners via `analysis/scanners.py::_scan_symbols`, the `quality` mode via `analysis/quality.py` (0101's landed scorer + liquidity gate).
- **What:** One tool folding `squeeze_scan`, `gainers_losers`, `momentum_scan`, `quality_rank`, `volume_breakout`, `smart_volume`. `rank_by` ∈ {`squeeze`, `gainers`, `losers`, `momentum`, `quality`, `volume_breakout`, `smart_volume`} (gainers/losers may be one mode with a direction, or two — implementer's call, documented). Each mode's extra params live in a nested per-mode object; the result is discriminated by `rank_by`. All modes dispatch through the existing pure compute functions unchanged.
- **Files touched:** `api/mcp_tools/scan_watchlist.py` (new); delete the six retired modules (`squeeze_scan.py`, `gainers_losers.py`, `momentum_scan.py`, `quality_rank.py`, `volume_breakout.py`, `smart_volume.py`); `mcp_app.py` (one `register_scan_watchlist`, six removals); `recommend.py` — if it consumed the `quality_rank` **tool**, repoint it at `analysis/quality.py` directly (the advisor's ADR-0096 quality consumption must survive the tool's removal); `tests/api/test_scan_watchlist_tool.py` folds the six retired tools' assertions (one per mode); delete/merge their standalone test files (`test_quality_rank_tool.py` etc.).
- **Done when:** `scan_watchlist(rank_by="squeeze", symbols=[…])` returns the same ranked payload the old `squeeze_scan` did (asserted field-for-field against a fixture), and the same holds for each other `rank_by` value including `rank_by="quality"` (byte-equivalent to today's `quality_rank` on a fixture); `recommend` still produces a quality-informed recommendation with no reference to a `quality_rank` tool; the `quality` mode preserves 0101's conditions-only stance (ADR-0096 — no grade/action/conviction/levels).

### Phase 2 — `forecast(kind=…)`
- **Owner skill:** dev
- **What:** Fold `forecast_volatility` and `forecast_regime` into `forecast` as `kind` ∈ {`direction`, `volatility`, `regime`} (default `direction` preserves today's call **inputs**). Result is the discriminated envelope `ForecastResponse{kind, result}` (Data shapes) — each kind's `result` is its current model unchanged, and each kind still publishes its own `forecast.completed` / `volatility_forecast.completed` / `regime_forecast.completed` event. The advisor's forecast legs call the underlying `forecast/` functions directly, unchanged.
- **Files touched:** `api/mcp_tools/forecast.py` (absorb + envelope), delete `forecast_volatility.py` + `forecast_regime.py`; `mcp_app.py`; verify `recommend.py`/advisor wiring reads the compute functions not the tools; `tests/api/test_forecast_nondirectional_tools.py` migrates to `kind` modes.
- **Done when:** `forecast(kind="volatility", …)` and `forecast(kind="regime", …)` return `{kind, result}` whose `result` is their standalone tool's payload (asserted field-for-field against a fixture) and publish the same events (asserted); `forecast(kind="direction")`'s `result` is byte-identical to today's `forecast` output on a fixture (the envelope adds only the `kind` + `result` nesting); and the advisor's vol/regime-as-non-voting inputs (ADR-0071) still resolve.

### Phase 3 — `sentiment(source=…)`
- **Owner skill:** dev
- **What:** Fold `sentiment_for_news` and `stocktwits_sentiment` into `sentiment` with `source` ∈ {`news`, `stocktwits`}, structured so a new source is one added enum value + adapter binding (the extension point 0103/0108 will use). Dispatches to the existing sentiment adapters unchanged.
- **Files touched:** `api/mcp_tools/sentiment.py` (new); delete `sentiment_for_news.py` + `stocktwits_sentiment.py`; `mcp_app.py`; `tests/api/test_*sentiment*` migrate to `source` modes.
- **Done when:** `sentiment(source="news", symbol=…)` and `sentiment(source="stocktwits", symbol=…)` return their predecessors' payloads (asserted), and adding a source is demonstrably a one-enum-value change (a stub third source registers without a new module or `register_*` call).

### Phase 4 — `price_structure(kind=…)`
- **Owner skill:** dev
- **What:** Fold `fibonacci_levels`, `pivot_points`, `anchored_vwap`, `market_structure` into `price_structure` with `kind` ∈ {`fibonacci`, `pivots`, `anchored_vwap`, `market_structure`}. The four retired reads already shared a `{result, partial_reason, scanned_at}` wrapper, so this is the phase-1 shared-wrapper case, not the disjoint envelope: result is the flattened `PriceStructureResponse{kind, result, partial_reason, scanned_at}` (Data shapes) — `result` is the mode's existing geometry model (`FibonacciLevels`/`PivotPoints`/`AnchoredVwapValue`/`MarketStructure`) as a field union, `kind` added alongside; `result`/`partial_reason`/`scanned_at` stay byte-identical. Pure reads over cached bars (no chart events — these never drew, unlike `detect_levels`). `market_structure` keeps its ADR-0084 second-trend-read semantics as the `market_structure` mode.
- **Files touched:** `api/mcp_tools/price_structure.py` (new); delete the four retired modules; `mcp_app.py`; `tests/api/test_price_structure_tool.py` (folds the four).
- **Done when:** each `kind` returns `{kind, result, partial_reason, scanned_at}` whose `result`/`partial_reason`/`scanned_at` are its predecessor's payload field-for-field against a fixture (the fold adds only the `kind` tag), and the anti-lookahead property each read carried (trailing anchor, last-completed-bar pivots) still holds under a truncation test.

### Phase 5 — `volume_read(kind=…)`
- **Owner skill:** dev
- **What:** Fold the two single-symbol volume reads `volume_confirmation` and `counter_trend_volume` into `volume_read` with `kind` ∈ {`confirmation`, `counter_trend`}. Both retired reads already shared a `{result, partial_reason, scanned_at}` wrapper, so — like phase 1/4 — result is the flattened `VolumeReadResponse{kind, result, partial_reason, scanned_at}` (Data shapes): `result` is the mode's existing model (`VolumeConfirmation`/`CounterTrendVolume`) as a field union, `kind` added alongside; `result`/`partial_reason`/`scanned_at` stay byte-identical. `counter_trend`'s anchoring to the canonical snapshot trend (ADR-0083) is preserved. (Lowest-value merge; drop if review prefers to leave two thin tools.)
- **Files touched:** `api/mcp_tools/volume_read.py` (new); delete `volume_confirmation.py` + `counter_trend_volume.py`; `mcp_app.py`; `tests/api/test_volume_read_tool.py`.
- **Done when:** `volume_read(kind="confirmation")` and `volume_read(kind="counter_trend")` return `{kind, result, partial_reason, scanned_at}` whose `result`/`partial_reason`/`scanned_at` reproduce their predecessors' payloads field-for-field (the fold adds only the `kind` tag), including the counter-trend split anchored to the live snapshot trend.

### Phase 6 — Toolset ledger + apiref + skill-doc sync
- **Owner skill:** dev
- **What:** Update `EXPECTED_FULL_TOOLSET` to the post-consolidation set (remove the 18 retired names, add the 5 new ones → 50), regenerate `docs/reference/` (`uv run python -m market_analyser.apiref`), and update every **skill reference doc** that names a retired tool so the skill descriptions/examples route to the new tool + mode (grep the `.claude/skills/**/references/` tree for the 18 retired names — `market-analyst` and `advisor` reference docs are the likely hits).
- **Files touched:** `tests/api/test_mcp_tools.py` (`EXPECTED_FULL_TOOLSET`), `docs/reference/*` (regenerated, not hand-edited), `.claude/skills/*/references/*.md` (retired-name replacements).
- **Done when:** `test_full_toolset_registration_is_exhaustive` passes against the 50-name set, `apiref --check` exits 0, and `grep -r` over `.claude/skills/**/references/` finds no retired tool name.

### Phase 7 — Live MCP smoke
- **Owner skill:** human
- **What:** Against the running sidecar, exercise each consolidated tool once per mode via the agent and confirm the answers match pre-consolidation behavior; confirm `recommend` still returns a quality-informed call. Deferrable like the other read-only smokes.
- **Done when:** every `rank_by`/`kind`/`source` value returns a sane payload live, and nothing in the agent's routing surfaces a retired tool name.

## Data shapes

```python
# illustrative — discriminated entrypoint; per-mode params nested to keep the
# discriminator itself the routing signal (params pattern, all clusters).
class ScanWatchlistParams(BaseModel):
    symbols: list[str]
    timeframe: str
    rank_by: Literal["squeeze", "gainers", "losers", "momentum",
                     "quality", "volume_breakout", "smart_volume"]
    # only the selected mode's block is read; others ignored
    momentum: MomentumScanOpts | None = None
    quality: QualityRankOpts | None = None
    # …one optional opts block per mode
```

**Result shape — two patterns (see the mid-implementation amendment above).** FastMCP
wraps a `Union`/generic *return annotation* in `{"result": …}`; only a single `BaseModel`
return stays unwrapped. So the tool return is always **one object**, and the discriminator
is a field on it:

- **Shared-wrapper clusters** (`scan_watchlist` ph1, `price_structure` ph4, `volume_read`
  ph5) — the modes' results already share a wrapper, so the union lives inside a *field*
  and the top-level object is preserved; the fold adds only the `kind`/`rank_by`
  discriminator alongside the existing wrapper fields (no extra nesting). `scan_watchlist`
  (shipped phase 1) is the canonical case, and ph4/ph5's four+two retired reads already
  shared `{result, partial_reason, scanned_at}`, so they fold the same way:

  ```python
  # scan_watchlist result — one object, mode-union inside `matches`:
  {"rank_by": "...", "matches": [ ...mode's match model... ],
   "skipped": [...], "scanned_at": "..."}
  # price_structure: {kind, result: Fibonacci|Pivots|AnchoredVwap|MarketStructure|None,
  #                   partial_reason, scanned_at}   — result/partial_reason/scanned_at
  #                   byte-identical to the retired read
  # volume_read:     {kind, result: VolumeConfirmation|CounterTrend|None,
  #                   partial_reason, scanned_at}
  ```

- **Disjoint-shape cluster** (`forecast` ph2 only) — the three retired forecast tools
  returned *bare* models with no shared wrapper, so wrap each in a purpose-built envelope
  (a single object → not generically wrapped) with the mode's existing model byte-identical
  under `result`:

  ```python
  class ForecastResponse(BaseModel):        # ph2 (kind ∈ direction/volatility/regime)
      kind: ForecastKind
      result: MultiHorizonForecastResult | VolatilityForecast | RegimeForecast
  # → {"kind": "direction", "result": { ...MultiHorizonForecastResult... }}
  ```

  In both patterns the `result` union is a plain field union (pydantic serializes each
  member by its runtime type — phase 1's discriminated-serialization test is the
  precedent), and `kind` echoes the input discriminator. Per-mode params still ride the
  request as nested opts blocks (params pattern above).

- **`sentiment`** (ph3) returns `dict[str, Any]` today (already `{"result": …}`-wrapped by
  FastMCP), so it consolidates with **no** shape change — keep the `dict` return, include a
  `source` key in it.

## Risks & open questions

- **Internal-consumer coupling (phases 1–2).** If `recommend`/the advisor reach a retired tool rather than its compute function, that rewiring is the real work. Mitigation: audit `recommend.py` and the advisor fusion in phase 1/2 before deleting any module; if a consumer can't be cleanly redirected to the underlying function, defer that cluster's merge and land the rest.
- **Discriminated-union schema legibility.** A fat union can bury a mode. Mitigation: write per-enum-value descriptions that carry what the retired tool's one-liner did, so `ToolSearch` still surfaces the mode.
- **FastMCP wraps union *return* types (RESOLVED 2026-07-15 — see the mid-implementation amendment).** A tool whose return annotation is a `Union`/generic is wrapped in `{"result": …}`; only a single `BaseModel` return stays unwrapped. So the disjoint-shape cluster `forecast` (ph2) can't keep each mode's exact top-level shape and instead returns the `{kind, result}` envelope (a single object). Not a blocker — full consolidation retained; the `result` payload stays byte-identical and the SSE events are untouched. Immune (all a single object already): `scan_watchlist`/`price_structure`/`volume_read` (shared `{…, scanned_at}` wrapper, mode-union in a field + `kind` added — see the close reconciliation) and `sentiment` (already `dict`).
- **Event-shape preservation (phase 2).** `forecast_volatility`/`forecast_regime` publish distinct SSE events the viewer renders. The merge must keep those events byte-identical — asserted, not assumed.
- **Sequencing vs 0103/0108.** Those plans extend `sentiment`; this plan must land phase 3 before they run, or they build the unified tool themselves. The README execution order notes this.
- **Gainers vs losers.** One directional mode or two enum values — implementer's call in phase 1; document whichever, so the agent isn't guessing.

## What this plan does NOT do

- **Does not merge distinct verbs.** Chart control, data access, compute-and-draw tools, backtest/prediction/DeFi/advisory/watch/execution verbs are untouched (ADR-0104's "what stays separate").
- **Does not change any computation.** No indicator, forecast, scanner, or sentiment math changes — surface refactor only. Determinism, anti-lookahead, and ADR-0029/0096 boundaries preserved.
- **Does not add per-skill tool mounts** (ADR-0104 alt B, rejected).
- **Does not reroute 0102/0107/0046** — those keep their new tools per ADR-0104's disposition (distinct verbs / deliberate execution split). It amends only 0103/0108 (sentiment reroute) and 0042 (collapse plural risk tools) — done by architect at ADR authoring, not a dev phase here.

## Followups (after this lands)

- Re-run the `EXPECTED_FULL_TOOLSET` growth review at each future plan's Mode 4 as ADR-0104 mandates.
- If `volume_read` (phase 5) was dropped, note the surface count is 51 not 50.
