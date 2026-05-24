# ADR-0003 — Vendor an upstream MCP project as a mirrored subtree, not a dependency

> **Status:** superseded by [ADR-0009](0009-rewrite-data-layer-in-house.md)
> **Date:** 2026-05-17
> **Related plan(s):** [0001-bootstrap](../plans/0001-bootstrap.md)

## Context

`market-analyser` reuses a substantial fraction of the data layer in an upstream MCP companion project — screener, indicators, sentiment, backtest engine, the BTC market pulse. The relationship between the two projects must be locked in early because every downstream design assumes a particular boundary.

Three realistic relationships:

1. **Runtime MCP dependency.** Spin up the upstream MCP project as a separate process and talk to it via MCP.
2. **Library dependency.** Add the upstream MCP project to `pyproject.toml` as a Git-source or PyPI dep and import normally.
3. **Vendor.** Copy the code into `market-analyser`'s tree, take ownership.

Forces:

- **Performance.** MCP is a JSON-RPC protocol over stdio. Round-tripping every screener call through MCP framing adds latency and JSON overhead — fine when an LLM is the caller, wasted when a local Python process is. The data layer should be an in-process call.
- **Evolution.** `market-analyser` has different needs than the MCP server: caching for desktop reuse, possible offline mode, deterministic backtest seeds, the contracts in `src/market_analyser/contracts/`. We will *change* the data layer's surface for desktop use. A dependency would force those changes upstream; vendoring lets us move at our own pace.
- **Determinism.** Backtests must be reproducible (see `best-practices.md`). The fewer moving parts under our version control, the cleaner the reproducibility story. A pinned library dependency is acceptable here; an out-of-process service is not.
- **Upstream improvement flow.** The upstream MCP project is actively maintained. We want the option to pull bug fixes (e.g. the resilience layer added to `screener_provider.py` on 2026-05-13) without manually rewriting them. Pure-copy vendoring makes this awkward; structured vendoring with a drift-check script makes it tolerable.
- **Code drift risk.** Vendored code that diverges silently is the worst of both worlds — we lose upstream fixes *and* we own the maintenance. Discipline is the answer; tooling enforces it.

## Decision

We will **vendor** files from the upstream MCP project into `src/market_analyser/data/vendored/upstream/`, mirroring the upstream directory layout exactly. Each vendored file gets a one-line header recording the upstream commit SHA. We do not edit vendored files casually — only the minimum changes needed to make them compile in the new package path (e.g. rewriting `from upstream.core.types import ...` to the new path). All structural changes (caching wrappers, abstractions, type adaptations) live in non-vendored adapter modules that *import* the vendored code, never edit it.

A `scripts/check-vendor-drift.py` script (followup, not week-one) will diff the vendored tree against a configured upstream checkout and fail CI on unexplained drift. An allowlist file documents intentional local edits with a reason.

We pin the vendoring source to a specific commit SHA, not a moving branch. Upgrading the vendored snapshot is a deliberate plan step, not a passive `git pull`.

## Consequences

### Positive
- **In-process performance.** Direct Python calls, no MCP framing.
- **We control the cadence.** Upstream changes that we want, we cherry-pick; ones that break our contracts, we skip. No surprise breakage.
- **Reproducible backtests are easier.** The whole data layer is pinned to a commit, recorded in our tree. A historical backtest run can be exactly recreated.
- **No new transport to debug.** MCP-over-stdio in production would mean trace-debugging across two languages and a protocol; vendoring keeps everything in one Python process.
- **Clear ownership boundary.** Anything under `src/market_analyser/data/vendored/` is upstream's design; anything outside is ours. Code reviewers know which conventions to apply.

### Negative
- **We own the bugs.** Upstream fixes don't reach us automatically. If a TradingView API change breaks the screener, we have to merge the fix ourselves. The drift-check script makes this discoverable but not automatic.
- **Repo grows.** We bring ~6 kLOC of vendored Python across the data layer. Mitigated by only vendoring what we use this week (see Plan 0001's manifest — one file in the bootstrap, far less than 6k).
- **Easy to "just fix it in place" temptation.** A developer hitting a bug in a vendored file will be tempted to edit there instead of in an adapter. The header comment is the speed bump; the drift script is the enforcement. Without both, this ADR's value erodes within months.
- **No PyPI install of the upstream MCP project solves a problem for us.** If upstream ever publishes to PyPI with a stable API, we should revisit this ADR.
- **Two copies of the same code on the same machine** during development (since the upstream project lives next to us). Confusing the two in imports is a real foot-gun — mitigate with explicit imports from `market_analyser.data.vendored.upstream.*` only, never the upstream package's bare top-level name.

### Neutral
- License compliance: the upstream MCP project is MIT. Include its `LICENSE` file unchanged at the root of our vendored tree (`src/market_analyser/data/vendored/upstream/LICENSE`). This is mechanical, not a real cost.

## Alternatives considered

### Alternative A — Runtime MCP dependency
Run the upstream MCP project as a child process, talk to it via MCP from the Python sidecar. Rejected because (1) it adds a JSON-RPC hop for every data fetch, with no compensating benefit since both sides are in our control, (2) it makes deterministic backtesting harder — the MCP server has its own caches and we can't audit them as easily — and (3) we'd ship the entire MCP server in our installer, doubling the dependency surface.

### Alternative B — Library dependency (PyPI or Git)
Add the upstream MCP project as a normal `[project.dependencies]` entry. Rejected because (1) it doesn't publish to PyPI in a stable cadence we can rely on, (2) a Git-source dep pinned to a SHA gives us none of the benefits of a real library (no semver, no changelog discipline) and all the costs (we still have to merge fixes manually, but without the visibility a vendored diff gives us), and (3) it foreclosed on the local-evolution use case that drove the vendoring decision in the first place.

### Alternative C — Git submodule
Mount the upstream MCP project as a submodule under `src/market_analyser/data/vendored/`. Rejected because submodules are operationally painful (clone, fetch, update flow surprises new contributors), they don't let us cherry-pick — it's all or nothing — and they tie our tree's commit graph to upstream's. The vendor-by-copy approach is the same thing minus the submodule mechanics.

## Notes

- The drift-check script's allowlist format (sketch): one line per intentional edit, `path/to/file.py:reason — see plan/ADR-NNNN`. The script computes a checksum of the upstream file at our pinned SHA, compares to our vendored file, and flags unlisted differences.
- We do not vendor the upstream test suite. Vendored modules are smoke-tested at our integration boundary, not unit-tested in their original form — they're already tested upstream.
- The pinned SHA lives in a top-level file: `vendored.lock` (single line, the commit SHA we last vendored from). Updating it is a tracked plan.
