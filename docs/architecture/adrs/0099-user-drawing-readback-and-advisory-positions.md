# ADR-0099 — User-drawing read-back mirror + advisory position drawings

> **Status:** proposed (Plan 0104 accepts at close)
> **Date:** 2026-07-14
> **Related plan(s):** [0104-drawing-readback-and-position-tools](../plans/0104-drawing-readback-and-position-tools.md) (accepts this ADR at close)
> **Related ADRs:** [ADR-0091](0091-chart-annotation-layer.md) (the two-source annotation layer — **refined here**: its "user drawings never cross the wire" clause gains a read-only-mirror carve-out, and its "renderer→agent read channel is a separate decision" open question is decided), [ADR-0021](0021-renderer-to-agent-feedback.md) (the renderer→agent feedback machinery — the `ui_events` buffer, `get_pending_ui_events`, and the agent-mode consent gate this reuses), [ADR-0029](0029-advisory-recommendation-boundary.md) (the advisory boundary — **extended here** to the drawn form of a recommendation), [ADR-0015](0015-claude-code-primary-control-surface.md) (agent owns analysis), [ADR-0077](0077-user-originated-display-overlays.md) (the display-vs-control classification test), [ADR-0002](0002-ipc-local-http.md) / [ADR-0014](0014-mcp-as-second-sidecar-protocol.md) (the dual-bearer transports the new route/tool ride)

## Context

[Plan 0097](../plans/0097-chart-drawing-dock.md) / [ADR-0091](0091-chart-annotation-layer.md) give the user a drawing dock (trendline/ray, h/v-line, rect, fib) and the agent a write path (`annotate_chart` → `chart.annotations v1`). Two asks remain, both explicitly out of ADR-0091's scope:

1. **The agent cannot read the user's drawings.** The user draws a resistance line and wants to ask "what do you think about this level?" — but user drawings live only in `localStorage['ma.userDrawings']` and never reach the sidecar. ADR-0091 deferred this deliberately: "a renderer→agent read channel, if ever wanted, is a separate decision." This ADR is that decision.
2. **No trading-idea drawing kinds.** TradingView's long/short position tool (entry/stop/target box with risk-reward) and its date/price range measures have no `DrawingSpec` equivalent. The position kinds carry a new boundary problem: an *agent-placed* entry/stop/target box is a directional recommendation in visual form, and [ADR-0029](0029-advisory-recommendation-boundary.md) reserves recommendations for the labeled advisory layer.

Forces:

- **Ownership must not flip.** ADR-0091 (unimplemented but approved) makes the renderer the owner of user drawings. Reversing that before it ships — moving drawings into SQLite so the agent can read them — would re-litigate a settled decision and turn every endpoint drag into a network write.
- **The read must answer "the line I drew yesterday."** ADR-0021's `ui_events` buffer is ephemeral and drop-oldest — fine for gestures, wrong as the *only* channel for durable artifacts the user wants to discuss later.
- **Consent posture.** ADR-0021 gates gesture broadcast behind an explicit agent-mode toggle because ambient pan/click surveillance was the concern. Drawings are different: deliberate, persisted artifacts, and the read is **pull-based** — the agent sees them only when a tool call (triggered by the user's own conversation) asks.

## Decision

Four coupled decisions, one layer:

**1. Read-back is a renderer→sidecar mirror; the renderer stays the source of truth.** The sidecar holds an **in-memory, per-symbol mirror** of the user drawing set. A new renderer-bearer route **`PUT /user_drawings/{symbol}`** accepts a declarative replace of that symbol's user drawings (`provenance: "user"` enforced); the renderer calls it on every drawing mutation and on chart load. A new MCP tool **`get_chart_drawings(symbol)`** returns the mirrored set plus a `synced_at` timestamp — `null` when nothing has synced since sidecar boot, so "no viewer running" is honestly distinct from "no drawings". The mirror is ephemeral (cleared on sidecar restart, repopulated on next renderer sync), needs no migration, and carries no authority: `localStorage` remains the single write-owner, exactly as ADR-0091 decided. This **refines ADR-0091's "user drawings never cross the wire"** to: user drawings never cross the wire *as control or authority*; a read-only display mirror crosses for agent consumption.

**2. The mirror is not agent-mode gated; the push event is.** The sync route accepts regardless of agent mode: the ADR-0021 gate protects against *silent ambient broadcast*, whereas `get_chart_drawings` is a pull the user's own conversation triggers. The *push* half — a new **`ui.drawing_changed v1`** envelope (symbol, change kind, drawing id/kind) appended to the existing ADR-0021 `ui_events` buffer via `POST /ui_events` — keeps the full agent-mode gate (403 when off), because an unprompted "the user just drew something" nudge is exactly the ambient-broadcast class the toggle exists for. Both ADR-0021 read affordances (`get_pending_ui_events`, the `ui-events://recent` resource) serve it unchanged.

**3. Five new `DrawingSpec` kinds.** `long_position`, `short_position` (one anchor at `(time, entry)` plus `stop` and `target` prices; risk-reward is derived at render, never stored), and `date_range`, `price_range`, `date_price_range` (two anchors; bar-count/Δt/Δprice/% readouts derived at render). Same shape, same render/merge/edit path, same per-symbol persistence as ADR-0091's six kinds — the vocabulary grows, the mechanism doesn't.

**4. Agent-placed position drawings are advisory-only, structurally enforced.** An agent-placed `long_position`/`short_position` is a recommendation made visual, so it carries [ADR-0029](0029-advisory-recommendation-boundary.md)'s obligations into the drawn form: **`annotate_chart` rejects a position-kind spec whose `rationale` or `basis` is missing or empty** (typed error, never a silent accept), and the renderer renders agent-placed positions with an explicit advisory label plus the rationale on hover. The sidecar cannot distinguish *which* skill called (one MCP bearer), so enforcement is structural (mandatory labeling at the tool boundary) plus charter (the `advisor` skill is the sanctioned author; analyst skills' charters already forbid recommendations) — the same posture ADR-0029 itself takes. User-drawn positions are private notes and carry no such requirement. Lines, zones, fibs, and ranges remain plain annotations any agent flow may place.

## Consequences

### Positive

- **Closes the loop in both directions.** The user's drawings become conversational objects ("what about this resistance?" now has a grounded answer), and the advisor's calls become visual objects (an entry/stop/target box with its rationale attached).
- **ADR-0091 survives intact.** Ownership, persistence, provenance-scoped editing — unchanged. The mirror is a shadow, not a second source of truth; there is no write path from sidecar to `ma.userDrawings`.
- **Migration-free and determinism-free.** The mirror is in-memory display state; nothing enters the financially-meaningful path. `ui.drawing_changed` rides an existing buffer, existing routes, existing tools.
- **The ADR-0029 boundary extends coherently.** A recommendation is labeled and rationale-bearing whether it arrives as prose (`recommend`) or as geometry (a position box). Future "can the agent draw X?" questions have a test: does X assert a direction? Then it must carry the advisory label.

### Negative

- **User drawings now cross the wire** — to the local sidecar only, but ADR-0091's cleanest privacy line ("never") softens to "read-only mirror, yes". A user who drew assuming total renderer-locality now has their drawings visible to any MCP-bearer holder. Accepted: the user explicitly asked for agent read-back, and the sync is to the same localhost process that already receives their symbol/timeframe choices.
- **Staleness.** With the viewer closed, `get_chart_drawings` serves the last synced state (or nothing after a sidecar restart). `synced_at` makes this honest rather than wrong, but the agent must read it.
- **More wire surface**: one route, one tool, one ui-event type, five kinds — each a schema to version and test.
- **The renderer edit engine grows again.** Position boxes (three draggable price handles, two fill zones, an R:R label) and range measures are more hit-test/render work on top of Plan 0097's already-large engine.
- **Structural enforcement is not identity enforcement.** A non-advisor flow *can* place a position box if it supplies rationale+basis. We accept this — ADR-0029's own enforcement is charter-plus-labeling, not authentication — but it is a real limit worth naming.

### Neutral

- **The mirror's consent posture is a considered choice, not an oversight**: pull-based reads ungated, push notifications gated. If the user later wants the mirror gated too, that is a one-line route check, not a redesign.
- Agent-placed positions remain ephemeral (re-pushed, not persisted) and hide-only, exactly like every other agent annotation under ADR-0091.

## Alternatives considered

### Alternative A — Sidecar-owned drawing store (SQLite), renderer as client

Authoritative sidecar persistence; the agent reads the source of truth directly, drawings survive renderer data resets. Rejected: reverses ADR-0091's renderer-owned decision before it is even implemented, adds a migration and a chatty write path (every endpoint drag becomes an HTTP call), and buys authority the use case doesn't need — the agent needs to *see* drawings, not own them.

### Alternative B — Event-only read-back (no mirror)

Drawings reach the agent solely as `ui.drawing_changed` events through the ADR-0021 buffer. Rejected: the buffer is ephemeral and drop-oldest, so "what do you think about the line I drew yesterday?" fails — the agent could never enumerate the current set. Kept as the *push* half only.

### Alternative C — Gate the mirror behind agent mode

Apply ADR-0021's toggle to the sync route, so drawings reach the sidecar only with agent mode on. Rejected: the gate exists for ambient gesture broadcast; a pull-based tool read prompted by the user's own question is not that. Gating would make the headline ask ("look at my resistance line") fail by default with a confusing remedy ("flip agent mode, redraw or reload, ask again").

### Alternative D — Position boxes as plain annotation kinds

Let any agent call place a position box like a trendline. Rejected: a bare entry/stop/target box is a buy/sell call in disguise — the exact thing [ADR-0096](0096-screening-quality-rank-conditions-side.md) refused for grades — and would let condition-side flows breach ADR-0029 by drawing instead of saying.

### Alternative E — Position tools user-only

Users draw positions; the agent never places them (it may still comment via the read channel). Cleanest boundary, rejected by the user's explicit choice: the advisor should be able to show its recommendation as the box, not force the user to transcribe prose into a drawing by hand.

## Notes

- The paired [Plan 0104](../plans/0104-drawing-readback-and-position-tools.md) implements this. It hard-depends on Plan 0097 (the `DrawingSpec` shape, dock, and edit engine it extends) and serializes after it.
- The reusable artifacts: **(a)** the ADR-0091 companion rule — *user-authored display state may be mirrored (read-only, ephemeral, non-authoritative) to the sidecar when the agent needs to see it; ownership never moves*; **(b)** the ADR-0029 extension — *a directional drawing is a recommendation and must carry the advisory label + rationale at the tool boundary, whatever the medium*.
- `get_chart_drawings` returns geometry plus `synced_at`; interpretation (is this resistance credible?) stays with the agent's existing analysis tools — the read channel carries facts, not judgments.
