# ADR-0065 — Neutral UI-event buffer core (domain loops must not import the api layer)

> **Status:** proposed
> **Date:** 2026-07-09
> **Related plan(s):** 0072-codebase-remediation-audit-2026-07
> **Related ADRs:** applies the neutral-core precedent of [0032](0032-data-layer-no-api-dependency.md) (the event **bus** was moved out of `api` for exactly this reason) to the renderer→agent feedback **buffer** of [0021](0021-renderer-to-agent-feedback.md); the buffer feeds the in-sidecar scheduler of [0055](0055-in-sidecar-watch-scheduler.md)

## Context

The renderer→agent feedback buffer ([ADR-0021](0021-renderer-to-agent-feedback.md)) — `UIEventBuffer` plus its `UIEventEnvelope` type — lives under `src/market_analyser/api/ui_events/`. It is the poll seam: domain code appends events, the api layer serves them to the agent via `GET/POST /ui_events`.

The in-sidecar watch scheduler ([ADR-0055](0055-in-sidecar-watch-scheduler.md)) is a **domain background loop** under `src/market_analyser/alerts/`. It imports the buffer directly:

```python
# src/market_analyser/alerts/scheduler.py:45-46
from market_analyser.api.ui_events import UIEventEnvelope
from market_analyser.api.ui_events.buffer import UIEventBuffer
```

That is a domain package depending **up** into the `api` transport/composition layer — the same inversion [ADR-0032](0032-data-layer-no-api-dependency.md) removed when it lifted the SSE event bus into the neutral top-level `events/` core so the data layer would not have to import `api`. The buffer is the same shape of shared primitive: **produced** by domain loops (the scheduler today; DeFi scan/PnL jobs and backfill are candidates tomorrow), **consumed** by the `api` routes. Every future domain publisher that wants to emit an agent-pollable event is currently forced to import from `api`.

The force: keep the dependency graph pointing one way (domain → neutral core, api → neutral core), so a background loop can run without dragging the web layer in behind it.

## Decision

We will move `UIEventBuffer` and `UIEventEnvelope` out of `api/ui_events/` into a **neutral top-level module `src/market_analyser/ui_events/`**, a sibling to `events/`, depended on **downward** by both the `api` routes and the domain loops. The transport-specific pieces that legitimately belong to the web layer — the `/ui_events` route and the agent-mode toggle (`agent_mode.py`) — stay under `api/`. `alerts/scheduler.py` (and any future domain publisher) imports the buffer and envelope from the neutral module; **no domain package imports `market_analyser.api` for this.**

`api/ui_events/` may keep a thin re-export of the moved symbols for one transition so the wide import diff can land incrementally, but the invariant the plan asserts is a static check: nothing under `alerts/`, `defi/`, `data/`, `analysis/` imports `market_analyser.api.*`.

## Consequences

- **Positive — the layer graph is acyclic in the intended direction.** Domain → `ui_events/` core, `api` → `ui_events/` core. Matches the ADR-0032 invariant that already holds for the SSE bus; the two "events" homes now have parallel, principled placement.
- **Positive — future domain publishers import a neutral module,** not the FastAPI layer. A DeFi job that wants to emit an agent-pollable event no longer pulls `api` (and transitively FastAPI) into a background task.
- **Positive — the scheduler is reusable and testable without the web app.**
- **Negative — a module move touches imports across `api`, `alerts`, `apiref`, and their tests** — a mechanical but wide diff. Mitigated by the optional transition-window re-export in `api.ui_events`.
- **Negative — two "events" homes** (`events/` push bus for SSE, `ui_events/` poll buffer for the agent). A mild naming overlap, but they are genuinely different transports (push vs poll) with different consumers and lifecycles; keeping them separate is clearer than merging.

## Alternatives considered

- **Keep it in `api/` and accept the inversion.** Rejected: it is the exact coupling ADR-0032 exists to prevent, and the scheduler is domain code that should not need the web layer to run.
- **Inject the buffer as a `Protocol` without moving the type.** Rejected: the concrete `UIEventEnvelope` type still has to be imported somewhere; a Protocol hides the buffer interface but not the envelope, and it is more machinery than a straight move.
- **Fold the buffer into the existing `events/` core.** Rejected (for now): the SSE bus and the poll buffer have different lifecycles and consumers; merging them conflates two transports under one name. A neutral sibling keeps them separable; a later ADR can merge them if the distinction stops paying rent.
