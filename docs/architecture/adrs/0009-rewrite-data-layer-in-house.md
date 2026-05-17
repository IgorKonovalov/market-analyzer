# ADR-0009 — Drop the vendored upstream; rewrite the data layer in-house

> **Status:** accepted
> **Date:** 2026-05-17
> **Related plan(s):** [0003-excise-vendored-upstream](../plans/0003-excise-vendored-upstream.md)
> **Supersedes:** [ADR-0003](0003-vendoring-strategy.md)

## Context

[ADR-0003](0003-vendoring-strategy.md) — accepted earlier on 2026-05-17 — committed us to vendor the data layer from an upstream companion repository (referred to here as `tradingview-mcp`, held by the author in a separate local checkout). The reasoning then: reuse ~6 kLOC of working data-layer code, own only the adapter wrappers, pull upstream fixes via a planned drift-check script.

Two facts have since changed that reasoning:

1. **The companion repository will be deleted** once `market-analyser` is complete. There is no upstream to pull fixes from and no expected divergence to police. The drift-check script ADR-0003 deferred to a followup becomes pointless work — it would compare our tree against a non-existent reference.
2. **The vendored carve-out we actually use is tiny.** Three files (~250 LOC total) under `src/market_analyser/data/vendored/tradingview_mcp/core/services/`, of which one function — `_fetch_ohlcv` (~40 lines of urllib + JSON parsing against Yahoo's Chart API) — is the only thing called by our adapter. The proxy helper is opt-in and dormant by default; the Yahoo quote functions are vendored but unused. ADR-0003's "battle-tested code" argument is doing very little work in practice.

A vendoring discipline pays off when the upstream is alive, divergence is a real risk, and the volume is large enough that copy-with-discipline beats rewrite-from-scratch. None of those conditions hold here. Continuing the policy keeps a `Vendored from ...` header in our source, a `vendored.lock` file, an unused `data/vendored/` package path, and a constant cognitive pull on contributors to think about a repository that will not exist by the time this app ships.

## Decision

We will **rewrite** the data layer in-house. The companion `tradingview-mcp` repository is permitted only as a **read-only reference for ideas and rough code structure** — its source is not copied, imported, or depended upon at runtime, and it carries no build- or test-time link to this project. After this ADR, `tradingview-mcp` is named only in this document and in the (superseded) [ADR-0003](0003-vendoring-strategy.md); every other location — source code, comments, file headers, plans, diagrams, skill SKILL.md / references, `CLAUDE.md`, the `vendored.lock` file itself — has the references removed. Execution is tracked in [Plan 0003](../plans/0003-excise-vendored-upstream.md).

[ADR-0007](0007-market-data-provider.md) — the `MarketDataProvider` Protocol with per-source adapters — remains substantively correct. Where its prose says adapters "wrap vendored services" or "the vendored data layer", read it under this ADR as "wrap our own implementation" / "the data layer". The Protocol shape, the `as_of` seam, the cache chokepoint, and the lazy bring-in cadence are all unchanged.

## Consequences

### Positive
- **Zero external project dependency.** The repo stands alone. When the companion repository is deleted, nothing here breaks.
- **Less ceremony per file.** No provenance headers, no `vendored.lock`, no SHA pinning, no drift-check script, no edit-allowlist. New code is just our code.
- **The dual-tree import footgun disappears.** ADR-0003 §Negative warned that contributors could accidentally import the sibling on disk (`tradingview_mcp.*`) instead of our copy (`market_analyser.data.vendored.tradingview_mcp.*`). With one tree, there is one import path.
- **No third-party license to carry.** ADR-0003 mandated bundling the upstream MIT `LICENSE`. With no vendored code, no license file to ship.
- **Skill descriptions get simpler.** Cross-repo cognitive load on every skill goes away. New contributors do not need a mental model of a second project.

### Negative
- **We lose the upstream-fix path.** If Yahoo or TradingView changes their API after a fix lands somewhere we could have copied, we discover the breakage ourselves rather than cherry-picking. Mitigation: the surfaces we actually use (Yahoo Chart API for OHLCV is the only one today) are stable and small.
- **Rewrite cost, paid now.** Plan 0003 has to rewrite `_fetch_ohlcv` and decide what to do with the proxy helper before the vendored tree can be deleted. Estimate: small (one short urllib fetcher plus tests).
- **Battle-testing reset.** ADR-0003's "the existing vendored implementations are battle-tested" protection is gone. We re-discover Yahoo's edge cases (None fields, partial bars, weekend gaps) on our own. Mitigation: validate every parsed row through the existing `Bar` pydantic model at the adapter boundary — the validation layer was already in place.
- **Reversal is costly.** If we later wish we had kept vendoring, restoring the discipline means re-introducing the headers, lock file, and drift script — and possibly re-vendoring from a different upstream by then. We accept this because the upstream is going away.

### Neutral
- [ADR-0003](0003-vendoring-strategy.md) stays in the tree, marked `superseded by ADR-0009`. ADRs are append-only; we do not delete it. Its historical context remains readable.
- [ADR-0007](0007-market-data-provider.md) stays as written; the substance is unchanged. We do not rewrite its prose — readers wanting the current wording look here.

## Alternatives considered

### Alternative A — Continue vendoring as ADR-0003 specifies
Keep the `data/vendored/` tree, keep `vendored.lock`, write the drift-check script. Rejected because the upstream is being deleted: every cost ADR-0003 accepted (drift policing, header maintenance, dual-tree import discipline) buys nothing once there is no upstream to drift against. The decision in ADR-0003 was right for its context; the context has changed.

### Alternative B — Library dependency on a frozen `tradingview-mcp` SHA
Pin a Git-source dependency to the current SHA before the companion repo is deleted, then carry that dep forward. Rejected because (1) a Git-source dep tied to a soon-to-be-deleted repository is operationally fragile — `pip install` against a deleted Git URL becomes a future blocker — and (2) we already rejected this in ADR-0003 §Alternative B for reasons that still apply (no semver, no changelog discipline).

### Alternative C — Vendor a frozen snapshot, no drift script
Keep the current vendored tree, drop the drift policy, never update it. Rejected because the provenance headers and `vendored.lock` still carry contributor cost ("what is this? do I edit it?") for code that, post-deletion of the companion repo, is just our code with confusing labels. The "vendored" framing only makes sense when the upstream is real; otherwise it is dead ceremony.

## Notes

- The companion repository is permitted to be opened in another editor window during a rewrite for design inspiration. It is not permitted to be imported, copied, or named in any file in this project other than this ADR and the (superseded) [ADR-0003](0003-vendoring-strategy.md).
- Plan 0003's final phase enforces this by `grep` over the repository — matches outside the two grandfathered ADRs and the plan file itself are a blocker.
- This ADR does not change the `MarketDataProvider` shape, the `as_of` discipline, the strategy interface, the persistence layout, the IPC contract, the Electron shell conventions, or per-skill ownership boundaries. It is exclusively a sourcing decision.
