# 0091 — Momentum, divergence & money-flow analysis layer

> **Status:** done — CLOSED 2026-07-13 (Mode-4 clean: **no blockers/majors**; two minor items below). All ten code phases landed on `main`, no branch: `dev` ph1–5 (`7347392`→`f4e004b`: oscillators, money-flow, snapshot values, `analysis/divergence.py` core, `recent_divergences` + `detect_divergences` tool) → `ui-builder` ph6–7 (`6c70593`/`4ced129`: oscillator + money-flow real v5 panes on Plan 0095's `lib/panes.ts`) → `dev` ph8 (`c60c197`: `chart.divergences v1` cross-pane delivery channel, ADR-0090) → `ui-builder` ph9 (`af156bb`→`89ba1a9`→`8e8c9ea`: RSI+MACD-histogram panes, reducer + Zod `.strict()` plumbing, two-pane divergence render). **ADR-0090 accepted at this close.** Gates re-verified green at close: `ruff` + `mypy --strict` (255 files) + 72 divergence/event Python tests + `apiref --check` no-drift; renderer typecheck (5 tsconfigs) + lint + **903 jest / 90 suites** + `gen-types:check` no-drift. **Sanctioned deviation (ratified):** ph9 was expanded under Option A (user-authorized, commit-flagged) to build the **RSI + MACD-histogram oscillator panes** — these were pre-existing log-and-skip `OverlayKind`s with no pane (phases 6–7 shipped Stochastic/StochRSI/CCI/Williams/ROC/MFI/CMF/AD but not RSI/MACD), so the divergence render had no oscillator pane to draw the second segment on for the two most common oscillators; ph9 promoted them to first-class panes with Python-parity (`1e-6`) + truncation-invariance mirror tests. This exceeds the plan's original ph9 Files-touched (which assumed the panes existed). **Minor 1 (test gap, not a defect):** no dedicated Python assertion pins `TYPE_REGISTRY["chart.divergences"] is ChartDivergencesPayloadV1` / Python-side `VERSION==1` — the invariant holds in code and is covered indirectly (publish test's `env.type` resolves through the registry; VERSION==1 pinned on the TS parity side). **Minor 2:** the ratified ph9 pane expansion above. **Phase 10 (`human` live smoke) pending.** No wire/schema regressions; renderer→Node isolation and data→api layering intact; largest new file `lib/divergences.ts` at 352 lines. Implemented directly in this working tree — no branch/worktree to merge or prune.
> **Created:** 2026-07-12
> **Owner skill(s):** dev, ui-builder, human
> **Related ADRs:** [0023-technical-analysis-surface](../adrs/0023-technical-analysis-surface.md) (realizes; the indicator/snapshot/glossary additions need no new ADR), [0090-cross-pane-divergence-delivery](../adrs/0090-cross-pane-divergence-delivery.md) (**the one new ADR — the `chart.divergences` cross-pane delivery channel; accepts at this plan's close**), [0060-glossary-tooltip-interaction-posture](../adrs/0060-glossary-tooltip-interaction-posture.md) (glossary), [0077-user-originated-display-overlays](../adrs/0077-user-originated-display-overlays.md) (client-computable overlays), [0049-chart-trendline-overlay-primitive](../adrs/0049-chart-trendline-overlay-primitive.md) / [0061-trendline-pattern-identity-and-colour](../adrs/0061-trendline-pattern-identity-and-colour.md) (divergence segment rendering), [0059-trendline-event-channel-and-recompute](../adrs/0059-trendline-event-channel-and-recompute.md) (the dedicated-channel precedent ADR-0090 extends), [0046-mcp-large-result-delivery](../adrs/0046-mcp-large-result-delivery.md) + [0064-generated-sidecar-api-reference](../adrs/0064-generated-sidecar-api-reference.md) (tool surface), [0088-lightweight-charts-v5-panes](../adrs/0088-lightweight-charts-v5-panes.md) (the real panes substrate the UI phases render on)
> **Depends on:** [Plan 0095 (lightweight-charts v5 migration)](0095-lightweight-charts-v5-migration.md) — **CLOSED 2026-07-13**; UI render phases 6, 7, 9 build on v5's `addPane()` API and consume its `lib/panes.ts` helper. Phases 6–7 have landed; phase 9 (divergence render) remains, gated now on the backend phase 8 (`chart.divergences`), not on 0095.

## Amendment — 2026-07-13 (phases 6–8 re-scoped onto the v5 panes API)

`dev` landed phases 1–5 (`7347392…f4e004b`): the oscillator + money-flow backend, the snapshot `recent_divergences`, and the `detect_divergences` MCP tool are in. `ui-builder` then hit the plan-vs-reality wall this plan's own Risk section (line 145) predicted: the pinned `lightweight-charts@4.2.3` has **no panes API** — sub-panes are faked with `scaleMargins` bands on one pane (`lib/chartSeries.ts:26-34`), which works for the current three (candles/volume/OBV) but is unusable for the **eight-plus** oscillator + money-flow panes phases 6–7 require (each band collapses to a sliver, sharing one price axis and crosshair).

Resolution ([ADR-0088](../adrs/0088-lightweight-charts-v5-panes.md)): upgrade to lightweight-charts **v5** (real `chart.addPane()`) as a **standalone behavior-preserving precursor, [Plan 0095](0095-lightweight-charts-v5-migration.md)** — kept separate so 0095's review proves "the existing chart is unchanged on v5" and this plan's review judges new-indicator correctness on a stable engine, not an engine swap. Phases 6–8 below are re-scoped: they now build on v5's panes API and the `lib/panes.ts` helper 0095 delivers, instead of building a pane framework from scratch. Phases 1–5 are unchanged (landed). No backend, wire, or done-when change to 1–5.

## Amendment — 2026-07-13 (a backend divergence-delivery phase inserted before the render; new ADR-0090)

Plan 0095 (v5 panes) closed and `ui-builder` landed phases 6–7 (oscillator + money-flow real panes). Phase 8 (divergence visualization) then surfaced a **plan gap**: the renderer draws chart geometry only from SSE `chart.*` events (`chart.show`/`update`/`highlight`/`trendlines`) or the bars it holds — it never fetches the condition snapshot. The `dev` phases pushed a `Divergence` to the *agent* (the `detect_divergences` tool, snapshot `recent_divergences`) but **nothing pushes one to the chart**, and a divergence is two segments across **two** panes (price pivots on pane 0, oscillator pivots on that oscillator's own v5 pane) — geometry no existing event carries (`chart.trendlines` is single-pane `TrendlineSpec[]`).

Resolution: add a proper backend delivery event rather than re-derive on the client (rejected — duplicates the Python pairing heuristic, reintroduces client/Python drift). A new **[ADR-0090](../adrs/0090-cross-pane-divergence-delivery.md)** records the `chart.divergences v1` cross-pane channel (the first chart channel carrying pane-routed geometry — a capability ADR-0088's real panes newly made possible, and a deliberate departure from ADR-0059's recompute-on-load pillar, so it earns its own decision record). Structurally: a **new `dev` phase 8** publishes `chart.divergences` from `detect_divergences`, mirroring how `detect_chart_patterns` emits `chart.trendlines`; the old render phase becomes **phase 9** (re-pointed onto the channel) and the human smoke becomes **phase 10**. Only the unshipped phases move — phases 1–7 (landed, committed) are untouched.

## TL;DR

From the 2026-07-12 capability audit: the analysis surface is strong on trend, candlesticks, and chart patterns but thin on **momentum oscillators**, has **no divergence detection at all** (we compute every input — RSI, MACD, OBV — but never detect price↔oscillator divergence), and lacks the **volume-weighted money-flow family** (MFI, A/D line, Chaikin Money Flow). This plan adds those three in-house, trailing, and lookahead-safe (ADR-0023), surfaces them on the condition snapshot and as MCP tools, and — the explicit requirement — **renders each in the UI**: new oscillator sub-panes with layer toggles, money-flow panes, and divergence drawn as connecting segments on the price and oscillator panes with glossary tooltips. First user-visible behavior: `analyze_symbol` carries stochastic/CCI/Williams %R/ROC/MFI/CMF values plus any active price↔RSI divergence, a new `detect_divergences` tool answers focused divergence queries, and the chart can toggle a Stochastic pane and show a highlighted bearish RSI divergence.

## Context & problem

The audit (grounded by reading `analysis/indicators.py` + `analysis/volume.py`) confirmed three genuine, verified-absent gaps — no `stochastic`/`cci`/`williams`/`mfi`/`chaikin`/`accumulation` anywhere in `src/`, and no divergence detector (every "divergence" string in the tree is either a doc comment or the Plan-0090 counter-trend note):

1. **Momentum oscillators.** We have RSI + MACD only. Range-bound momentum turns and overbought/oversold *in the absence of trend* are under-covered without Stochastic, Stochastic RSI, CCI, Williams %R, and ROC.
2. **Divergence detection.** The single highest-leverage missing piece for trend/reversal warning. We already own the inputs (price swings via `swing_pivots`, RSI/MACD/OBV series); we simply never pair price pivots against oscillator pivots to detect regular/hidden divergence.
3. **Money-flow family.** OBV is binary (up-volume vs down-volume). MFI (volume-weighted RSI), the Accumulation/Distribution line, and Chaikin Money Flow grade *conviction* and catch accumulation/distribution the current volume tools miss.

The user's directive: implement the complete picture, and **pair every backend addition with UI** (panes, overlays, divergence visualization) — not a headless analysis-only drop.

## Decision

We add, all inside the pure/trailing analysis surface (ADR-0023) and each paired with a renderer surface:

1. **Oscillators** in `analysis/indicators.py`: `stochastic()` (%K/%D), `stochastic_rsi()`, `cci()`, `williams_r()`, `roc()`.
2. **Money-flow** in `analysis/volume.py` (price+volume): `mfi()`, `accumulation_distribution()`, `chaikin_money_flow()`.
3. **Divergence detection** in a new `analysis/divergence.py`: `detect_divergences(bars, oscillator, ...)` pairing recent price `swing_pivots` against the oscillator's own pivots to classify regular/hidden bullish/bearish divergence, returning a `Divergence` model.
4. **Snapshot + tools**: the new oscillator/money-flow latest values ride the snapshot `indicators` dict; the snapshot gains a `recent_divergences` list; a dedicated `detect_divergences` MCP tool serves focused queries; `docs/reference/` is regenerated.
5. **UI**: oscillator sub-panes with LayersPanel toggles + client-compute parity (the Plan-0082 `computeBbands` precedent), money-flow panes, and **divergence rendering** as color-coded connecting segments on the price + oscillator panes, all with glossary entries (ADR-0060) and en/ru parity (ADR-0063).

The analysis additions need **no new ADR**: they are new indicator consumers on the ADR-0023 surface (its "new indicators get a home here, thresholds are ours to own" clause), overlays follow ADR-0077/0049, glossary follows ADR-0060. The divergence pivot-pairing heuristic and its constants are owned like the candlestick thresholds ADR-0023 already covers; the method is pinned in the plan's Data-shapes + Done-when, not a separate decision record.

**One new ADR — [ADR-0090](../adrs/0090-cross-pane-divergence-delivery.md)** — was added mid-flight (see the 2026-07-13 amendment) for the divergence *delivery* channel: pushing a two-pane divergence to the chart is a genuinely new wire capability (`chart.trendlines` is single-pane) that only ADR-0088's real panes made possible, and it deliberately departs from ADR-0059's recompute-on-load pillar — so it earns its own record, distinct from the analysis surface above.

## Architecture diagram

```mermaid
flowchart LR
    subgraph analysis["src/market_analyser/analysis/ (pure, trailing — ADR-0023)"]
        IND["indicators.py<br/>+ stochastic / stochastic_rsi<br/>+ cci / williams_r / roc"]
        VOL["volume.py<br/>+ mfi / accumulation_distribution<br/>+ chaikin_money_flow"]
        DIV["divergence.py (new)<br/>detect_divergences()"]
        SNAP["snapshot.py<br/>+ oscillator/money-flow values<br/>+ recent_divergences"]
        IND --> SNAP
        VOL --> SNAP
        IND --> DIV
        VOL --> DIV
        DIV --> SNAP
    end
    subgraph api["api/ (dev)"]
        AZ["analyze_symbol (existing)"]
        DTOOL["detect_divergences (tool)<br/>+ publishes chart.divergences (ph8)"]
        CHAN["chart.divergences v1<br/>(ADR-0090, cross-pane)"]
    end
    subgraph ui["desktop/ renderer (ui-builder)"]
        PANES["oscillator + money-flow sub-panes<br/>+ LayersPanel toggles"]
        DVIS["divergence segments<br/>(price pane + oscillator pane, ph9)"]
    end
    SNAP --> AZ --> PANES
    DIV --> DTOOL --> CHAN --> DVIS
```

## Implementation phases

### Phase 1 — Oscillator indicators
- **Owner skill:** dev
- **What:** `stochastic(bars, k_period=14, d_period=3)` (%K/%D value object), `stochastic_rsi(closes, ...)`, `cci(bars, period=20)`, `williams_r(bars, period=14)`, `roc(closes, period=12)` in `analysis/indicators.py`, mirroring the existing length-aligned/`None`-prefixed/trailing conventions.
- **Files touched:** `src/market_analyser/analysis/indicators.py`, `tests/analysis/test_indicators.py`.
- **Done when:** each new indicator matches a hand-worked fixture within `1e-9`; each has a **truncation-invariance** test (compute on `bars[0..=k]` == full-series value at every `i <= k`, the ADR-0023 no-lookahead guarantee); `None`-prefix length alignment matches input length. `mypy --strict` + `ruff` clean.

### Phase 2 — Money-flow indicators
- **Owner skill:** dev
- **What:** `mfi(bars, period=14)` (money-flow index), `accumulation_distribution(bars)` (cumulative A/D line), `chaikin_money_flow(bars, period=20)` in `analysis/volume.py`.
- **Files touched:** `src/market_analyser/analysis/volume.py`, `tests/analysis/test_volume.py`.
- **Done when:** each matches a hand-worked fixture within `1e-9`; truncation-invariance test each; the degenerate zero-range / zero-volume bars are handled (`None`, never divide-by-zero, matching the existing `vwap`/`relative_volume` guards). `mypy --strict` + `ruff` clean.

### Phase 3 — Snapshot integration
- **Owner skill:** dev
- **What:** surface the latest oscillator + money-flow values in the snapshot `indicators` dict (`stoch_k`, `stoch_d`, `stoch_rsi`, `cci`, `williams_r`, `roc`, `mfi`, `ad_line`, `cmf`). The `momentum` classifier is left unchanged (RSI-zone + MACD, ADR-0023) — the new values are reported facts, not a re-vote.
- **Files touched:** `src/market_analyser/analysis/snapshot.py`, `analysis/types.py` (docstring), `tests/analysis/test_snapshot.py`.
- **Done when:** the pinned `indicators` key-set test is updated to include exactly the nine new keys (frozen-field guard fails on a missing/extra key); each new value equals the standalone indicator's latest defined value on a fixture; the snapshot stays conditions-only (no action field — existing guard passes).

### Phase 4 — Divergence detection core
- **Owner skill:** dev
- **What:** `analysis/divergence.py` with `detect_divergences(bars, oscillator, lookback=60, pivot_window=SR_PIVOT_WINDOW)` and a `Divergence` model. Pairs the two most recent confirmed price `swing_pivots` of a kind against the oscillator's own pivots over the same bars and classifies: **regular bearish** (price higher high, oscillator lower high), **regular bullish** (price lower low, oscillator higher low), **hidden bearish** (price lower high, oscillator higher high), **hidden bullish** (price higher low, oscillator lower low). `oscillator` selects the series (`rsi`, `macd_hist`, `obv`, `mfi`). Reuses `swing_pivots` (confirmed-only → trailing).
- **Files touched:** `src/market_analyser/analysis/divergence.py`, `analysis/types.py`, `tests/analysis/test_divergence.py`.
- **Done when:** a constructed higher-high-price / lower-high-RSI fixture yields exactly one `regular_bearish` divergence with the right pivot anchors; the bullish mirror; a hidden-divergence fixture; a no-divergence fixture yields `[]`; **truncation-invariance** (a divergence reported at bar `i` is byte-identical on `bars[0..=i]` — no future pivot leaks in); `extra="forbid"` rejects an added field at construction.

### Phase 5 — Divergence + oscillator MCP surface
- **Owner skill:** dev
- **What:** `recent_divergences: list[Divergence]` added to the snapshot (scoped to the recent-activity window, like `recent_patterns`), and a dedicated `detect_divergences(symbol, timeframe, oscillator="rsi", lookback=60, as_of=None)` MCP tool returning `{result, partial_reason, scanned_at}` (honest `no_bars` on empty cache — never a silent fetch). Register in the toolset, bump `EXPECTED_FULL_TOOLSET`, regenerate `docs/reference/` (ADR-0064).
- **Files touched:** `analysis/snapshot.py`, the MCP tool module under `api/mcp_tools/`, tool registry + `EXPECTED_FULL_TOOLSET`, `tests/**`, `docs/reference/**`.
- **Done when:** the tool drives end-to-end on a populated symbol (returns the divergence with anchors matching the standalone detector), returns `partial_reason="no_bars"`/`result=None` on empty cache, replays trailing under `as_of`; snapshot `recent_divergences` matches the detector over the recent window; the tool is in `EXPECTED_FULL_TOOLSET` and `apiref --check` exits 0.

### Phase 6 — Oscillator sub-panes + toggles (UI) — *v5 panes*
- **Owner skill:** ui-builder
- **Blocked on:** [Plan 0095](0095-lightweight-charts-v5-migration.md) (v5 + `lib/panes.ts` helper).
- **What:** render Stochastic (%K/%D), Stochastic RSI, CCI, Williams %R, ROC as toggleable **real panes** below the price pane, each created via the `lib/panes.ts` helper Plan 0095 delivers (`chart.addPane()` / `addSeries(<Type>Series, opts, paneIndex)`) — a real independently-scaled pane per oscillator (0–100 for Stochastic/Stoch-RSI, unbounded for CCI/ROC, −100..0 for Williams %R), **not** a `scaleMargins` band. The reusable oscillator-pane wrapper (create-or-reuse by stable id, set height, tear down on toggle-off, push the mirrored series) is the first deliverable, built on top of 0095's `panes.ts`. Add client-computable `OverlayKind`s so users can add them (ADR-0077 user-overlay path) and the agent can request them; client-compute functions mirror the Python indicators with a Python-generated fixture parity test within `1e-6` **and** a truncation-invariance test (the `computeBbands` precedent). LayersPanel toggle rows + glossary entries (ADR-0060) + en/ru keys (ADR-0063).
- **Files touched:** `desktop/renderer/lib/panes.ts` (consume; extend only if needed) + a new oscillator-pane wrapper/hook + `CandlestickChart` + LayersPanel + `lib/` compute mirrors (`lib/oscillators.ts`) + glossary + locales + `events.test.ts` parity guard + jest suites (incl. `lib/panes` usage).
- **Done when:** each oscillator draws in its **own real pane** (autoscaling independently of price and of the other oscillator panes, shared time axis + one crosshair) from a fixture within `1e-6` of the Python series; the truncation-invariance (no-lookahead) test passes on each mirror; a LayersPanel toggle shows/hides each pane (creating/removing the pane, or `applyOptions({visible})` with the pane retained — the wrapper's documented contract); the TS↔pydantic `OverlayKind` literal-parity guard carries the new kinds; each new term has a glossary entry with symmetric en/ru keys; typecheck + lint + jest green.

### Phase 7 — Money-flow rendering (UI) — *v5 panes*
- **Owner skill:** ui-builder
- **Blocked on:** [Plan 0095](0095-lightweight-charts-v5-migration.md); serial-after phase 6 (reuses its oscillator-pane wrapper).
- **What:** MFI as a 0–100 real pane, Chaikin Money Flow as a zero-centered real pane, and the Accumulation/Distribution line as a cumulative-line real pane — each its own `addPane()` pane via the phase-6 wrapper, each toggleable, each with client-compute parity + glossary + locales, same discipline as phase 6.
- **Files touched:** as phase 6 (`lib/volume.ts` mirrors for A/D + CMF + MFI, the oscillator-pane wrapper, LayersPanel, glossary, locales, tests).
- **Done when:** each money-flow indicator draws in its own real pane within `1e-6` of the Python series on a fixture; truncation-invariance passes; toggles work; parity guard + glossary + en/ru symmetric; jest green.

### Phase 8 — Divergence chart-delivery channel (backend) — *ADR-0090*
- **Owner skill:** dev
- **What:** add a dedicated **`chart.divergences v1`** SSE channel so a detected divergence reaches the chart (the renderer never fetches the snapshot; the tool must push). A new `ChartDivergencesPayloadV1{ symbol: str, timeframe: str, divergences: list[Divergence] }` in `events/payloads.py`, registered in `TYPE_REGISTRY` as `chart.divergences`, carrying the analysis `Divergence` **inline** (the same inline-model choice `chart.highlight`/`Marker` and `signal.evaluated`/`SignalEvaluation` make — `Divergence` is already pure geometry; its `oscillator` field is the pane-routing key, its `PivotPoint`s the drawable anchors). Thread an `event_bus` into `register_detect_divergences` / `_detect_divergences_response` (captured by closure, exactly like `detect_chart_patterns`), and **publish one `chart.divergences` event when the scan returns a non-empty result** for the scanned `symbol`/`timeframe`. An empty result (scanned, none found) and the `no_bars` miss publish **nothing** (parity with `detect_chart_patterns`'s `count=0` no-publish). The tool's data-return shape (`{result, partial_reason, scanned_at}`) is **unchanged** — publishing is an added side effect, not a contract change. Regenerate `docs/reference/` (the new event surfaces via `TYPE_REGISTRY`, ADR-0064). *(The publish body stays in the tool module — a single caller; if a `POST /scan_divergences` recompute route is later added (ADR-0090 followup), it extracts to `mcp_tools/_shared/` then, the Plan 0072 move — not now.)*
- **Files touched:** `src/market_analyser/events/payloads.py` (payload + `TYPE_REGISTRY` + `__all__`), `src/market_analyser/api/mcp_tools/detect_divergences.py` (event_bus + publish), the tool's registration call site (pass `event_bus`), `tests/**` (payload validation + publish-on-nonempty / no-publish-on-empty-or-no_bars), `docs/reference/events.md` (regen).
- **Done when:** `ChartDivergencesPayloadV1` validates a symbol/timeframe + a `Divergence` list and is in `TYPE_REGISTRY["chart.divergences"]` with `VERSION == 1`; `detect_divergences` on a populated symbol whose scan finds a divergence publishes exactly one `chart.divergences` event whose `divergences` equal the tool's `result` (same anchors), for the scanned `symbol`/`timeframe`; an empty-result scan and a `no_bars` miss publish nothing; the tool's returned `{result, partial_reason, scanned_at}` is byte-identical to before (no data-contract change); `apiref --check` exits 0; `mypy --strict` + `ruff` clean.

### Phase 9 — Divergence visualization (UI) — *v5 panes, `chart.divergences`*
- **Owner skill:** ui-builder
- **Blocked on:** phase 8 (the `chart.divergences` channel); [Plan 0095](0095-lightweight-charts-v5-migration.md); serial-after phases 6–7 (the oscillator pane must exist to draw the oscillator-pivot segment on it — reuse their `useOscillatorPanes` wrapper).
- **What:** consume the **`chart.divergences`** channel and draw each active `Divergence` as two connecting segments — one across the price pivots on the **price pane (pane 0)**, one across the oscillator pivots on **that oscillator's own v5 pane** (resolved from the payload's `oscillator` field via the phases 6–7 `useOscillatorPanes` wrapper) — reusing the trendline overlay primitive (ADR-0049/0061, migrated to v5 by Plan 0095). A primitive attaches to a series/pane in v5, so the oscillator-pane segment attaches to that oscillator's pane series; the price-pane segment attaches to the main series as today. If the divergence's oscillator pane is **not currently shown**, phase 9 **ensures it** (create-or-reuse via the phase-6 wrapper) before attaching the oscillator segment, so the second segment always has a pane. Color-coded by class (regular vs hidden) and direction (bullish vs bearish), with a label and a glossary tooltip explaining the divergence type. Mirror `Divergence` / `DivergenceKind` / `PivotPoint` and `ChartDivergencesPayloadV1` in `types/events.ts`; add the `chart.divergences` envelope + a `.strict()` Zod schema; add an `applyChartDivergences` reducer path that applies the payload **only when `symbol`+`timeframe` match the active chart** (the ADR-0045/0059 active-chart-gate) and clears divergences on a symbol/timeframe change, mirroring `applyChartTrendlines`.
- **Files touched:** `desktop/renderer/types/events.ts` (mirrors + envelope + Zod `.strict()`) + `handlers/chartHandlers.ts` (`applyChartDivergences` + state field + clear-on-context-change) + chart + trendline primitive (v5) attach-to-oscillator-pane + tooltip + glossary (`divergence` category: regular/hidden × bullish/bearish) + `en.ts`/`ru.ts` + `events.test.ts` parity guard over the `Divergence` field set + jest suites.
- **Done when:** a real `chart.divergences` dispatch for the active chart renders a bearish RSI divergence as the two labeled segments — the price higher-high line on the price pane and the RSI lower-high line **on the RSI oscillator pane** (its pane ensured if it was off) — in the divergence colour; a hidden-bullish case renders distinctly; a payload for a non-active symbol/timeframe is dropped; divergences clear on a symbol/timeframe switch; the glossary tooltip shows the meaning on hover; a malformed payload is Zod-dropped with a loud `console.warn`; the TS↔pydantic parity guard asserts the `Divergence` field set; typecheck + lint + jest green.

### Phase 10 — Live smoke (human)
- **Owner skill:** human
- **Done when (user-run):** `analyze_symbol BTC-USD 1d` / `ETH-USD 1d` return the new oscillator + money-flow values; on a symbol/timeframe where price prints a higher high while RSI prints a lower high, `detect_divergences` (and the snapshot `recent_divergences`) flags a regular bearish divergence with sane anchors; the chart toggles each new pane, and the divergence renders as the connecting segments with a working glossary tooltip; money-flow panes track accumulation/distribution; nothing in any output or tooltip reads as a buy/sell call; an empty-cache symbol is an honest miss, not a fabricated result.

## Data shapes

```python
# illustrative — not the final interface

# analysis/indicators.py value objects (mirror BollingerValue/AdxValue shape)
class StochasticValue(BaseModel):   # frozen
    k: float
    d: float

# snapshot indicators dict gains (flat dict[str, float | None]):
#   stoch_k, stoch_d, stoch_rsi, cci, williams_r, roc, mfi, ad_line, cmf

# analysis/types.py
DivergenceKind = Literal["regular_bullish", "regular_bearish",
                         "hidden_bullish", "hidden_bearish"]

class Divergence(BaseModel):          # frozen, extra="forbid", conditions-only
    oscillator: Literal["rsi", "macd_hist", "obv", "mfi"]  # ALSO the pane-routing key (ph8/9)
    kind: DivergenceKind
    price_pivots: list[PivotPoint]    # the two price anchors (reused geometry shape)
    oscillator_pivots: list[PivotPoint]  # PivotPoint.price = oscillator VALUE (y on its pane)
    bar_index: int                    # the confirming bar (trailing knowability)
    strength: float                   # 0..1, detector-defined — not a probability

# events/payloads.py — the phase-8 delivery channel (ADR-0090), carries Divergence inline
class ChartDivergencesPayloadV1(BaseModel):   # VERSION=1, frozen, extra="forbid"
    symbol: str
    timeframe: str
    divergences: list[Divergence]     # TYPE_REGISTRY["chart.divergences"]; active-chart-gated in the reducer
```

## Risks & open questions

- Risk: **divergence detection is heuristic** (which pivots to pair, how far back, minimum pivot separation). Bad pairing yields noise. Mitigation: name the constants (pivot window, lookback, min separation) as module constants; pin the pairing rule with fixtures; default to the two most-recent confirmed pivots of a kind. Owned like candlestick thresholds (ADR-0023).
- Risk: **oscillator panes may need a framework that doesn't exist yet.** The renderer draws overlays on the price pane + the OBV bottom strip; a general multi-oscillator-pane system may be net-new. Mitigation: phase 6 explicitly reconciles plan-vs-reality and builds the reusable pane component if absent (the Plan 0076 pattern) — flagged, not assumed.
- Risk: client-compute drift between the TS mirrors and the Python indicators. Mitigation: Python-generated fixtures + `1e-6` parity tests per indicator (the established `computeBbands` guard), plus truncation-invariance on each mirror.
- Open question: exact oscillator set to *surface in the snapshot* vs *tool/overlay only*. Default: all nine values in the snapshot (cheap floats), divergence as a list. Trim in phase 3 if the snapshot grows unwieldy.
- Risk: `EXPECTED_FULL_TOOLSET` is a moving baseline. Mitigation: phase 5 bumps to the actual post-add count, not a hard-coded target (the Plan 0074/0078 note).

## What this plan does NOT do

- **No new momentum *vote*.** The `momentum` stance stays RSI-zone + MACD (ADR-0023); the new oscillators are reported facts, not a re-classification. Changing the stance is a separate decision.
- **No divergence *strategy* or alert.** Encoding a divergence entry is `strategy-author`; a divergence `create_watch` alert is a separate plan.
- **No structural/price-action trend, Fibonacci, or pivots** — those are Plan 0092.
- **No `advisor` consumption** of oscillators/divergence — an ADR-0029 question for later.
- **No multi-timeframe divergence** (divergence across timeframes) — a followup.

## Followups (after this lands)
- Feed divergence / money-flow into the `advisor` as a basis input (ADR-0029 scope).
- Divergence-based `create_watch` alert and/or a divergence-entry strategy (`strategy-author`).
- Multi-timeframe divergence confluence.
- Refine the `momentum` stance to optionally weigh Stochastic/MFI.
