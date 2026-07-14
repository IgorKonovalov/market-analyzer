# ADR-0101 — Remove the agent-mode gate (always-on renderer→agent gesture forwarding)

> **Status:** proposed (Plan 0106 accepts at close)
> **Date:** 2026-07-14
> **Related plan(s):** [0106-remove-agent-mode](../plans/0106-remove-agent-mode.md) (accepts this ADR at close)
> **Related ADRs:** [ADR-0021](0021-renderer-to-agent-feedback.md) (**amended, not superseded** — the agent-mode toggle + gate are removed; the buffer / `get_pending_ui_events` / `ui-events://recent` transport it decided stands unchanged), [ADR-0099](0099-user-drawing-readback-and-advisory-positions.md) (**amended** — its decision 2 "the push event is agent-mode gated" falls with the mode; `ui.drawing_changed` forwards unconditionally), [ADR-0002](0002-ipc-local-http.md) / [ADR-0014](0014-mcp-as-second-sidecar-protocol.md) (the renderer-bearer route auth that remains the real boundary), [ADR-0065](0065-neutral-ui-event-buffer-core.md) (the neutral buffer core, untouched)

## Context

[ADR-0021](0021-renderer-to-agent-feedback.md) introduced **agent mode**: a sidecar-resident toggle (`agent_mode.json`, `GET`/`PUT /agent_mode`, a chart-header button) whose OFF state 403s `POST /ui_events`, so UI gestures reach the agent only with explicit consent. The stated concern was the user "does not want pan/zoom and click gestures silently broadcast."

Three years of design later (in project time: fourteen months), the gate's protective story has eroded on every axis:

- **[ADR-0099](0099-user-drawing-readback-and-advisory-positions.md) made the incoherence official.** The user's *drawings* — deliberate analysis artifacts carrying far more meaning than a click — now mirror to the sidecar ungated. Guarding a bar click while resistance lines stream freely is not a privacy posture; it is a leftover.
- **The channel was always pull-only.** Nothing is pushed into the conversation: the agent sees gestures only when it calls `get_pending_ui_events` (or reads the resource), which in practice happens because the user just asked it something. Consent is effectively re-granted per conversation turn; the toggle guards against a scenario — silent ambient broadcast — the transport cannot produce.
- **The vocabulary is deliberate gestures, not telemetry.** Pan/zoom never crossed the wire. `ui.range_selected` fires only in select-range mode (its own explicit toolbar switch). The single ambient event is `ui.bar_clicked`.
- **The gate already needed a carve-out to not break the product.** The alert scheduler appends to the same buffer *bypassing* the gate (`alerts/scheduler.py` — "what fired while I was away must survive the toggle being off"). A consent gate that load-bearing paths route around has stopped meaning consent.
- **The threat model is a single-user desktop app** on a localhost sidecar, where "the agent" is the user's own Claude Code session; the renderer bearer on `POST /ui_events` (ADR-0002/0014) is the boundary that actually defends anything.

## Decision

We **remove agent mode entirely**. Concretely:

- **Renderer:** the header toggle (`AgentModeToggle`), the `useAgentMode` hook, and every `agentMode` conditional go. Gesture forwarding is unconditional: `ui.bar_clicked` on a chart click, `ui.range_selected` on a select-range drag. **Select-range mode stays** — it is a gesture-mode switch (drag means select, not pan), not a consent switch, and it remains the renderer-local toggle it already is.
- **Sidecar:** the `GET`/`PUT /agent_mode` routes, the persisted `agent_mode.json` state, and the 403 gate on `POST /ui_events` are deleted. The route keeps its renderer-bearer auth unchanged. The `ui.agent_mode_toggled v1` event leaves the vocabulary (nothing can toggle). Any `agent_mode.json` left in the data dir is best-effort deleted at startup and dropped from the data-dir docs.
- **What stands from ADR-0021:** the bounded drop-oldest buffer, `get_pending_ui_events` (drain/peek), the `ui-events://recent` resource + best-effort update notifications, the envelope shape, and the dual-bearer split. This ADR amends ADR-0021's *gate*; its *transport* remains the decided mechanism.
- **[ADR-0099](0099-user-drawing-readback-and-advisory-positions.md)'s consent split collapses to "ungated":** `ui.drawing_changed v1` forwards unconditionally like every other gesture. Plan 0104's phase 4 is amended accordingly before it starts.
- **Single-instance viewer enforcement stays.** ADR-0021 justified it via agent-mode being single sidecar-resident state; that rationale moves, but the behavior is now load-bearing on its own (one live viewer is assumed by the sticky display stores and event semantics). Reverting it would be a separate decision.

## Consequences

### Positive

- **One less mode.** The user never has to remember a toggle to make "look at my chart" work; the 0097 dock and 0104 read-back land on a simpler gesture machine (no agent-mode/drawing-mode interaction to reason about). A real declutter in the Plan 0096 spirit: a component, a hook, two routes, a state file, an event type, and a prop threaded through five files all go.
- **The consent story becomes coherent**: everything the renderer tells the sidecar is display/gesture state, always; the agent reads it only when asked. No half-gated surface to explain.
- **The alert-path carve-out stops being a carve-out** — there is no gate to bypass.

### Negative

- **Every bar click now lands in the buffer.** Ordinary chart inspection produces `ui.bar_clicked` noise the agent may read later ("what was the user looking at?" leaks a little). Accepted: cap-100 drop-oldest, pull-only, single user — and the click event *is* the "ask about this bar" affordance, so gating it away would break its purpose.
- **Re-gating requires a decision if the surface ever leaves the machine.** The [ADR-0073](0073-execution-engine-topology-control-plane-data-plane.md) tunnel future (or any remote MCP exposure) must re-examine consent; re-adding a gate is a route check, but *remembering* to is on that future plan. This ADR's removal is scoped to the local-desktop topology.
- **A user-visible behavior change:** gestures forward by default. The single user requested exactly this; there is no second user to surprise.

### Neutral

- `EXPECTED_FULL_TOOLSET` is unchanged — agent mode had routes and state, no MCP tool. `get_pending_ui_events`' docstring loses its "when agent mode is on" framing.
- The API reference shrinks by two routes and one event type (regenerated per [ADR-0064](0064-generated-sidecar-api-reference.md)).

## Alternatives considered

### Alternative A — Keep the gate as-is

Status quo. Rejected: incoherent after ADR-0099 (drawings ungated, clicks gated), already breached by the alert path, and it defends against a broadcast the pull-only transport cannot perform.

### Alternative B — Keep the machinery, flip the default to ON

Smallest diff; the toggle survives for users who want it. Rejected: worst of both — all the code and UI surface stay, the consent story stays incoherent, and a toggle nobody turns off is dead weight wearing a control's clothing.

### Alternative C — Gate only `ui.bar_clicked` on select-range mode

Remove the mode but forward clicks only while select-range is active, killing the click noise. Rejected: "click a bar, ask about it" is the event's whole purpose and must work in the default gesture mode; entering a drag mode to make a click meaningful is a UX regression, and the noise it avoids is already bounded and pull-only.

### Alternative D — Fully supersede ADR-0021

Mark ADR-0021 superseded and restate the transport here. Rejected: the buffer/tool/resource design is untouched and load-bearing (alerts, Plan 0099's position alerts, Plan 0104's drawing events all ride it); superseding would misstate what changed. This ADR amends the gate clause only.

## Notes

- The paired [Plan 0106](../plans/0106-remove-agent-mode.md) implements the removal: `dev` (sidecar routes/state/gate/vocabulary + apiref) then `ui-builder` (toggle/hook/prop-threading/i18n) then `human` smoke. It should run **before Plan 0097** — the dock's gesture-coordination work is simpler against the post-removal machine — giving the chart-chain order 0096 → 0105 (chart legibility, a parallel-session sibling) → 0106 → 0097 → 0104 → 0098.
- The reusable rule: **a consent gate earns its keep only against push surfaces.** Pull-based reads the user's own conversation triggers do not need a mode; if a future channel actually pushes content at the agent unprompted, gate *that*.
