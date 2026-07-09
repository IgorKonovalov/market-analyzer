# 0064 — Generated sidecar API reference from live introspection, CI-gated

> **Status:** accepted (Plan 0070 close 2026-07-09)
> **Date:** 2026-07-09
> **Related plan:** [0070-sidecar-api-reference-generator](../plans/0070-sidecar-api-reference-generator.md)
> **Related ADRs:** [0014](0014-mcp-as-second-sidecar-protocol.md) (MCP surface), [0017](0017-live-ui-updates-via-sse.md) (SSE events), [0046](0046-mcp-large-result-delivery.md) (why we don't ship full schemas on the wire)

## Status

Accepted at the close of Plan 0070 (2026-07-09). The `src/market_analyser/apiref/` generator ships: introspects the fully-wired MCP registry + FastAPI OpenAPI + event `TYPE_REGISTRY`, renders deterministic markdown under `docs/reference/`, and gates freshness in CI via `apiref --check`.

## Context

The sidecar exposes a large, still-growing contract: ~41 MCP tools at `/mcp`, ~16 REST route groups, and a typed SSE event vocabulary on `/events`. The authoritative description of each surface already exists in code — a tool's `@server.tool(description=...)` string (sometimes a rich constant like `FORECAST_DESCRIPTION`), the JSON schema FastMCP derives from the tool function's signature + Pydantic models, the FastAPI route signatures and their `response_model`s, and the SSE envelope payload models. But none of it is browsable as documentation: to learn what `derivatives_snapshot` accepts and returns, a human must open its module and read the source.

The prose that does exist is scattered and partial: `README.md` names "41 tools" in one paragraph and lists REST routes only inside a mermaid diagram; `docs/onboarding/claude-code-setup.md` deep-dives exactly two tools; the ADRs record *why* decisions were made, not *what each endpoint does*. There is no per-command catalog.

A hand-written catalog is the obvious fix and the wrong one: the moment a tool's description or a Pydantic field changes, a hand-maintained copy drifts, and a drifted API reference is worse than none — it lies with authority. The repo already faced this exact problem for the renderer's TypeScript types and solved it with generation + a CI drift check (`pnpm gen-types` / `gen-types:check`). The same discipline applies here. The open question is not *whether* to generate but *how to read the surface*: introspect the live, fully-wired server, or statically parse the source.

One wrinkle shapes the decision: MCP tools register **conditionally** on which dependencies `create_mcp_components` receives (the DeFi tools appear only when a wallet source is wired, the metric-series tools only when persistence is wired, etc.). Any generator that wants to see all 41 tools must construct a fully-wired server — which the test suite already does, in `tests/api/test_mcp_tools.py`'s `EXPECTED_FULL_TOOLSET` assertion against a fully-wired `create_mcp_components`.

## Decision

We generate the sidecar API reference by **introspecting the live, fully-wired application** and gate its freshness in CI.

A Python generator constructs the real `FastMCP` server (via a fully-wired `create_mcp_components`), the real FastAPI app, and the SSE envelope registry, then reads their own computed metadata: the MCP tool registry (names, descriptions, input JSON schemas, and output shapes), the FastAPI OpenAPI document (routes, params, request/response schemas), and the registered event kinds with their payload schemas. It renders this into deterministic, human-readable markdown under `docs/reference/` — one full-detail entry per tool / route / event (name, summary, description, a parameters table, the return/payload shape, and a source-file link). The committed markdown is kept honest by a `--check` mode that re-renders in memory and fails on any diff, wired into CI exactly like `gen-types:check`. The descriptions and schemas in the doc are therefore *the same objects the agent and clients see at runtime* — the doc cannot drift from behavior without turning CI red.

We rejected **static AST/docstring parsing** because it re-derives by hand the JSON schemas FastMCP and Pydantic already compute, and is fragile against descriptions built dynamically at import time (e.g. `FORECAST_DESCRIPTION`), producing a second, lower-fidelity model of the surface that can itself drift from the registered reality. We rejected **relying on FastAPI's built-in Swagger UI (`/docs`)** because it covers only the REST surface (not MCP tools or SSE events), is a live endpoint rather than a versioned, greppable, review-diffable in-repo artifact, and requires a running sidecar to consult. We rejected **a hand-written catalog** because it rots on the first unmirrored field change, and **generation without a CI gate** because an ungated generated file drifts silently the first time someone edits a tool and forgets to regenerate — the same failure the type-mirror gate exists to prevent.

## Consequences

Positive:

- **The reference cannot lie.** It is rendered from the same registry/OpenAPI/envelope objects the runtime uses; a stale doc is a red CI check, not a latent trap.
- **Zero schema re-derivation.** Parameter types, defaults, required-ness, and return shapes come straight from the JSON schemas Pydantic/FastMCP already produce.
- **Reuses existing wiring.** The fully-wired construction needed to see all 41 tools already exists in the test suite; the generator shares that single source of truth rather than inventing a second one.
- **Browsable and greppable.** A committed markdown tree under `docs/reference/` diffs in PRs, is searchable in-repo, and needs no running sidecar to read.

Negative (the price we pay):

- **The generator must build a fully-wired app**, which is heavier than parsing source files and couples the generator to the app's construction signature — when `create_mcp_components` gains a dependency, the generator's wiring must keep up (mitigated by sharing the test suite's wiring helper, so one change updates both).
- **Fidelity is bounded by the schemas and docstrings.** A tool with a thin description or an under-annotated return type produces a thin entry; the generator surfaces what's there, it doesn't author prose. This is a forcing function for better in-code descriptions, but it means the doc's quality tracks the code's.
- **One more CI gate to keep green.** Every change to a tool description, a Pydantic field, a route, or an event payload now also requires a regenerate-and-commit step, or CI reddens. This is the intended cost — it is what makes the doc trustworthy — but it is friction on surface-changing PRs.
- **Deterministic output is a constraint on the generator**, not a freebie: tool ordering, schema key ordering, and the absence of any wall-clock/host-specific content must be enforced so `--check` is stable across machines and runs (consistent with the repo's determinism culture).

## Alternatives considered

- **Static AST / docstring parsing of `mcp_tools/` + `routes/`.** No need to build a server, but re-derives schemas by hand and breaks on dynamically-constructed descriptions — a lower-fidelity second model of the surface. Rejected.
- **FastAPI Swagger/OpenAPI UI (`/docs`) only.** Covers REST but not MCP tools or SSE events; a live endpoint, not a versioned in-repo artifact; needs a running sidecar. Rejected as insufficient scope and wrong medium (we still consume its OpenAPI document programmatically for the REST section — just not as *the* deliverable).
- **Hand-written markdown catalog.** Most readable on day one; rots on the first unmirrored change. Rejected.
- **Generate but don't gate.** Drifts silently the first time regeneration is skipped. Rejected in favor of the `gen-types:check`-style hard gate.
