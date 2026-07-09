# 0076 — OBV chart overlay

> **Status:** approved
> **Created:** 2026-07-09
> **Owner skill(s):** dev, ui-builder, human
> **Related ADRs:** [0023](../adrs/0023-technical-analysis-surface.md) (OBV already computed), [0017](../adrs/0017-live-ui-updates-via-sse.md) (overlay events); render phase **sequences after [Plan 0072](0072-codebase-remediation-audit-2026-07.md) phase 8** (same overlay-drawing code as Plan 0073 phase 4)

## TL;DR

Expose On-Balance Volume as a chart overlay. OBV is already computed and trailing (`analysis/volume.py::obv`/`obv_slope`, surfaced in `analyze_symbol`), but there is no way for the agent to ask the viewer to *draw* it. Add an `obv` kind to `OverlaySpec` (additive, no payload version bump — the ADR-0073-phase-3 pattern) and render it as a separate-pane line, the same way `rsi`/`macd` overlays already draw. The first user-visible behavior is `show_chart` with an `obv` overlay putting an OBV line in its own pane under the price chart.

## Context & problem

The analysis surface already has OBV: `obv()` and `obv_slope()` in `analysis/volume.py`, folded into `volume_summary` and returned in the condition snapshot's `indicators` dict — so the agent can *read* OBV via `analyze_symbol`. What is missing is the **overlay descriptor**: `OverlaySpec`'s `kind` literal is `ema/sma/rsi/macd/bbands/price_line/supertrend` — no `obv` — so the agent cannot request an OBV line on the chart. This is a small, self-contained gap.

## Decision

We follow the existing thin-descriptor overlay convention exactly (the same one Plan 0073 phase 3 uses for `ichimoku`): add `obv` to the `OverlaySpec` `kind` literal (additive, disjoint from `price_line`'s fields, no version bump), and have the **renderer compute OBV from the bars it holds** and draw it in a **separate pane** — mirroring how the `rsi`/`macd` oscillator overlays already render below the price series. No sidecar OBV re-implementation on the wire; the renderer's client-side compute is the accepted ADR-0023 display-side duplication.

Because the render lands in the same overlay-drawing code as Plan 0073 phase 4, the render phase is **sequenced after Plan 0072 phase 8** (the `CandlestickChart.tsx` decomposition), for the same contention reason; the dev descriptor phase touches no contended file and ships immediately.

## Architecture diagram

```mermaid
flowchart LR
    SPEC["events/chart_types.py<br/>OverlaySpec: +'obv' kind"] --> TOOL["show_chart / update_chart<br/>agent requests obv overlay"]
    TOOL -- "chart.show v1 (obv overlay)" --> CHART["renderer (post-0072 ph8)<br/>compute OBV from bars<br/>→ separate-pane line"]
```

## Implementation phases

### Phase 1 — `obv` overlay descriptor
- **Owner skill:** dev
- **What:** Add `obv` to the `OverlaySpec` `kind` literal and its validation, document it on the `show_chart`/`analyze_symbol` tool surface.
- **Files touched:** `src/market_analyser/events/chart_types.py` (`OverlaySpec`), `tests/events/…`, tool descriptions as needed, generated `docs/reference/` via `pnpm gen:api-docs`.
- **Design notes:** `obv` carries no extra fields (no `period` — OBV is cumulative, unparameterized); it rejects `price`/`label`/`role` like the other indicator kinds. `exclude_none` keeps existing overlays byte-unchanged.
- **Done when:** `OverlaySpec.model_validate({"kind": "obv"})` succeeds and dumps to `{"kind": "obv"}`; `{"kind": "obv", "price": 1}` raises; existing overlays are byte-identical on the wire; the generated API reference lists the kind and `--check` passes.

### Phase 2 — Render OBV in a separate pane  *(SEQUENCE AFTER Plan 0072 phase 8)*
- **Owner skill:** ui-builder
- **What:** In the decomposed chart, compute OBV client-side from the loaded bars and draw it as a separate-pane line, mirroring the `rsi`/`macd` oscillator-pane rendering.
- **Files touched:** the post-0072-ph8 overlay/pane modules under `desktop/renderer/…`, the overlay Zod schema (+ `obv` variant), `lib/` helper + `.test.ts`.
- **Design notes:** reuse the existing separate-pane overlay path (`rsi`/`macd`); OBV is an unbounded cumulative line, so the pane auto-scales (no 0–100 band). Client-side OBV mirrors `analysis/volume.py::obv` (cumulative sign-of-close-change × volume).
- **Done when:** requesting an `obv` overlay draws an OBV line in its own pane under the price chart; toggling it off removes the pane; a `lib` unit test pins the OBV series (cumulative up/down volume) against a small fixture; renderer jest + typecheck + lint green.

### Phase 3 — Live smoke
- **Owner skill:** human
- **What:** Confirm end-to-end.
- **Done when:** `show_chart` with an `obv` overlay on a symbol renders the OBV pane and the line tracks accumulation/distribution visibly against price; recorded for close.

## Risks & open questions

- **Render contention.** The render phase edits the same overlay code as Plan 0073 phase 4; both gate after Plan 0072 phase 8. Mitigation: the dev descriptor phase is independent and ships now; the two render phases can be sequenced or batched by the ui-builder session once the decompose lands.
- **Pane scaling.** OBV is unbounded and its absolute level is arbitrary (seed-dependent) — only its shape/slope matters. The pane must auto-scale; no fixed band. Noted for phase 2.

## What this plan does NOT do

- **No `obv_slope` overlay** — the slope is available in `analyze_symbol`; only the OBV line is drawn here.
- **No OBV-based strategy or alert** — separate `strategy-author` work if wanted.
- **No sidecar OBV-on-the-wire** — the renderer computes it, per the overlay convention.

## Followups (after this lands)

- Optionally expose `obv_slope` as a companion overlay or a divergence marker if OBV/price divergence becomes a wanted signal.
