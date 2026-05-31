# ADR-0032 — The data layer must not depend on the api layer

> **Status:** proposed
> **Date:** 2026-05-31
> **Related plan(s):** [0028-data-layer-boundary-hardening](../plans/0028-data-layer-boundary-hardening.md)

## Context

The project's layering runs one direction: `api/` (FastAPI routes + MCP tools) depends on `data/` (providers + adapters), persistence, backtest, analysis, strategies. Downstream layers do not know the api exists. CLAUDE.md states this, but the rule has never been recorded as a decision with a rationale — so the one place it was broken was broken on purpose, with a docstring rather than an objection.

`data/backfill.py:32` imports `EventBus` and three payload models from `market_analyser.api.events`:

```python
from market_analyser.api.events import (
    EventBus, GapWindow, OhlcvBackfilledPayloadV1,
    OhlcvBackfillFailedPayloadV1, OhlcvBackfillStartedPayloadV1,
)
```

The `BackfillCoordinator` was designed (Plan 0013) to publish backfill-progress events directly, so it reached up into the api layer for the bus. The module docstring (`backfill.py:17-20`) acknowledges the `data→api` direction and confines it to the one file.

This is **not** a runtime import cycle: `api/events/__init__.py` imports only the standard library and pydantic — it is a leaf with no back-edge into `data/`. So the cost today is structural, not functional: `data/` can no longer be imported in isolation (importing it drags in `api.events`), and the layering rule now has a documented exception that the next contributor can cite as precedent. The May audit independently re-flagged the same reach, which is the signal that "documented one-off exception" is not stable — it invites recurrence.

The forces: we want `data/` to stay independently importable and testable (a data-layer change should not require the api package to load), and we want the event/pub-sub abstraction to be available to any layer that produces progress events without inverting the dependency arrow.

## Decision

We will record the layering rule as a decision — **`data/` (and every other downstream layer) must not import from `api/`** — and resolve the existing violation by relocating the event abstraction to a layer-neutral core package that both layers depend on.

> The `EventBus` class and the typed envelope/payload models move from `api/events/` to a new top-level `market_analyser/events/` package. `api/` re-exports them (or imports from the new location) for its routes/SSE plumbing; `data/backfill.py` imports the bus and payloads from `market_analyser.events` instead of `market_analyser.api.events`. The dependency arrow then points `data → events` and `api → events`, never `data → api`.

`events/` is a leaf (stdlib + pydantic only, exactly as `api/events` is today), so nothing downstream gains an api dependency. The api package keeps owning the *HTTP/SSE wiring* (the route that streams the bus to the renderer); only the bus + schema, which are transport-neutral, move down.

## Consequences

### Positive
- `data/` is independently importable again — no api package load to touch the backfill coordinator or its tests.
- The layering rule is now a citable decision, not a CLAUDE.md sentence; the next "just import the bus from api" reach has an ADR to bounce off.
- Any future producer of progress events (a backtest progress stream, an analysis job) has a neutral home for the bus instead of repeating the `data→api` reach.

### Negative
- A move-and-re-export commit touches import sites across `api/` (every current `from market_analyser.api.events import …`) plus `data/backfill.py` and their tests. It is pure relocation with no behavior change, but it is broad and must keep the SSE event-schema parity guard (`desktop/renderer/types/events.test.ts`, which dumps the pydantic schemas) green.
- One more top-level package. Minor; `events/` is small and cohesive.

### Neutral
- The renderer-facing event schema is unchanged — same models, same field names, same versions — so the codegen/parity guards should pass without renderer edits. (If any test imports the models by their old `api.events` path, it updates to the new path; that is a test-only churn, not a contract change.)

## Alternatives considered

### Alternative A — Leave it; it's only a directional smell with no cycle
The standing call (plans README follow-up, from Plan 0013) was "no actual cycle, promote to a plan only if the pattern recurs." It recurred in the audit. Keeping it means the documented exception stands as precedent and `data/` stays non-isolable. Rejected now that the reach has been independently re-flagged — the cheap fix removes the precedent permanently.

### Alternative B — Invert via a callback/Protocol injected from api into data
Define a `SupportsPublish` Protocol in `data/` and have `api/` inject a concrete bus at construction, so `data/` depends only on its own abstraction. This is a clean inversion, but it is more machinery than the problem warrants: the bus + payloads are already transport-neutral pydantic, so relocating them is simpler than introducing an injection seam, and it gives every layer (not just data) a shared event home. Rejected as over-engineered for a leaf relocation.

## Notes

Pairs with [ADR-0031](0031-data-source-adapter-contract.md); both land in Plan 0028's data-layer-hygiene pass. Supersedes the informal guidance in the plans-README follow-up carried from Plan 0013.
