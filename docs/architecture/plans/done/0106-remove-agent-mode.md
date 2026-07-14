# 0106 — Remove agent mode (always-on gesture forwarding)

> **Status:** done (closed 2026-07-14 — ph1 `63638ef` dev sidecar removal, ph2 `7d6f8bb` ui-builder renderer removal, ph3 human smoke PASS same day; clean Mode 4, no blockers/majors, two sanctioned-residue minors — stale gate references in `ui_events/buffer.py` docstring + `alerts/scheduler.py` comment, both files pinned untouched by this plan; ADR-0101 accepted at close, ADR-0021/0099 marked amended; grep-verified: zero live `agent_mode` in `src/` beyond the sanctioned startup cleanup in `api/__main__.py`, zero identifiers in `desktop/`)
> **Created:** 2026-07-14
> **Owner skill(s):** dev, ui-builder, human
> **Related ADRs:** [0101-remove-agent-mode-gate](../adrs/0101-remove-agent-mode-gate.md) (paired — accepts at close; amends [ADR-0021](../adrs/0021-renderer-to-agent-feedback.md)'s gate and [ADR-0099](../adrs/0099-user-drawing-readback-and-advisory-positions.md)'s push-gate clause), [0065-neutral-ui-event-buffer-core](../adrs/0065-neutral-ui-event-buffer-core.md) (buffer core untouched), [0064-generated-sidecar-api-reference](../adrs/0064-generated-sidecar-api-reference.md) (apiref regen), [0063-in-house-i18n-and-reason-codes](../adrs/0063-in-house-i18n-and-reason-codes.md) (key removal keeps en/ru parity)

## TL;DR

Delete the agent-mode toggle end-to-end, per [ADR-0101](../adrs/0101-remove-agent-mode-gate.md): the chart-header button, the `useAgentMode` hook, the `GET`/`PUT /agent_mode` routes, the persisted `agent_mode.json`, the 403 gate on `POST /ui_events`, and the `ui.agent_mode_toggled v1` event. Gesture forwarding (`ui.bar_clicked`, `ui.range_selected`) becomes unconditional; **select-range mode stays** (it is a gesture switch, not a consent switch); the ADR-0021 buffer/tool/resource transport and the renderer-bearer route auth are untouched. Runs **before Plan 0097** so the drawing dock lands on the simplified gesture machine.

## Context & problem

[ADR-0101](../adrs/0101-remove-agent-mode-gate.md) records why the gate no longer earns its keep: [ADR-0099](../adrs/0099-user-drawing-readback-and-advisory-positions.md) mirrors user drawings ungated (guarding a click while streaming resistance lines is incoherent), the channel is pull-only (nothing broadcasts), the vocabulary is deliberate gestures, and the alert path already bypasses the gate. What remains is pure removal work across both processes plus the doc/reference fallout.

## Decision

Two removal phases (sidecar, then renderer), then a human smoke. No new mechanism, no schema addition — the wire shrinks. Amending [Plan 0104](0104-drawing-readback-and-position-tools.md) phase 4 (drop its agent-mode conditional) is done by architect alongside this plan's authoring, not by the implementer.

## Implementation phases

Each phase ships as its own commit.

### Phase 1 — Sidecar removal

- **Owner skill:** dev
- **What:** Delete the agent-mode state module and its two routes; remove the 403 agent-mode check from `POST /ui_events` (renderer-bearer auth stays exactly as is); drop `ui.agent_mode_toggled` from the accepted envelope vocabulary; best-effort delete a leftover `agent_mode.json` at startup (ignore errors); update `get_pending_ui_events`'s docstring (no more "when agent mode is on" framing); regenerate the API reference.
- **Files touched:** `src/market_analyser/api/ui_events/agent_mode.py` (delete), `src/market_analyser/api/routes/agent_mode.py` (delete), `src/market_analyser/api/routes/ui_events.py`, the ui-events envelope vocabulary module, `src/market_analyser/api/app.py` / `__main__.py` / `apiref/wiring.py` (wiring), `src/market_analyser/api/mcp_tools/get_pending_ui_events.py` (docstring), affected tests, `docs/reference/` (regenerated), data-dir docs (drop `agent_mode.json` from the [ADR-0020](../adrs/0020-shared-data-dir-contract.md) file list).
- **Done when:** `POST /ui_events` accepts a valid `ui.bar_clicked` / `ui.range_selected` envelope with no mode precondition (asserted) and still 401s without the renderer bearer (asserted); `GET`/`PUT /agent_mode` return 404 (asserted); an `ui.agent_mode_toggled` envelope is rejected as unknown type (asserted); a pre-seeded `agent_mode.json` is gone after startup (asserted); the alert-scheduler append path is byte-identical (its existing tests green, no source change); `EXPECTED_FULL_TOOLSET` unchanged; `pytest`, `mypy --strict`, `ruff`, and `apiref --check` green; a repo grep for `agent_mode` in `src/` returns only historical ADR/plan references, zero live code.

### Phase 2 — Renderer removal

- **Owner skill:** ui-builder
- **What:** Delete `AgentModeToggle` and `useAgentMode` (+ their tests); remove the `agentMode` parameter and conditionals from `useChartGestures` (forward whenever `symbol`/`timeframe` are present); un-thread the prop from `CandlestickChart.tsx`, `ChartToolbar.tsx`, `OhlcvView.tsx`, and any other referencing view; drop `getAgentMode`/`putAgentMode` from the typed client; drop `agent_mode_toggled` from `types/ui-events.ts`; remove the now-unused en + ru keys (parity preserved — both sides shrink together). Select-range mode's toolbar switch and drag behavior are **not** touched.
- **Files touched:** `desktop/renderer/components/AgentModeToggle.tsx` + test (delete), `desktop/renderer/hooks/useAgentMode.ts` + test (delete), `desktop/renderer/hooks/useChartGestures.ts` + tests, `desktop/renderer/components/CandlestickChart.tsx` + gesture tests, `desktop/renderer/components/ChartToolbar.tsx`, `desktop/renderer/views/OhlcvView.tsx` + test, `desktop/renderer/views/DefiPnlView.tsx` (whatever its reference is), `desktop/renderer/api/client.ts` + test, `desktop/renderer/types/ui-events.ts`, `desktop/renderer/locales/en.ts` + `ru.ts`.
- **Done when:** a bar click and a select-range drag each POST their envelope with no mode check (asserted via mocked client); select-range mode still toggles drag-vs-pan exactly as before (existing gesture tests adapted, not weakened); no `agentMode`/`agent_mode` identifier remains under `desktop/` (grep-asserted in a test or verified in review); typecheck (all tsconfigs), lint, renderer jest, `test:main`, and `gen-types:check` green; en/ru catalogs stay key-parity clean with no orphaned keys; no CSP change (diff-confirmed).

### Phase 3 — Human smoke

- **Owner skill:** human
- **What:** End-to-end in the running app, agent attached over MCP.
- **Done when:** (a) no agent-mode toggle appears anywhere in the UI; (b) clicking a bar, then asking the agent to check pending events, surfaces the `ui.bar_clicked` envelope via `get_pending_ui_events` — no setup step needed; (c) a select-range drag surfaces `ui.range_selected` the same way, and normal drag still pans when select-range mode is off; (d) a triggered watch alert still reaches the agent through the pending-events path (the ADR-0021 transport intact); (e) `GET /agent_mode` 404s; (f) en and ru render with no missing-key artifacts.

## Risks & open questions

- **Risk: chart-file contention.** Phase 2 touches `CandlestickChart.tsx`/`ChartToolbar.tsx` — the 0096→0097→0104→0098 chain's hot files. Mitigation: this plan slots **early in the remaining chain** (0096's phases are already done, close pending; Plan 0105 chart-legibility, authored the same day in a parallel session, sits adjacent to 0096): 0096-close → 0105 → **0106** → 0097 → 0104 → 0098; never worktree-parallel with any of them.
- **Risk: a hidden consumer of `GET /agent_mode`.** The renderer polls it today; anything else reading it would 404 after phase 1. Mitigation: phase 1's grep done-when sweeps `src/` and `desktop/` both; the e2e suite runs in phase 2's gates.
- **Open question: none.** The removal is mechanical once ADR-0101 is accepted; the only judgment calls (keep select-range mode, keep single-instance, keep the transport) are settled in the ADR.

## What this plan does NOT do

- **No buffer/transport change** — cap, drop-oldest, drain/peek, the MCP tool, and the resource are byte-identical.
- **No select-range-mode change** — the gesture switch stays renderer-local and user-facing.
- **No single-instance revert** — the lock stays (ADR-0101 relocates its rationale).
- **No auth change** — `POST /ui_events` keeps the renderer bearer; the dual-bearer split (ADR-0014) is untouched.
- **No new events, tools, routes, or CSP change** — the wire only shrinks.

## Followups (after this lands)

- None owned here. If the MCP surface ever leaves this machine (the ADR-0073 tunnel arc), that plan must re-decide consent for renderer-originated events (recorded in ADR-0101's consequences).
