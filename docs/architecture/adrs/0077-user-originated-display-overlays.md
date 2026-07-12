# ADR-0077 — User-originated display overlays (renderer-owned indicator layer)

> **Status:** accepted (Plan 0082 close 2026-07-12 — user-overlay layer + `bbands` render + add-indicator form shipped; the classification test *client-computed + no-sidecar-call + no-domain-change ⇒ renderer-owned* is now load-bearing in `lib/userOverlays.ts`; user overlays never cross the wire)
> **Date:** 2026-07-11
> **Related plan(s):** [0082-bollinger-bands-and-overlay-controls](../plans/0082-bollinger-bands-and-overlay-controls.md) (accepts this ADR at close)
> **Related ADRs:** [ADR-0015](0015-claude-code-primary-control-surface.md) (agent is the primary control surface — **refined here**), [ADR-0017](0017-live-ui-updates-via-sse.md) (agent→renderer overlay events), [ADR-0039](0039-renderer-theming-localstorage.md) (renderer-owned display prefs in `ma.*` localStorage — the pattern reused here), [ADR-0062](0062-user-chart-style-overrides.md) (user chart-style overrides — the closest precedent), [ADR-0023](0023-technical-analysis-surface.md) (client-side indicator duplication is accepted for display)

## Context

[ADR-0015](0015-claude-code-primary-control-surface.md) makes Claude Code the **primary control surface**: symbols, timeframes, indicators, and backtest parameters "all originate" in the agent, and the renderer's control role "shrinks to near-zero" while its viewer role expands. Chart overlays today follow that model strictly — the agent calls `show_chart`/`update_chart`, the sidecar emits `chart.show`/`chart.update` over SSE ([ADR-0017](0017-live-ui-updates-via-sse.md)) carrying an `OverlaySpec`, and the renderer draws it. The only user-facing overlay control is a passive legend (`LayersPanel`) that toggles visibility of what the agent already drew. There is no way for the user to *add* an EMA or a Bollinger Band from the UI.

The user has asked for exactly that: a form-style control to add and parameterize overlays directly on the chart. This raises a genuine boundary question — **is a user-added indicator overlay a control action (reserved for the agent by ADR-0015) or a display preference (owned by the renderer)?**

Three facts make this non-obvious, and on balance point to "display preference":

1. **Indicator overlays are computed entirely client-side.** EMA, SMA, Bollinger Bands, Supertrend, Ichimoku, VWAP, and OBV are all derived by the renderer from bars it already holds — no sidecar call, no new data fetch. [ADR-0023](0023-technical-analysis-surface.md) already sanctions this display-side duplication of indicator math. Adding an overlay changes **nothing** on the sidecar and persists nothing to domain state.
2. **There is already a sanctioned class of renderer-owned display state.** [ADR-0039](0039-renderer-theming-localstorage.md) (theme) and [ADR-0062](0062-user-chart-style-overrides.md) (per-theme colours, line widths, candle series-type) establish that the renderer may **originate and persist** presentation preferences in `localStorage['ma.*']` without the agent. A user-added overlay is the same species of state as "draw candles in this colour" — a view choice, not a domain command.
3. **ADR-0015's own negatives anticipate this.** It notes that "some existing renderer code patterns … become legacy on contact" and frames the rule as being about *who drives analysis, backtests, and data* — not about who may toggle a purely visual, client-computed decoration.

The countervailing force: overlays currently arrive **only** from the agent, so the renderer originating an `OverlaySpec` is new, and once two sources of overlays coexist the renderer inherits merge, provenance, and removal semantics it did not have before. That real cost is why this warrants an ADR rather than a silent renderer change.

## Decision

We classify **user-added indicator overlays as renderer-owned display preferences**, not control actions, and permit the renderer to originate them. Concretely:

- The renderer may construct `OverlaySpec` values locally for the **display-only indicator kinds** (`ema`, `sma`, `bbands`, `supertrend`, `ichimoku`, and — as their render paths land — `vwap`, `obv`), hold them in a **user-overlay layer keyed by `(symbol, timeframe)`**, and persist that layer in `localStorage['ma.userOverlays']` following the [ADR-0039](0039-renderer-theming-localstorage.md) `ma.*` convention.
- User overlays are **never serialized to the sidecar and never trigger an MCP call.** They exist only in the renderer. The `OverlaySpec` shape is reused for code economy (one reconcile path, one registry), not because these specs go on the wire.
- The renderer **merges** user overlays with agent-pushed overlays for drawing, deduped by the existing overlay identity (`overlayKey`/`overlayLayerId` = `overlay:<kind>:<period>`). Identical agent and user requests collapse to one drawn series (idempotent).
- User overlays are **sticky**: an agent `chart.show`/`chart.update` that replaces the *agent* overlay set does **not** clear the user layer. The user's decoration survives the agent redrawing the chart, exactly as the user's theme and chart-style survive it.
- Removal is provenance-scoped: the user may **remove** their own overlays (they own them); agent-pushed overlays keep the existing **hide-only** toggle (the agent owns them, the user can suppress but not delete).

This scope is **deliberately narrow**. It does **not** extend to:

- `price_line` overlays — those carry agent *analysis* semantics (support/resistance from `analyze_symbol`, per Plan 0047), not a neutral indicator; they stay agent-originated.
- Anything requiring a sidecar call, a data fetch, a backtest, a screen, or any domain-state change. Those remain the agent's exclusively, unchanged.

This **refines** [ADR-0015](0015-claude-code-primary-control-surface.md) the same way [ADR-0039](0039-renderer-theming-localstorage.md) and [ADR-0062](0062-user-chart-style-overrides.md) already did — it does not supersede or weaken it. The load-bearing rule ("the agent is the sole surface for data, analysis, backtests, and any sidecar/domain command") is untouched. The line we are drawing is explicit: **a renderer action is a display preference (renderer-owned) iff it is client-computed, issues no sidecar call, and changes no domain state; otherwise it is a control action (agent-owned).** User indicator overlays fall on the display-preference side of that line.

## Consequences

### Positive

- **The user gets direct chart control for display**, satisfying the stated ask, without any new sidecar surface, MCP tool, or event type — the whole feature lives in the renderer.
- **It reuses existing machinery**: the client-side indicator math ([ADR-0023](0023-technical-analysis-surface.md)), the `OverlaySpec`/`OVERLAY_REGISTRY`/`useOverlaySeries` reconcile path, and the `ma.*` localStorage persistence convention ([ADR-0039](0039-renderer-theming-localstorage.md)). No new architectural primitive is introduced.
- **The display-vs-control boundary is now explicitly recorded**, so future renderer-feature questions ("can the user add X from the UI?") have a test to apply rather than re-litigating ADR-0015 each time.
- **Consistency with the theme/chart-style precedent** makes the mental model coherent: the renderer owns *how the chart looks*; the agent owns *what data and analysis it shows*.

### Negative

- **Two overlay sources must coexist.** The renderer inherits merge, dedup, provenance, and removal-semantics complexity it did not have when overlays were agent-only. `LayersPanel` and the reconcile path grow. This is the real cost and it is paid in renderer code.
- **Sticky user overlays can surprise.** A user who added BB on `BTC-USD 1d` will see it persist even after the agent issues a fresh `chart.show`. This is intentional (it mirrors theme stickiness) but is a behaviour the user must internalise; a user overlay is not cleared by "redraw this chart".
- **Persisted per-`(symbol, timeframe)` state accumulates.** Every symbol/timeframe the user decorates adds a `localStorage` entry. The store must bound/prune (e.g. cap entries, drop empties) so it does not grow without limit; blocked/full storage must degrade gracefully (the ADR-0039 pattern).
- **A slight divergence risk between user-overlay UX and agent-overlay UX.** Users can delete their own overlays but only hide agent ones; the legend must make that distinction legible or it reads as inconsistent.

### Neutral

- **The wire contract is unchanged.** `OverlaySpec`, the `chart.*` events, and the SSE vocabulary are untouched by user overlays (which never leave the renderer). The only wire-adjacent change in the paired plan is clarifying `bbands`' existing descriptor and, later, adding `vwap`/`obv` kinds so the agent *could* also request them — additive, no version bump, independent of this ADR.
- **The agent remains fully capable of drawing every one of these overlays too.** This ADR adds a user path; it does not remove or diminish the agent path. Both produce the same drawn result via the same registry.
- **Symmetric to ADR-0062.** Where ADR-0062 let the user override *how a series is styled*, this lets the user choose *which indicator series exist*. Same owner (renderer), same persistence convention, same "survives agent redraw" property.

## Alternatives considered

### Alternative A — Keep overlay creation agent-only; only enrich the legend

Leave creation to the agent (the user asks Claude for a Bollinger Band) and limit the UI to richer control over what the agent drew — inline period editing, reordering — with no user-originated overlays.

Rejected because it reads ADR-0015 too strictly. ADR-0015's rule is about *control over data, analysis, and domain operations*; a client-computed visual decoration that touches no sidecar state is the same category as theme and chart-style, which the renderer already owns ([ADR-0039](0039-renderer-theming-localstorage.md)/[ADR-0062](0062-user-chart-style-overrides.md)). This alternative also fails the user's explicit request to add overlays from the UI, forcing a conversational round-trip for a pure display toggle.

### Alternative B — Route user overlay-adds back through the sidecar / an MCP path

Have the "add overlay" control call the sidecar (or feed the agent via a renderer→agent channel), so overlays still originate on the agent side and the renderer stays a pure viewer.

Rejected because the transport does not exist and building it is disproportionate. [ADR-0017](0017-live-ui-updates-via-sse.md) is deliberately one-way (sidecar→renderer); renderer→agent feedback ([ADR-0021](0021-renderer-to-agent-feedback.md)) is a narrow, unrelated channel. Adding a network round-trip and a domain surface so the user can toggle a client-computed line is topology for its own sake — it manufactures a "control action" out of what is intrinsically a display preference, contradicting the very classification this ADR settles.

### Alternative C — A single global user-overlay set (not keyed by symbol/timeframe)

Persist one user-overlay list applied to every chart, regardless of instrument or timeframe.

Rejected on product grounds (and by the user's explicit choice): "Bollinger Bands on everything" is usually not wanted, and an EMA period sensible on a daily chart is often wrong on a 15-minute one. Keying the user layer by `(symbol, timeframe)` matches how a chartist actually thinks — "I keep BB on BTC daily" — at the cost of slightly more storage, which the store bounds anyway.

## Notes

- The paired [Plan 0082](../plans/0082-bollinger-bands-and-overlay-controls.md) implements this: a `bbands` render (the flagship new indicator), the user-overlay store + merge, and the add-indicator form in `LayersPanel`. VWAP and OBV are sequenced follow-ons — OBV's render is the approved-but-unbuilt [Plan 0076](../plans/0076-obv-chart-overlay.md), which this plan coordinates with rather than duplicates.
- The classification test this ADR records — *client-computed + no sidecar call + no domain-state change ⇒ renderer-owned display preference* — is the reusable artifact. Future "can the UI do X directly?" questions should be answered against it before reaching for a new ADR.
