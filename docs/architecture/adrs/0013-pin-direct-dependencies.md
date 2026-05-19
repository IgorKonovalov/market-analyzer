# ADR-0013 — Pin every direct dependency to an exact version in the manifests

> **Status:** accepted
> **Date:** 2026-05-19
> **Related plan(s):** [0005-dependency-cooldown](../plans/0005-dependency-cooldown.md)
> **Related ADRs:** [ADR-0012](0012-dependency-cooldown.md) — companion decision on resolution-time cooldown.

## Context

This repository ships two dependency manifests: `pyproject.toml` (consumed by `uv` for the sidecar) and `desktop/package.json` (consumed by `pnpm` for the Electron shell). Both manifests today carry a mix of constraint styles. `pyproject.toml` uses `>=` floors across the board (`fastapi>=0.115`, `pydantic>=2.9`, `ruff>=0.7`, …). `desktop/package.json` is closer to pinned — most runtime deps already name exact versions — but a handful of dev tooling entries use `^` ranges (`"@eslint/js": "^10.0.1"`, `"eslint-config-prettier": "^10.1.8"`).

Lockfiles already protect installs: `uv.lock` and `pnpm-lock.yaml` pin exact versions with hashes, and CI runs `--frozen` on both ecosystems. So the question "what gets installed?" is already deterministic via the lockfile. The question this ADR addresses is different: **what does the manifest itself say?** With `>=` and `^` floors, the manifest does not document what is actually installed — to know what is in use you have to read the lockfile. More importantly, when someone runs `uv lock --upgrade` or `pnpm update`, the resolver is free to pick anything that satisfies the loose constraint, and the only signal of "we just upgraded fastapi from 0.115 to 0.118" is the lockfile diff. Manifest constraints stop being a statement of intent and become merely an admissibility filter.

ADR-0012 sets a resolution-time cooldown so that whatever the resolver picks is at least 14 days old. That bounds *time-newness* but does not bound *intent*. A maintainer running `uv lock --upgrade` today still sweeps every direct dependency to its newest cooldown-admissible version, all in one undifferentiated lockfile diff. We want the opposite: each version bump should be a deliberate manifest edit, named in the diff, with the upgrade reason in the commit message.

There is no technical force pushing back against exact pinning. `uv` and `pnpm` both treat `==X.Y.Z` / `"X.Y.Z"` as first-class constraints. The cost is editorial: bumping a dependency now requires editing the manifest as well as refreshing the lockfile. We think that cost is the feature.

## Decision

We will pin every direct dependency in this repository to an exact version in its manifest. Concretely:

- **`pyproject.toml`** — every entry in `[project.dependencies]` and every entry in `[dependency-groups.dev]` uses the `==X.Y.Z` form (e.g., `"fastapi==0.115.4"`). No `>=`, no `~=`, no `^`. The `[build-system] requires` list is pinned by the same rule.
- **`desktop/package.json`** — every entry in `dependencies` and `devDependencies` is an exact version string (e.g., `"@eslint/js": "10.0.4"`). No `^`, no `~`, no `>=`, no `*`, no `latest`.

Bumping a dependency is a two-line change (manifest edit + lockfile refresh) committed together, with the upgrade reason in the commit message — security patch, feature requirement, ecosystem-wide bump, etc. The lockfile diff is then meaningful because it is *only* showing transitive churn caused by the named direct bump.

We do not pin transitive dependencies in the manifest. The lockfile already handles those, and naming transitives in the manifest would invert the resolver's role.

## Consequences

### Positive

- **The manifest is the truth.** Reading `pyproject.toml` / `desktop/package.json` tells you what version is in use, full stop. The lockfile is not a separate source of truth that the manifest summarises — they agree by construction, on the direct deps that matter for review.
- **Lockfile diffs become readable.** Today, a `uv lock --upgrade` produces a sea of churn that nobody can meaningfully review. After this ADR, the only direct-dep changes in a lockfile diff are the ones whose manifest entries were also touched in the same commit. The remaining lockfile churn is transitive, which is the kind of churn you skim, not the kind you audit line by line.
- **Pairs cleanly with ADR-0012.** The cooldown stops young versions from being picked; the pin stops *any* version from being silently picked. Together they convert "resolution sweeps the upstream registry" into "resolution is bounded by what the manifest names." That is the property we want for a repo whose backtest outputs need to be reproducible across machines and across time.
- **Easier root-causing.** When `pytest` starts failing or a renderer test goes red after a `uv lock` / `pnpm install`, you can immediately tell whether a direct dep moved (manifest diff) or only a transitive shifted (lockfile-only diff). The signal is co-located with the cause.

### Negative

- **Every upgrade is now a manifest edit.** Bumping `ruff` from `0.7.0` to `0.7.3` is a one-line `pyproject.toml` change plus a `uv lock` plus a commit. Today it is just a `uv lock --upgrade ruff` and a lockfile commit. The friction is modest but real, and it lands on every contributor.
- **Dev-tooling churn shows up in `git log`.** Pinning the `[dependency-groups.dev]` group (ruff, mypy, pytest, …) means every routine bump of those tools is a `pyproject.toml` commit. Some teams pin only runtime deps and let dev-tooling float. We are pinning both because divergent ruff/mypy versions across machines produce divergent lint/type output, which we have explicitly chosen against for determinism reasons. The cost is more chore commits.
- **No more "automatic minor upgrades" from `^` or `~`.** A contributor who does `pnpm add some-package` will get an exact pin and will not subsequently absorb its `1.4.x` patches without an explicit bump. That is the point, but it means we lose the convenience layer that range operators provide for low-risk patch acquisition.
- **Initial migration cost.** Every existing `>=` and `^` in both manifests must be rewritten to the exact version currently in the lockfile. That is a mechanical chore (Plan 0005 carries the work) but it is a one-time cost that has to actually happen.

### Neutral

- This ADR does not interact with the lockfile policy. Both ecosystems continue to commit lockfiles, CI continues to `--frozen` everywhere. The ADR only governs the *manifest* shape.
- This ADR does not interact with `pip-audit`, the cooldown setting, or any test invocation. Pinned manifests look identical to the rest of the tooling.
- Automatic dependency-bump bots (Renovate, Dependabot) work fine against exact pins — they read the lockfile, see the available newer version, and open a PR with both the manifest edit and the lockfile refresh. We are not currently running such a bot; if we adopt one in the future, this ADR has no special interaction with it beyond requiring the bot to edit the manifest (which all current major bots already do).

## Alternatives considered

### Alternative A — Pin only runtime dependencies, leave dev-tools on ranges

`[project.dependencies]` and `dependencies` get exact pins; `[dependency-groups.dev]` and `devDependencies` stay on `>=` / `^`. Rejected because lint/format/type-check tools (ruff, mypy, prettier, eslint) produce different output across minor versions. If two contributors run different `ruff` versions, one CI is green and the other contributor's pre-commit blows up locally — for no reason that is anyone's fault. The determinism we care about extends to the lint/type pass, so dev-tools get the same pinning rule. The friction cost is the same chore commit pattern either way.

### Alternative B — Use `~=` (compatible-release) instead of `==`

Pin to the minor (`fastapi~=0.115`), letting patch versions float. Rejected because it reintroduces the original problem at a smaller scale: a `uv lock --upgrade` can still silently move `0.115.4` to `0.115.7`, and the manifest still misrepresents what is installed. The whole point of this ADR is that the manifest is the source of intent; `~=` weakens that. Patch-only floating also does not buy us much — we already gate young versions via the cooldown, so the security argument for "let patch versions auto-flow" is already addressed.

### Alternative C — Status quo (loose ranges in manifests, lockfile as truth)

Keep `>=` and `^`, lean on the lockfile for reproducibility, treat the manifest as an admissibility filter. Rejected because lockfile diffs alone do not document intent. The cooldown ADR already pushes us toward "every resolution is a deliberate event"; loose manifest ranges undercut that. The status quo also confuses new contributors: they read `>=0.115` and think they can update to any newer version, when in practice the lockfile pins something specific and `--frozen` enforces it.

## Notes

- The exact versions that go into the manifest at migration time are read out of the current lockfiles (`uv.lock`, `pnpm-lock.yaml`). The migration commit must not change resolution; it only relabels the constraint format. Plan 0005's done-when for the pinning phases verifies this by running `uv sync --frozen` and `pnpm install --frozen-lockfile` before and after and asserting no version diff.
- The cooldown (ADR-0012) and the pin (this ADR) are designed to compose. A contributor adding a new package runs `uv add fastapi` and gets an exact pin written into `[project.dependencies]` whose value is the newest version older than the cooldown cutoff. Both properties hold simultaneously without any per-command flag work.
- This ADR does not mandate a particular dependency-update bot. If/when we adopt one, this ADR is compatible.
