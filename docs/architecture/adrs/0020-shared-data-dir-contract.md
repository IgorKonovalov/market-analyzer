# ADR-0020 — Shared data directory is contractual, not derived

> **Status:** accepted (2026-05-22 — Plan 0007 close ceremony, after Phase 5 smoke confirmed the contract holds end-to-end)
> **Date:** 2026-05-22
> **Related plan(s):** [0007 — live agent-driven viewer](../plans/done/0007-live-agent-driven-viewer.md) (phase 4.1 implements this ADR; closed 2026-05-22)
> **Related ADRs:** [ADR-0016](0016-standalone-sidecar-mode.md) (lockfile + idempotent attach — this ADR refines the data-dir half), [ADR-0006](0006-persistence-layout.md) (SQLite + config layout — the data dir hosts `app.db` too)

## Context

[ADR-0016](0016-standalone-sidecar-mode.md) anchors the lockfile, the MCP bearer file (`mcp-secret.json`), the renderer bearer (`sidecar.lock`), the SQLite cache (`app.db`), and `config.json` at a single per-user "data directory". It does not say *which* directory, beyond "the OS-appropriate per-user app-data directory". Both halves of the system compute it locally:

- **Python side** — `src/market_analyser/config.py::default_app_data_dir()` returns `%APPDATA%\market-analyser\` on Windows, `~/Library/Application Support/market-analyser` on macOS, `$XDG_DATA_HOME/market-analyser` on Linux. Honours `MARKET_ANALYSER_DATA_DIR` as an override.
- **Electron side** — `desktop/electron/main.ts::resolveDataDir()` returns `app.getPath('userData')`, which under the hood derives from `app.getName()`. In a packaged build, `build.productName = "market-analyser"` (per `desktop/package.json`) propagates and the paths agree. In dev (`pnpm dev` → unpackaged Electron), `app.getName()` falls back to `package.json#name = "@market-analyser/desktop"`, so `userData` becomes `%APPDATA%\@market-analyser\desktop\` — silently divergent from Python.

The Plan 0007 phase 5 smoke surfaced the consequence: a manually-started sidecar writes `sidecar.lock` and `mcp-secret.json` to `%APPDATA%\market-analyser\` (Python default); Electron in dev mode reads from `%APPDATA%\@market-analyser\desktop\`; Claude Code's `.mcp.json` points at whichever bearer the user last copied; all three can disagree at once, producing the "renderer connects to port X, agent talks to port Y, no chart updates ever land" failure mode captured in `.claude/smoke-handoff-plan-0007.md`. The handoff's proposed one-liner (`app.setName('market-analyser')` early in `main.ts`) patches the symptom for the current package layout, but the underlying contract — *Python and Electron must agree on this path by construction* — is not codified anywhere. The next rename of `desktop/package.json#name`, the next change to `build.productName`, or the next platform-specific fallback added to either resolver brings the divergence back.

The other forces at play:

- **The agent-primary architecture (ADR-0015) makes the divergence load-bearing.** When Electron was the only client, both halves of "the data dir" were Electron's choice. With Claude Code as a co-equal client that launches independently of Electron, the data dir is a contract between *three* processes (sidecar, viewer, agent), not two.
- **Tests defend local correctness, not cross-process integration.** Phase 1's `sidecar-supervisor.spec.ts` injects `dataDir: '/tmp/test-data'`; phase 4's `live-chart.spec.ts` injects `MARKET_ANALYSER_DATA_DIR` from `app.getPath('userData')` (the JSDoc at lines 50–57 documents this as a known workaround). Both ship green while the production paths diverge. CI cannot catch this class of bug without a spec that exercises the real resolvers on both sides and asserts they agree.

## Decision

We will treat the shared data directory as a **contract**: a single algorithm, implemented identically on both sides, with a startup-time assertion that the two implementations agree.

Concretely:

1. **One canonical algorithm.** The data dir is `<platform-base>/market-analyser/` where `<platform-base>` is `%APPDATA%` on Windows, `~/Library/Application Support` on macOS, `$XDG_DATA_HOME or ~/.local/share` on Linux. The literal `"market-analyser"` is the contract — it never derives from `package.json#name`, `build.productName`, or any other indirection. `MARKET_ANALYSER_DATA_DIR` remains a verbatim override for tests and explicit-relocation use cases.
2. **Electron stops trusting `app.getPath('userData')` for this purpose.** A new resolver in `desktop/shared/data-dir.ts` computes the canonical path directly. `app.setName('market-analyser')` is still called early in `main.ts` so OS-level surfaces (window title, taskbar grouping, recent-files) show the right name, but the data-dir resolution does not depend on it.
3. **Python side stays as-is by inspection.** `default_app_data_dir()` already implements the canonical algorithm. The contract documents *that this is the algorithm*, not just *the current behaviour*.
4. **`/healthz` reports the data dir the sidecar is using.** The route returns `{ok, version, data_dir}`. The Electron attach path computes its own canonical path, fetches `/healthz` (bearer-authenticated for `data_dir` disclosure — `ok`/`version` stay public for the unauthenticated liveness probe), and if `data_dir` does not match the path Electron just read the lockfile from, refuses to attach with a structured error. The mismatch is logged and surfaced to the user via the same fatal-window path used for other startup failures.
5. **A regression test fails if either resolver drifts.** A test that imports the Electron resolver and the Python resolver and asserts equality on each platform — or, equivalently, a single source-of-truth check (e.g. parametrised env-var fixtures that exercise both resolvers and compare results) — runs in CI on every change to either resolver or the platform-base logic.

## Consequences

### Positive

- **The "three lockfile paths on disk" failure mode is structurally impossible.** Manual sidecar, Electron-spawned sidecar, and Claude Code all read/write the same paths because all three derive them from the same algorithm — and any drift in the algorithm fails the regression test before it ships.
- **The standalone-sidecar promise from ADR-0016 holds in dev mode too.** Previously the promise was load-bearing only in packaged builds; dev-mode users (i.e. every developer working on the project) hit a different bug class. After this ADR, dev and packaged behave the same.
- **The `/healthz` identity surface unblocks the deferred Plan 0007 line-247 requirement** — the plan explicitly required `/healthz` confirmation on attach but never landed it. This ADR makes it part of the contract rather than a phase-1 done-when that got dropped.
- **`mcp-secret.json` consistency is automatic.** Because the file lives at `<data-dir>/mcp-secret.json` and both halves agree on the data dir, the agent's `.mcp.json` bearer cannot silently point at a different file across spawn/manual cycles.

### Negative

- **Electron stops using `userData` for its primary persistent state.** This is an unusual departure — `app.getPath('userData')` is the conventional choice for an Electron app's persistence. We are *deliberately* opting out for this one data dir to gain cross-process consistency with Python. Other Electron-private state (cache, sessions, devtools settings) can keep using `userData` — only the shared dir is contractual.
- **Renaming the desktop package no longer "just works".** Future renames of `desktop/package.json#name` or `build.productName` no longer migrate the data dir along with them; the data dir is anchored to the literal `"market-analyser"` string in code. A future rebrand would need an explicit migration step, not a config edit. We accept this — silent migrations of user data are worse than explicit ones.
- **`/healthz` grows a field.** The auth-exempt `/healthz` was a minimal `{ok, version}`. Adding `data_dir` opens a small information-disclosure surface (the absolute path on disk reveals the username on Windows/macOS). Mitigation: `data_dir` is only included when the request carries the renderer bearer; the unauthenticated path stays `{ok, version}`. This keeps the liveness probe public while the identity claim is gated.
- **Two-resolver duplication.** Python and TypeScript each implement the algorithm. A future refactor cannot DRY this up across the language boundary (no shared library). We accept the duplication and let the regression test be the consistency enforcement.

### Neutral

- **`MARKET_ANALYSER_DATA_DIR` semantics unchanged.** The env-var override still wins on both sides; tests and explicit-relocation use cases keep working.
- **Packaged builds behave identically.** `app.setName('market-analyser')` already lined up with `productName`; this ADR is a no-op for packaged builds at runtime. The CI regression test would catch any future drift.

## Alternatives considered

### Alternative A — `app.setName('market-analyser')` and trust `userData`

The minimal fix from the smoke handoff. One line in `main.ts`; `app.getName()` now returns `'market-analyser'` early enough that `app.getPath('userData')` resolves correctly. Rejected because it patches the current symptom without fixing the contract: any future change that resets the name later (a third-party Electron plugin, a different test bootstrap, a rebranded fork) silently breaks the alignment again. The phase-1 supervisor spec wouldn't detect it because it never exercises the real `app.getPath('userData')` path. The smoke would fail in dev a second time; the next debugging session repeats yesterday's work.

### Alternative B — Sidecar publishes data dir via `/identity`, Electron reads it and trusts it

Electron skips its own data-dir resolution entirely. On boot it tries `app.getPath('userData')` for the *first* lockfile read; if `/healthz` (or a new `/identity`) reports a different `data_dir`, Electron re-reads the lockfile from the sidecar's reported path. Rejected because it inverts the contract: the sidecar becomes the source of truth and Electron is a follower. That's wrong for two reasons — (a) Electron also writes to the shared dir (the renderer's `Stop sidecar` button hits `/settings/stop` which the sidecar uses to remove its own lockfile, but Electron's own cache of e.g. recently-opened symbols is meant to live there too in future plans), and (b) the agent (Claude Code) reads `mcp-secret.json` independently of both — there is no single follower-leader pair, there are three peers, so the contract must be a *shared spec* not a *broadcast value*.

### Alternative C — Deterministic port + drop the lockfile-as-discovery dance

Pin the sidecar to a fixed port (e.g. 8765). The lockfile then only carries the bearer + PID. The data-dir mismatch becomes lower-stakes because the port discovery doesn't depend on which lockfile you read. Rejected for this ADR's scope because (a) the bearer file (`mcp-secret.json`) and SQLite cache (`app.db`) still live in *some* data dir and still need cross-process agreement, so the contract problem isn't solved, just moved; (b) fixing the port introduces a new failure mode (port collision with another app) that ADR-0016 deliberately avoided via OS-picked ports. If the deterministic-port idea returns, it warrants its own ADR superseding the relevant section of ADR-0016 — not a side-effect of this one.

## Notes

The Plan 0007 phase 4.1 phase implements this ADR. Phase 4.2 (identity check on attach) consumes the new `/healthz` data-dir field. The regression test that fails on resolver drift lives at `tests/test_data_dir_contract.py` plus its Electron-side companion under `desktop/tests/main/`.

The literal `"market-analyser"` directory name was chosen at Plan 0001 phase 1 (`APP_DIRNAME` in `src/market_analyser/config.py:19`) and is canonical from this ADR onward. Future renames would require a migration step, not a refactor.
