# ADR-0012 — Dependency cooldown: refuse package versions younger than 14 days

> **Status:** accepted
> **Date:** 2026-05-19
> **Related plan(s):** [0005-dependency-cooldown](../plans/0005-dependency-cooldown.md)
> **Related ADRs:** [ADR-0013](0013-pin-direct-dependencies.md) — companion decision on manifest-level exact pinning. Together these form the project's dependency-discipline pair: the cooldown bounds *time-newness* of resolved versions, the pin bounds *intent* (no silent upgrades from a lockfile refresh).

## Context

`market-analyser` pulls from two large public package registries: PyPI (via `uv` for the sidecar) and the npm registry (via `pnpm` for the desktop shell). Both ecosystems have a recurring class of incident in which a malicious version of a legitimate package — or a freshly-published typosquat — is pushed to the registry and is in the wild for hours to days before community/security tooling flags it and it gets yanked. The historical detect-and-takedown window for these incidents is on the order of one to fourteen days; very few survive longer than that without being reported and removed.

This repository's lockfiles (`uv.lock`, `pnpm-lock.yaml`) already protect installs once the lock is committed: CI runs `uv sync --frozen` and `pnpm install --frozen-lockfile`, so a frozen environment cannot silently absorb a new upstream version. The exposure window is therefore narrow but real, and it sits at **resolution time** — when a developer runs `uv add`, `uv lock --upgrade`, `pnpm add`, or `pnpm update`. Whatever versions the resolver picks during those commands then get pinned into the lockfile and propagate to every machine and to CI.

Both `uv` and `pnpm` ship a first-class mechanism for refusing package versions younger than a given threshold during resolution. `uv` exposes it as `[tool.uv] exclude-newer = "YYYY-MM-DD"` (and the matching `--exclude-newer` CLI flag); `pnpm` exposes it as the `minimumReleaseAge` setting in `pnpm-workspace.yaml` (camelCase, value in minutes; pnpm v10+ reads only auth/registry from `.npmrc`). Both are deterministic, both are honored by the resolver, and both leave already-locked packages untouched. We therefore have a low-cost way to close the resolution-time window without introducing custom tooling.

The cost is that the same mechanism that blocks malicious young versions also blocks legitimate young CVE patches. We need to accept that lag explicitly and document the override path, rather than design an automated bypass that the policy can't actually defend.

## Decision

We will enforce a **14-day minimum release age** on all package versions resolved against PyPI and the npm registry, configured statically in this repository and tracked in version control:

- **Python (`uv`)** — `[tool.uv] exclude-newer = "<cutoff-date>"` in `pyproject.toml`.
- **Node (`pnpm`)** — `minimumReleaseAge: <minutes>` in repo-root `pnpm-workspace.yaml` (verified at Plan 0005 implementation time; requires `pnpm` ≥ `10.16.0`).

The cutoff is **a single tracked value** per ecosystem. Advancing it is a routine chore commit (target cadence: weekly, advancing the cutoff by approximately seven days), and is the only sanctioned way to admit a younger package version. Security-driven bumps — e.g., a published CVE in a dependency whose patch is younger than the cutoff — land as a regular commit whose message names the CVE. There is no per-package allowlist, no `--exclude-newer` override flag in normal workflows, and no dynamic "today minus 14 days" computation: the cutoff is what is committed, and resolution is reproducible across every machine that checks out this commit.

## Consequences

### Positive

- Closes the resolution-time window where a freshly-uploaded malicious version of a transitive dependency can be picked by the resolver and pinned into the lockfile. The 14-day floor sits comfortably past the historical detect-and-takedown window for the bulk of malicious-package incidents in both ecosystems.
- Determinism is preserved. The cutoff is a static value in `pyproject.toml` / `pnpm-workspace.yaml`; running `uv lock` or `pnpm install` on different machines on different days produces the same resolution result. This matches the repo's stated determinism stance and keeps backtest reproducibility intact for any code path that transitively depends on a pinned package version.
- The mechanism is built into both package managers — no custom proxy, no private registry mirror, no wrapper script. The blast radius of a misconfiguration is small (resolution fails loudly with a useful error).
- The single-value override path is auditable. Every cutoff bump is a diff in `git log`; CVE-driven bumps are discoverable by grepping commit messages for the CVE identifier. No allowlist file rots silently.

### Negative

- **Security-patch lag.** A CVE patch published yesterday cannot be installed until either fourteen days pass or the cutoff is bumped. The bump is intentional friction — it forces an explicit "we are accepting code that is X days old" review — but it does slow down emergency response. The mitigation is documentation: the `Dependency cooldown` section in `CLAUDE.md` (added by Plan 0005) describes the bump procedure and links to this ADR so the response path is on-the-shelf when it's needed.
- **Adding a brand-new dependency is sometimes blocked.** If a developer wants to add a package whose only-ever-released version is less than 14 days old (rare, but happens for new tooling), the resolver will refuse. The path forward is the same: bump the cutoff with justification in the commit message. We will not introduce a per-package allowlist for this case — the friction is part of the policy.
- **Periodic chore commits.** The cutoff drifts further behind every day it isn't bumped. The implicit contract is that someone (the user, in practice) advances both cutoffs ~weekly. If the cadence slips badly, dependency-update PRs start failing for ordinary reasons until the cutoffs are caught up. The plan calls this out and adds a one-liner README note; we are explicitly not building automation for the bump (a scheduled bot would re-introduce the wall-clock dependence we just removed).
- **pnpm version floor.** `minimumReleaseAge` is supported on `pnpm` ≥ `10.16.0`; older pnpm versions silently ignore unknown workspace-level settings. Plan 0005 phase 1 verified the floor, bumped `desktop/package.json`'s `packageManager` field to `pnpm@11.1.2`, and bumped `pnpm/action-setup`'s `version:` in CI to match. If a contributor is on a pre-feature pnpm, their local resolution would not be guarded; the CI version pin protects the lockfile.

### Neutral

- This ADR does not replace `pip-audit` (already in CI) or any future vulnerability scanner. Cooldown is preventive (refuses young versions during resolution); audit is reactive (flags known vulnerabilities post-resolution). They are complementary; both stay.
- This ADR does not change the lockfile policy or the `--frozen` install discipline. Those mechanisms protect everything downstream of resolution; the cooldown protects resolution itself.
- The pnpm version floor that this ADR creates (≥ `10.16.0`, in practice `11.1.2`) brought one piece of unrelated v11 collateral: `pnpm-workspace.yaml`'s `onlyBuiltDependencies` field was removed in v10 and is deleted by v11, so `electron` and `esbuild` were migrated to `allowBuilds: {electron: true, esbuild: true}` as part of Plan 0005. Future pnpm-major bumps should expect this shape of breaking-config collateral.

## Alternatives considered

### Alternative A — Per-package allowlist of "exempt" young versions

Maintain a list of packages exempt from the cooldown, either inline in `pyproject.toml` / `pnpm-workspace.yaml` or as a separate manifest. Rejected because the allowlist becomes a quietly-growing exception register: each entry is reviewed once at add-time and rarely audited afterwards, and a malicious version of an already-exempt package would walk straight through the policy. The single-tracked-cutoff path forces every admission to surface as a `git diff` on the cutoff value, with the reason captured in a commit message that lives in `git log` forever. That is the auditing property we want; an allowlist actively undermines it.

### Alternative B — Dynamic "today minus 14 days" cutoff computed at resolution

A wrapper script invokes `uv` / `pnpm` with a freshly-computed cutoff timestamp. Rejected because resolution then becomes wall-clock-dependent: two developers running `uv lock` on adjacent days could legitimately resolve to different versions, even with no other changes. This breaks the determinism contract this repo states and complicates lockfile review (the diff says "the resolver picked a newer minor — is that because of a real change, or because today is Tuesday?"). The static-cutoff option preserves the property that the lockfile is a function of the commit's contents, not the calendar.

### Alternative C — Hardened private registry mirror

Stand up a Verdaccio / private PyPI proxy that holds packages for a 14-day quarantine before serving them downstream. Rejected because the operational surface (a running service, its own auth, its own patching) outweighs the marginal benefit over the in-resolver mechanism. The package managers already do the right thing natively; running a separate cache in front buys us nothing that the cutoff doesn't already buy, and adds a piece of infrastructure that must itself be kept current and trusted.

### Alternative D — Commercial supply-chain scanner (Socket / Phylum / Snyk)

Drop one of the commercial detectors into CI to flag known-malicious packages directly. Not rejected — these tools are complementary and could be layered on later — but they are not a substitute for the cooldown. They depend on the vendor's detection coverage and on their detection-to-publish latency being shorter than the attacker's publish-to-install latency. The cooldown adds a hard time floor that doesn't rely on anyone's detection pipeline. If we adopt one of these scanners later, it stacks on top of this ADR rather than replacing it.

## Notes

- Reference: `uv` documents `--exclude-newer` and `[tool.uv] exclude-newer` as a stable, reproducibility-oriented feature.
- Reference: `pnpm` documents `minimumReleaseAge` as a security setting in `pnpm-workspace.yaml` (camelCase, value in minutes). Plan 0005 phase 1 verified this is the only honored location on pnpm v10+ (`.npmrc` is read only for auth/registry) and that the floor version is `10.16.0`.
- The 14-day choice is a balance between cooldown protection and patch lag. Future ADRs may revise it if incident data suggests a different window; the mechanism would not change, only the constant.
