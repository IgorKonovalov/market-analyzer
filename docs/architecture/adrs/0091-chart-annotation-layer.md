# ADR-0091 — Chart annotation layer (agent-wire + user-local, one DrawingSpec)

> **Status:** accepted (Plan 0097 close 2026-07-14)
> **Date:** 2026-07-13
> **Related plan(s):** [0097-chart-drawing-dock](../plans/0097-chart-drawing-dock.md) (accepts this ADR at close)
> **Sibling channel:** [ADR-0090](0090-cross-pane-divergence-delivery.md) (the `chart.divergences v1` cross-pane channel, Plan 0091) is the other agent→chart geometry event added the same day; `chart.annotations v1` here is the freeform-drawing analog. Both push agent-derived geometry over SSE and reuse the ADR-0049/0061 draw primitive; they are independent events, not a shared channel.
> **Related ADRs:** [ADR-0077](0077-user-originated-display-overlays.md) (user-originated display overlays — the two-source merge precedent, and the renderer-owned *user* side reused here), [ADR-0015](0015-claude-code-primary-control-surface.md) (agent owns analysis — **why the agent annotation path crosses the wire**), [ADR-0017](0017-live-ui-updates-via-sse.md) (SSE event vocabulary — a new `chart.annotations v1` event), [ADR-0049](0049-chart-trendline-overlay-primitive.md) / [ADR-0061](0061-trendline-pattern-identity-and-colour.md) (the trendline primitive this reuses as render substrate), [ADR-0059](0059-trendline-event-channel-and-recompute.md) (the logical-coordinate + recompute-on-load handling drawings inherit), [ADR-0088](0088-lightweight-charts-v5-panes.md) (v5 `ISeriesPrimitive` substrate), [ADR-0039](0039-renderer-theming-localstorage.md) (`ma.*` persistence convention), [ADR-0063](0063-in-house-i18n-and-reason-codes.md) (en/ru parity), [ADR-0008](0008-electron-shell-conventions.md) (CSP unchanged)

## Context

[Plan 0096](../plans/0096-chart-and-app-declutter.md) reserved left-edge layout space for a **drawing dock** but deferred it as a new capability. This ADR is the durable half of building it ([Plan 0097](../plans/0097-chart-drawing-dock.md)). The user specified four tool families (trendline/ray, horizontal/vertical line, rectangle/zone, Fibonacci retracement), **per-symbol** persistence anchored to `(time, price)` so a drawing shows across every timeframe for that symbol, **full editing** (select / drag-endpoints / delete / snap-to-OHLC), and — the decisive scoping choice — **the agent must be able to place and read annotations too**, not just the user.

The chart already renders shape primitives — `TrendlinePrimitive` ([ADR-0049](0049-chart-trendline-overlay-primitive.md)/[ADR-0061](0061-trendline-pattern-identity-and-colour.md)), `IchimokuPrimitive`, `PatternSpanPrimitive` — as v5 `ISeriesPrimitive`s ([ADR-0088](0088-lightweight-charts-v5-panes.md)). What does not exist is a **user-authored, persisted, editable** drawing layer, or **any way for the agent to place a freeform annotation**.

[ADR-0077](0077-user-originated-display-overlays.md) settled a closely-related boundary and its test is the hinge here: *client-computed + no sidecar call + no domain-state change ⇒ renderer-owned display preference; otherwise it is a control/analysis action ⇒ agent-owned.* It concluded that user **indicator overlays never cross the wire** — they are pure client-computed decoration with no analysis semantics. Annotations are different **on exactly one axis**: a drawing carries *analysis meaning* ("resistance is here", "this is the accumulation zone"). When the **user** draws it, that meaning is a private note — renderer-owned, like an overlay. When the **agent** draws it, that meaning is the agent *communicating its analysis to the user*, which [ADR-0015](0015-claude-code-primary-control-surface.md) places squarely on the agent side, and the only way the agent reaches the chart is over the SSE wire ([ADR-0017](0017-live-ui-updates-via-sse.md)). So annotations are **intrinsically two-source**, and — unlike overlays — one of those sources *requires* a wire path. That is the decision this ADR records: not whether annotations are renderer-owned (they are, on the user side), but that the annotation layer, alone among the renderer's display layers, also grows an **agent wire path**, and why that is consistent with (not a reversal of) ADR-0077.

## Decision

We introduce a single **`DrawingSpec`** shape describing a freeform chart annotation of one of the supported kinds (`trendline`, `ray`, `hline`, `vline`, `rect`, `fib`), with all geometry anchored to `(time, price)` data coordinates. `DrawingSpec` is defined **once in the sidecar** (pydantic) with TypeScript generated from it (the repo's `gen-types` path), and it serves **both** sources through **one render/merge path**:

- **Agent source — over the wire.** A new `annotate_chart` MCP tool takes a symbol + a declarative set of `DrawingSpec`s and emits a new **`chart.annotations v1`** SSE event carrying that set (declarative replace of the *agent* annotation set for the symbol, mirroring how `chart.update` replaces the agent overlay set). Agent annotations are **not persisted by the renderer** — the agent re-issues them, exactly as agent overlays behave under [ADR-0077](0077-user-originated-display-overlays.md).
- **User source — renderer-local.** The drawing dock constructs `DrawingSpec`s locally and persists them in `localStorage['ma.userDrawings']`, **keyed by symbol** (not `(symbol, timeframe)`), following the [ADR-0039](0039-renderer-theming-localstorage.md) `ma.*` convention (bounded/pruned). User drawings **never cross the wire** and issue no sidecar call — renderer-owned, exactly like user overlays.
- **One merge path, provenance-scoped editing.** The renderer merges the two sources for drawing, deduped by drawing identity. **The user may select / drag / delete / re-anchor their own drawings** (they own them); **agent-placed drawings are hide-only** (the agent owns them; the user can suppress but not mutate), the same provenance rule ADR-0077 set for overlays. `DrawingSpec` carries a provenance discriminator so the merge and the edit-affordance gating are unambiguous.

Coordinates anchor to `(time, price)`, so a drawing is a claim about **price over time** and renders across **every timeframe** for its symbol; extend-to-edge kinds (`ray`, `hline`, `vline`) inherit the logical-coordinate / off-grid-time handling [ADR-0059](0059-trendline-event-channel-and-recompute.md) already established for pattern trendlines, and drawings recompute on history load the same way.

This **refines** [ADR-0077](0077-user-originated-display-overlays.md) rather than contradicting it: the user side is governed by ADR-0077's rule unchanged (renderer-owned, no wire); the ADR-0077 classification test still holds — a *user* drawing is client-authored with no sidecar call, so renderer-owned. The new element is that the **agent** side of *this particular layer* is an analysis-communication action, which ADR-0015 already reserves for the agent and which necessarily rides the SSE wire. Drawings are **display**, not domain state — no determinism/lookahead concern applies.

## Consequences

### Positive

- **Delivers both halves of the ask:** a full user drawing dock *and* an agent that can mark levels/zones for the user to see.
- **One shape, one render path.** `DrawingSpec` + the reused `TrendlinePrimitive`/span-primitive substrate ([ADR-0049](0049-chart-trendline-overlay-primitive.md)/[ADR-0088](0088-lightweight-charts-v5-panes.md)) means neither source has bespoke rendering; the [ADR-0077](0077-user-originated-display-overlays.md) two-source merge + provenance pattern is reused wholesale.
- **The display-vs-control boundary is extended coherently.** ADR-0077's test still answers "can the UI do X?"; this ADR adds the companion rule for annotations: *user annotation = renderer-owned; agent annotation = analysis communication = wire.* Future "should this be on the wire?" questions have a worked precedent.
- **Agent drawings compose with existing analysis output** — the agent can annotate what `analyze_symbol` finds, turning a described level into a drawn one.

### Negative

- **A genuinely new wire surface** — an MCP tool, a versioned SSE event, and a generated wire type — the very thing [ADR-0077](0077-user-originated-display-overlays.md) deliberately avoided for overlays. This is the real cost of the "agent can place too" choice and it is paid in sidecar + generated-type + event-versioning surface.
- **Editing/hit-testing is significant renderer engineering.** Select, drag-endpoints, delete, and OHLC snapping across six geometry kinds (point-to-segment / ray / axis-line / rect-edge / fib-line distance) is the bulk of the work and the main regression surface.
- **A third two-source-merge layer.** After agent-vs-user overlays (ADR-0077) and preset composition (ADR-0089), the renderer now also merges agent-vs-user *drawings* with its own provenance rules — more display state to keep coherent and legible.
- **Coordinate edge cases.** Extend-to-edge and off-grid-time anchors reintroduce the logical-coordinate failure class [ADR-0059](0059-trendline-event-channel-and-recompute.md) fixed for pattern trendlines; drawings must ride that same handling or they mis-render on paging/timeframe switch.

### Neutral

- **Agent drawings are ephemeral in the renderer** (re-pushed, not persisted), symmetric with agent overlays; user drawings persist. On reload, user drawings restore and agent drawings await the next push.
- **The event vocabulary grows additively.** `chart.annotations v1` is a new event, not a change to `chart.show`/`chart.update`; existing consumers are unaffected.
- **Symmetric to ADR-0077/0089.** The renderer owns *how the chart looks* (style), *which indicator series exist* (overlays), *which named layout is applied* (presets), and now *which freeform marks the user has drawn* — with the agent able to contribute marks as analysis.

## Alternatives considered

### Alternative A — Renderer-only drawings; no agent path (the ADR-0077 posture)

Keep drawings purely user-authored and local, never on the wire, exactly as overlays. Rejected by the user's explicit choice and on the merits: a level the agent identifies in `analyze_symbol` should be drawable *by the agent*, and drawing it is analysis communication — precisely the class [ADR-0015](0015-claude-code-primary-control-surface.md) reserves for the agent. Denying the agent the annotation path would force the user to re-draw by hand what the agent already found.

### Alternative B — Separate specs/paths for agent vs user drawings

Give agent annotations their own wire type and render path, distinct from a renderer-only user drawing type. Rejected because it doubles the primitive/render/merge code for one visual result; ADR-0077's lesson is that **one shape with a provenance field** and a single reconcile path is simpler and less bug-prone than two parallel systems.

### Alternative C — Per-`(symbol, timeframe)` drawings

Scope a drawing to the timeframe it was drawn on, like the ADR-0077 overlay store. Rejected by the user's choice and the chartist model: a trendline is a claim about price over time, meaningful across timeframes; anchoring to `(time, price)` and showing it on every timeframe for the symbol matches intent, at the cost of the logical-coordinate handling we already own ([ADR-0059](0059-trendline-event-channel-and-recompute.md)).

### Alternative D — Persist agent drawings in renderer localStorage too

Have the renderer persist agent-placed annotations alongside user ones. Rejected: the renderer must not persist agent-authored content (it blurs provenance and would resurrect stale agent marks the agent no longer asserts). The agent re-pushes, symmetric with agent overlays under [ADR-0077](0077-user-originated-display-overlays.md).

## Notes

- The paired [Plan 0097](../plans/0097-chart-drawing-dock.md) implements this: `dev` defines `DrawingSpec` + the `chart.annotations v1` event + the `annotate_chart` tool first (Python-authored, TS generated), then `ui-builder` builds the tool-rail, the six-kind render + full edit + per-symbol persistence, and the agent-source merge.
- The reusable artifact is the companion to ADR-0077's test: **an annotation is renderer-owned when the *user* authors it and wire-borne when the *agent* authors it, because agent authorship is analysis communication (ADR-0015).** This tells future layers whether a new agent-reachable surface is warranted.
- Plan 0097 fills the left-edge rail space [Plan 0096](../plans/0096-chart-and-app-declutter.md) reserved and shares `CandlestickChart.tsx` with it — 0097 serializes after 0096 (recorded in the plans index execution order).
