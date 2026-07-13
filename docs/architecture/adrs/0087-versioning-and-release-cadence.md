# ADR-0087 — Application versioning: SemVer 0.x with `major_version_zero`, commitizen as the single bump authority run at plan close

> **Status:** accepted
> **Date:** 2026-07-13
> **Related plan(s):** none (repo-wide convention; no paired plan)

## Context

The app version was `0.0.1` in three places — `pyproject.toml` `[project].version`, `pyproject.toml` `[tool.commitizen].version`, and `desktop/package.json` `version` — and had never moved, despite ~90 shipped plans and ~86 ADRs' worth of features. The number is meaningless as-is: a reader (or a packaged installer's "About" box, or a `git tag`) sees `0.0.1` and infers pre-alpha, which is false.

The root cause is not neglect of a chore; it is that **nothing owned the bump**. `commitizen` was configured (`[tool.commitizen]`, `tag_format = "v$version"`, `version_scheme = "pep440"`) but was never run to bump, had no `version_files` wiring to keep the three strings in sync, and had no `major_version_zero` setting — so the first `BREAKING CHANGE`-tagged commit run through `cz bump` would have jumped the app straight to `1.0.0`, declaring a stability guarantee the app does not offer (no published installers; ADR-0025's execution arc and the portfolio pillar are still in flight).

Three questions had to be answered together, because they interact: (1) what number does the app carry now; (2) how does it move going forward; (3) who is responsible for moving it. Left unanswered they recur — the version drifts back to a lie, or three files disagree.

## Decision

**Semantic versioning, held in the `0.x` band until the app declares stability.** We set the version to **`0.5.0`** now — pre-1.0 (the public surface is not frozen; installers are unpublished) but started high enough to read as "mature but unreleased" rather than "just bootstrapped." `1.0.0` is a deliberate future act, taken only when we choose to guarantee the MCP tool surface / REST contract, not a number we back into.

**`commitizen` is the single bump authority, and `[project].version` in `pyproject.toml` is the single source of truth.** We set `version_provider = "pep621"` (commitizen reads and writes `[project].version` directly), which lets us **delete the redundant `[tool.commitizen].version` field** — there is now exactly one canonical version string in `pyproject.toml`. `version_files` syncs `desktop/package.json` from it on every bump. We set `major_version_zero = true` so that while in `0.x`: a `feat` commit bumps the **minor** (`0.5.0 → 0.6.0`), a `fix` bumps the **patch** (`0.5.0 → 0.5.1`), and a `BREAKING CHANGE` also bumps the **minor** (never silently to `1.0.0`).

**The bump runs once per plan, in the architect's close ceremony.** After a plan's review passes and its close docs land, architect runs `cz bump` (which computes a single increment from all conventional commits since the last tag, writes the new version to both files, and creates the `vX.Y.Z` tag). This gives **one version bump per shipped plan** — the unit a user recognizes as "a feature" — rather than one per phase commit (which would inflate the minor several times per plan). `cz bump` stages and tags but does not push; the user pushes, consistent with the project's no-auto-push rule.

## Consequences

### Positive
- The version number becomes truthful again and stays truthful — one command, one canonical source, no three-file drift.
- `major_version_zero = true` removes the `1.0.0`-by-accident trap: reaching 1.0 is now an explicit choice, not a side effect of a `BREAKING CHANGE` footnote.
- "One bump per plan" gives the version a meaning a human can read: the minor number is roughly "how many feature-plans have shipped since 0.5.0."
- Fits the existing ceremony exactly — architect already owns the close, already commits docs by explicit path, already refrains from pushing. The bump is one more close-ceremony step, not a new workflow.

### Negative
- **The bump is a manual close-ceremony step that can be forgotten**, exactly as it was before. This ADR mitigates by naming the owner (architect) and the moment (close), and by adding it to the close checklist — but there is no CI gate forcing a bump per plan, so discipline still carries it. We accept this over a per-commit CI auto-bump, which would couple version churn to phase commits (see Alternative C).
- **`cz bump` writes a git tag**, so a mistaken bump leaves a tag behind. Tags are cheap to delete locally before push, but this is a sharper edge than a plain version edit. Since we never rewrite history, a bad *pushed* tag is corrected forward with the next bump, not amended.
- **A plan that ships only `chore`/`docs`/`refactor` commits produces no bump** (commitizen finds nothing version-bumping). That is correct behavior — a docs-only plan isn't a feature — but it means "one bump per plan" is really "one bump per plan that changed behavior," which the closer must not mistake for a missed step.

### Neutral
- The jump from `0.0.1` to `0.5.0` is a one-time discontinuity; the `v0.0.1`-era had no tags, so no tag history is invalidated.
- `desktop/package.json` remains a synced follower, not an independent version. The renderer and sidecar always share one version number, which matches how they ship (one app, one installer).

## Alternatives considered

### Alternative A — Start at `0.1.0`
The conventional "first meaningful pre-release." Rejected in favor of `0.5.0` on the user's call: `0.1.0` reads as "just past bootstrap," which understates an app with ~90 shipped plans. Both are pre-1.0 and mechanically identical going forward; the only difference is the starting integer, and `0.5.0` communicates maturity honestly. (Had the app been genuinely early, `0.1.0` would have been right.)

### Alternative B — Keep `version_provider = "commitizen"` and sync all three strings via `version_files`
Leaves the canonical version in `[tool.commitizen].version` and lists both `[project].version` and `package.json` as synced files. Rejected because it keeps **three** version strings in `pyproject.toml`'s orbit (the tool field plus the project field plus the package.json follower) and requires a `version_files` regex against `pyproject.toml` that must avoid matching `version_scheme` / `target-version` — a fragile, easy-to-break pattern. `version_provider = "pep621"` makes `[project].version` canonical and deletes the tool field entirely: one source in `pyproject.toml`, one synced follower.

### Alternative C — CI auto-bump on every merge to `main` (per-commit cadence)
A GitHub Action runs `cz bump` on every push. Rejected because the project commits **per phase** (several `feat` commits per plan), so per-commit bumping would move the minor several times per feature — exactly the noise "one bump per plan" avoids — and it would fire on pushes the user makes, coupling version state to CI timing rather than to the human-meaningful close event. It also contradicts the no-auto-push posture: a CI bump implies a CI push of the tag. Manual-at-close keeps the version aligned to shipped features and keeps pushes user-driven.

### Alternative D — Do nothing / bump the three strings by hand each feature
The status quo. Rejected: it is how the version stalled at `0.0.1` in the first place. Three hand-edited strings with no owner and no tag is precisely the drift this ADR exists to end.

## Notes
- Mechanics after this ADR: `cz bump` (auto-detects increment) at plan close; `cz bump --dry-run` to preview; `uv run cz version -p` to read the current project version. The `v$version` tag format and `pep440` scheme are unchanged.
- This ADR governs the *application* version only. Dependency versions are governed by ADR-0012 (cooldown) and ADR-0013 (exact pinning) and are unrelated.
- The close-ceremony checklist in the architect skill gains a "bump version (`cz bump`) + confirm tag" step; the README's Configuration section documents the scheme for developers.
