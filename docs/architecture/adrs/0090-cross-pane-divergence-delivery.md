# ADR-0090 — Divergences on a dedicated cross-pane `chart.divergences` channel

> **Status:** proposed (accepts at Plan 0091 close)
> **Date:** 2026-07-13
> **Related plan(s):** [0091-momentum-divergence-moneyflow-layer](../plans/0091-momentum-divergence-moneyflow-layer.md)
> **Related ADRs:** extends the dedicated-channel precedent of [0045](0045-candlestick-pattern-span-delivery.md) (`chart.highlight` markers) and [0059](0059-trendline-event-channel-and-recompute.md) (`chart.trendlines`); exploits the real-panes substrate of [0088](0088-lightweight-charts-v5-panes.md); reuses the trendline draw primitive of [0049](0049-chart-trendline-overlay-primitive.md) / [0061](0061-trendline-pattern-identity-and-colour.md); rides the event stream of [0017](0017-live-ui-updates-via-sse.md)

## Context

Plan 0091's `dev` phases 1–5 landed the divergence backend: the `Divergence` model (`analysis/types.py`), the `detect_divergences` MCP tool, and the snapshot `recent_divergences` field. Phase 8 must draw a detected divergence on the chart, and here the plan hit a genuine gap the earlier phases did not anticipate.

A divergence is **not single-pane geometry**. It is two connecting segments across two panes: a line across the two price pivots on the price pane (pane 0), and a line across the two matched oscillator pivots on **that oscillator's own v5 pane** (the RSI pane, the MFI pane, …). Every existing chart delivery channel carries single-pane geometry:

- `chart.trendlines` (ADR-0059) carries `list[TrendlineSpec]`, all anchored on one series — the price pane. It has no notion of *which pane* a line belongs to, because before ADR-0088 there were no real panes to choose between.
- `chart.highlight` (ADR-0045) carries markers on the price series.

So no channel can carry "the oscillator segment belongs on the RSI pane." Overloading `chart.trendlines` would require smuggling a pane/oscillator discriminator onto `TrendlineSpec`, coupling the pattern-trendline producer to a concept it does not have and breaking the "one `TrendlineSpec` = one line on the price pane" contract ADR-0059/0061 rely on.

The renderer also **never fetches the condition snapshot** — it draws chart geometry only from SSE `chart.*` events or the bars it holds. `detect_divergences` today returns data to the agent only; nothing pushes a `Divergence` to the chart. The two candidate ways to close the gap:

1. **Client-side re-derivation** — ship the oscillator pivots to the renderer (or have it recompute them from bars) and let it pair them itself. Rejected in the Plan 0091 phase-8 design discussion: it duplicates the Python pairing heuristic (which pivots to pair, min separation, confirmation) in TypeScript, reintroduces exactly the client/Python drift risk the plan's own Risk section flags, and makes "what the chart shows" diverge from "what the tool reported."
2. **A proper backend delivery event** — the detector publishes the already-computed divergence geometry on a dedicated channel, mirroring how `detect_chart_patterns` publishes `chart.trendlines`. Chosen.

This is the third dedicated chart channel, but the first that carries geometry **spanning panes** — a wire capability that only became possible once ADR-0088 gave the chart real panes. That novelty, plus a deliberate departure from ADR-0059's recompute-on-load pillar (below), is what makes this a decision worth recording rather than a mechanical additive step.

## Decision

We add a dedicated **`chart.divergences v1`** event carrying pane-routed divergence geometry, and have `detect_divergences` publish it — layer-only and active-chart-gated, exactly like `chart.trendlines`.

- **Payload.** `ChartDivergencesPayloadV1{ symbol, timeframe, divergences: list[Divergence] }`, registered in `TYPE_REGISTRY` as `chart.divergences`. It carries the analysis `Divergence` model **inline** — the same inline-model choice `chart.highlight` makes with `Marker` and `signal.evaluated` makes with `SignalEvaluation`. `Divergence` is already pure geometry: its `price_pivots` / `oscillator_pivots` are `PivotPoint`s (the exact `(ts, price)` anchor shape the ADR-0049 trendline primitive consumes, with the oscillator pivot's `price` field carrying the oscillator *value* at that pivot — its y-coordinate on the oscillator pane), and its `oscillator` field (`rsi` / `macd_hist` / `obv` / `mfi`) is the **pane-routing key**: it tells the renderer which oscillator pane to attach the second segment to. No render-specialized DTO is introduced — a `Divergence` is already a drawable, so a second type would be redundant surface with a second parity guard.
- **Producer.** `detect_divergences` gains an `event_bus` (captured by closure, like `detect_chart_patterns`), and when its scan returns a non-empty result it publishes one `chart.divergences` event for the scanned `symbol`/`timeframe`. An empty result (scanned, none found) and the `no_bars` miss publish nothing — parity with `detect_chart_patterns`'s `count=0` no-publish. The tool's data return shape (`{result, partial_reason, scanned_at}`) is unchanged; publishing is an added side effect, not a contract change.
- **Reducer.** The renderer mirrors `Divergence` (and `PivotPoint` / `DivergenceKind`) in `types/events.ts`, adds the `chart.divergences` envelope + a `.strict()` Zod schema, and applies the payload **only when `symbol`+`timeframe` match the active chart** — the ADR-0045/0059 active-chart-gate, verbatim. Divergences are cleared on a symbol/timeframe change, exactly as trendlines and highlights are.
- **Draw.** For each divergence the renderer draws two segments with the migrated-to-v5 trendline primitive: the price-pivot line on the main series (price pane), and the oscillator-pivot line on the series owning that oscillator's pane, resolved through the phases 6–7 `useOscillatorPanes` wrapper. Colour is keyed by class (regular vs hidden) and direction (bullish vs bearish); each carries a glossary tooltip (ADR-0060) with en/ru parity (ADR-0063).

Like `chart.trendlines`, divergences are **derived geometry, never persisted**.

## Consequences

### Positive
- The cross-pane concept lives on the wire, not in a client re-derivation: the renderer draws exactly what the Python detector paired, so chart and tool never disagree, and the pairing heuristic has one home (ADR-0023) instead of two.
- Carrying `Divergence` inline means the TS↔pydantic parity guard Plan 0091 phase 8 already needs covers both the tool result and the wire shape — one guard, one source of truth.
- The channel is a clean third instance of the ADR-0045/0059 pattern (dedicated, active-chart-gated, derived-not-persisted), so the renderer's mental model stays "one model for all layered chart geometry," now generalised to pane-routed geometry.
- `TrendlineSpec` and the pattern-trendline producer are untouched — the pane-routing concept is confined to the new channel.

### Negative — the price we pay
- **No recompute-on-load path (the ADR-0059 durability half is deliberately deferred).** ADR-0059 gave trendlines a `POST /scan_chart_patterns` recompute so they survive reload/pan without persistence. `chart.divergences` ships **push-only** (tool publish + the snapshot's `recent_divergences` for the agent), with no `POST /scan_divergences` route and no renderer recompute trigger in Plan 0091's scope. The consequence is the exact durability asymmetry ADR-0059 called a defect: a divergence drawn by a tool call vanishes on viewer reload and is not re-derived from current bars. We accept it for now because divergence is a lower-frequency, agent-driven read than continuous pattern trendlines; a `POST /scan_divergences` sibling that reuses the same publish body is the recorded followup that closes the asymmetry when it's worth the chattier recompute.
- **A new event type** to keep mirrored in the hand-maintained `types/events.ts`, guarded by the parity test, plus a new reducer path and its tests — one more entry in the envelope union and in `TYPE_REGISTRY` (so `docs/reference/events.md` regenerates, ADR-0064).
- **The oscillator pane must exist to receive the second segment.** The divergence oscillators (`rsi` / `macd_hist` / `obv` / `mfi`) are not all among the phases 6–7 toggle set, so phase 8 must *ensure-or-create* the target pane via the phase-6 wrapper before attaching the oscillator segment (or draw the price segment alone and disclose). This is the phase's main reconciliation risk, flagged in the plan.

### Neutral
- The producer wiring (threading `event_bus` into `register_detect_divergences`) matches `detect_chart_patterns` exactly; the tool's data contract is unchanged. If a `POST /scan_divergences` recompute route is later added, the publish body extracts to `mcp_tools/_shared/` at that point — the same one-caller→two-caller move `detect_chart_patterns` made in Plan 0072, not warranted while there is a single caller.

## Alternatives considered

### Alternative A — Client-side re-derivation from `recent_divergences` / bars
Ship the pivots (or the raw series) to the renderer and pair them in TypeScript. Rejected: duplicates the Python pairing heuristic on the client, reintroduces the client/Python drift the plan explicitly guards against, and lets the drawn geometry diverge from the tool's reported divergence. The backend already computed the exact anchors — re-deriving them is wasted risk.

### Alternative B — Overload `chart.trendlines` with a pane/oscillator field
Add an optional pane or oscillator discriminator to `TrendlineSpec` and route the oscillator segment from that. Rejected: it welds the pane-routing concept onto the pattern-trendline producer (which has no oscillator), breaks the "one `TrendlineSpec` = one price-pane line" contract ADR-0059/0061 depend on, and reintroduces the coupling ADR-0059 spent its decision removing. A dedicated channel keeps the concept isolated.

### Alternative C — Ship the recompute route now (full ADR-0059 symmetry)
Add `POST /scan_divergences` + a renderer recompute-on-load/range-settle trigger alongside the channel, so divergences survive reload like trendlines. Rejected for Plan 0091's scope (not the decision — the scope): divergence is agent-driven and lower-frequency than continuous pattern detection, so the chattier recompute is not yet worth it; the push-only channel is the smaller correct first step, and the route is a recorded followup that drops onto the same publish body when justified.

### Alternative D — A render-specialized `DivergenceSpec` DTO instead of inline `Divergence`
Map `Divergence` → a drawing-only wire type to decouple the wire from the analysis model. Rejected: `Divergence` is already frozen, `extra="forbid"`, conditions-only pure geometry whose pivots are the anchor shape the primitive consumes — a second type would be redundant surface needing its own parity guard, for no decoupling benefit that the existing inline-model precedent (`Marker`, `SignalEvaluation`) doesn't already accept.

## Notes

This ADR is proposed alongside Plan 0091's inserted backend-delivery phase and accepts at that plan's close ceremony (the ADR-0059/0083 cadence). It changes *how divergence geometry travels to the renderer*, not how a divergence is detected (that math is the pure `analysis.divergence.detect_divergences`, ADR-0023) nor how a line is drawn (the ADR-0049/0061 primitive, migrated to v5 by ADR-0088/Plan 0095). The `Divergence` model itself is unchanged from Plan 0091 phase 4.
