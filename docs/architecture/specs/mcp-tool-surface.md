# Spec — MCP tool surface

> **Subsystem:** The agent-callable MCP tool surface mounted on the sidecar at `/mcp` — its one-verb-per-tool granularity rule and the `EXPECTED_FULL_TOOLSET` budget ledger that governs its growth.
> **Source:** src/market_analyser/api/ (`mcp_app.py` registration, `mcp_tools/` implementations), tests/api/test_mcp_tools.py (`EXPECTED_FULL_TOOLSET`), docs/reference/mcp-tools.md (generated surface)
> **Reconciled-through:** Plan 0112
> **Governing ADRs:** 0104-mcp-tool-surface-granularity, 0014-mcp-server-on-sidecar, 0015-claude-code-primary-control-surface, 0064-generated-sidecar-api-reference

The MCP tool surface is the agent's control plane (ADR-0015). Its coherence — not
its raw byte count — is what a well-factored surface buys: near-duplicate tool
descriptions degrade the agent's routing and discovery. This spec states the
granularity rule that keeps the surface coherent and the ledger that enforces it.

## Invariants

- **One tool per verb.** The surface MUST expose one top-level tool per *operation*.
  A capability that is a new **mode of an existing verb** — the same operation over
  the same inputs, differing by a discriminator — MUST extend that tool through a
  `kind` / `rank_by` / `source`-style parameter and a discriminated result, and MUST
  NOT add a new top-level tool. Only a **genuinely new verb** (new operation, new
  inputs, or a new capability boundary) earns a new top-level tool.  (ADR-0104 Decision)

- **Distinct verbs stay distinct.** The surface MUST keep genuinely-different verbs
  as separate tools even when merging would shrink the count — the per-tool
  description is the agent's primary routing signal, and collapsing unrelated verbs to
  save a number trades a real boundary for a cosmetic win.  (ADR-0104)

- **`EXPECTED_FULL_TOOLSET` is the tool budget ledger.** The set in
  `tests/api/test_mcp_tools.py` MUST enumerate exactly the fully-wired tool surface;
  the registration test asserts the live server's tool names equal it. Adding a name
  is the reviewable moment where a plan must state, in its tool phase, *which verb is
  new* — a name that is really a mode of an existing verb is a Mode 4 blocker, not a
  nit.  (ADR-0104 governance artifact; `tests/api/test_mcp_tools.py`)

- **The generated reference tracks the surface.** The agent-facing tool docs under
  `docs/reference/` MUST be regenerated from the live sidecar when the surface changes;
  drift reddens the apiref `--check` gate. Mechanical surface truth (params, payloads,
  source links) is apiref's job — behavioral intent is this spec's.  (ADR-0064)

- **The human-confirm split is protected.** The execution verbs
  (`prepare_order` / `confirm_order`, if/when built) MUST stay split — the two-tool
  shape is the ADR-0025 human-confirm gate, an explicit capability boundary the
  granularity rule protects, not a redundancy to merge.  (ADR-0104; ADR-0025)

- **Consolidation is a surface refactor, not a behavior change.** Folding a same-verb
  cluster behind one discriminated tool MUST NOT change the underlying computation:
  determinism, anti-lookahead, and the advisory boundary ([advisory-boundary
  spec](advisory-boundary.md)) are unaffected by tool-shape changes.  (ADR-0104 Neutral)

## Scenarios

- WHEN a plan adds a capability that is the same operation over the same inputs as an
  existing tool (e.g. another watchlist ranking, another forecast kind, another
  sentiment source) THEN it becomes a new enum value on that tool's discriminator
  (`scan_watchlist.rank_by`, `forecast.kind`, `sentiment.source`), not a new
  top-level tool.  (ADR-0104 consolidation table)

- WHEN a plan adds a genuinely new verb (e.g. `sector_rotation`'s basket taxonomy,
  `defi_fundamentals`' token fundamentals, `event_calendar`'s calendar) THEN it earns
  one new top-level tool and one new `EXPECTED_FULL_TOOLSET` entry, justified in the
  plan's tool phase.  (ADR-0104 Notes, queued-plan disposition)

- WHEN the live server's tool-name set diverges from `EXPECTED_FULL_TOOLSET` THEN
  `test_full_toolset_registration_is_exhaustive` fails, naming the missing/extra tools.
  (`tests/api/test_mcp_tools.py`)

- WHEN a tool's return annotation is a `Union`/generic type THEN FastMCP wraps it in a
  `{"result": …}` object; a cluster of modes returning different bare models needs a
  purpose-built single-object envelope (as `forecast` uses `ForecastResponse{kind,
  result}`) to preserve each mode's payload.  (ADR-0104 Negative note; Plan 0109)

- WHEN a plan proposes splitting one verb into several near-identical tools to expose
  modes THEN it is redirected to a discriminator on the single verb; when it proposes
  merging two genuinely distinct verbs to cut the count THEN the merge is rejected.
  (ADR-0104 Decision, both directions)

## Known gaps / honest nulls

- **The count is a point-in-time view, not the contract.** The contract is the
  granularity *rule* and the *ledger*, not a fixed integer. The ledger currently
  enumerates 59 tools (post-Plan 0113 `event_calendar`); that number moves with every
  new-verb plan and is authoritative only as the current contents of
  `EXPECTED_FULL_TOOLSET`, which the registration test pins.

- **Mode-vs-verb is architect judgment.** "Is this a new verb or a mode?" is decided
  by ADR-0104's rule at review time, not by a machine check. The `EXPECTED_FULL_TOOLSET`
  test enforces that the ledger matches the live surface; it does not enforce that each
  entry *deserves* to be a distinct verb — that is the Mode 4 judgment call.

- **Deferred loading softens but does not remove the coherence cost.** In Claude Code
  (the primary control surface) MCP tool schemas are deferred, so the raw context cost
  of tool N+1 is near zero until searched. The residual cost the rule targets is
  discovery and routing quality, which a smaller, non-redundant surface improves
  regardless of any one client's loading strategy.  (ADR-0104 Context)
