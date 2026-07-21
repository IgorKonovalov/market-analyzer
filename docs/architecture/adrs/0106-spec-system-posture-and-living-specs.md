# ADR-0106 — Documentation-system posture: keep bespoke plans + ADRs, add a native living-spec layer, decline OpenSpec for the core repo

> **Status:** proposed | accepted (Plan 0112 accepts at close)
> **Date:** 2026-07-21
> **Related plan(s):** 0112-living-behavioral-specs

## Context

The user asked whether this repo — or future ones — should adopt [OpenSpec](https://github.com/Fission-AI/openspec) (Fission-AI), a lightweight spec-driven-development framework. OpenSpec puts an `openspec/` directory in a project with three parts: `specs/` (living, plain-Markdown behavioral requirements — `SHALL` + `WHEN/THEN` scenarios describing the system's *current desired* behavior), `changes/<feature>/` (one folder per in-flight change: `proposal.md` + `design.md` + `tasks.md` + a `specs/` delta), and `archive/` (completed changes filed by date). It drives an AI agent through slash commands (`/opsx:explore → propose → apply → archive`) and ships a `stores` beta for read-only cross-repo shared specs.

This repo already runs a mature, deeply-integrated documentation pipeline. **Plans** (`docs/architecture/plans/NNNN-*.md`) fuse proposal + design + task-breakdown into one owner-tagged, phased document; they are indexed in `plans/README.md`, implemented by sibling skills that branch on the machine-readable `**Owner skill:**` tag, closed by the architect's ceremony (status flip → `git mv` to `done/` → ADR acceptance → index refresh → branch merge → `cz bump`), and parallelised via git worktrees. **ADRs** (`docs/architecture/adrs/NNNN-*.md`) are append-only decision records that name their rejected alternatives — a concept OpenSpec has no equivalent for. The result is a near one-to-one overlap: OpenSpec's `changes/` ≈ our `plans/`, its `archive/` ≈ our `plans/done/`, its slash-command loop ≈ our `architect → dev/sibling → close ceremony`. Everywhere the two overlap, ours is the more capable of the pair *here*, because it knows about owner-skill routing, worktree parallelism, `/safe-commit`'s explicit-path staging under a shared working tree, the dependency-cooldown/pinning policy (ADR-0012/0013), and the domain non-negotiables (no-lookahead, determinism, secrets placement).

The overlap forces the decision rather than settling it, because OpenSpec exposes **one thing this repo genuinely lacks**: a *living* behavioral-spec layer. Our plans are point-in-time and are archived to `done/` at close; nothing merges their behavioral claims into a continuously-maintained "what the system does today" document per subsystem. The closest artifacts are the generated API reference (`docs/reference/`, ADR-0064 — mechanical: params and payload shapes, not behavioral contracts like "a decision at bar `i` sees only `bars[0..=i]`") and `CLAUDE.md` (orientation, not a per-subsystem contract). So the choice is three-way: adopt the tool, ignore it, or borrow the concept.

## Decision

We will **not adopt OpenSpec as tooling for the core `market-analyser` repo.** Its change/archive machinery is redundant with — and, in this repo, less capable than — the plans + ADRs pipeline, it carries no ADR concept, its `/opsx:*` loop is unaware of the owner-skill/worktree/`safe-commit`/`cz bump` ceremony this repo depends on, and installing its CLI is a new npm dependency subject to our own cooldown and exact-pinning policy for marginal value.

We **will borrow its one genuinely-missing idea** as a native, low-ceremony **living-spec layer** under `docs/architecture/specs/`: one behavioral contract per core subsystem, stating the subsystem's invariants and `WHEN/THEN` scenarios in plain Markdown, carrying a "reconciled-through" plan reference, and **reconciled at each plan's close ceremony** so it never drifts. This is architect-authored documentation (like ADRs and diagrams), not sibling-implemented code; Plan 0112 builds only the scaffolding, the freshness gate, and the pilot seed specs. The living spec answers "what does the system do now"; the plan answers "what are we building next" (it expires); the ADR answers "why did we choose this over the alternatives" (it doesn't).

OpenSpec is **not rejected everywhere**: it remains a sanctioned starter for a *future* lightweight repo that will not inherit this skill ecosystem (it gives proposal/design/task discipline for near-zero setup cost), and its `stores` feature is a candidate if we ever run several repos that need to share cross-repo standards (the cooldown/pinning policy, the secrets picture, the no-lookahead rule) as a read-only spec store.

## Consequences

### Positive
- Closes the actual gap (no living behavioral-truth layer) without a migration off a load-bearing, skill-integrated pipeline.
- The living-spec layer is native Markdown under `docs/architecture/` — it diffs cleanly, needs no new dependency, and reuses the existing close ceremony as its freshness mechanism.
- A durable, link-shaped record that OpenSpec was evaluated and declined for the core repo, so future sessions don't re-litigate it.
- Keeps the door open for OpenSpec where it actually fits (greenfield / cross-repo), rather than a blanket "no".

### Negative
- The living-spec layer is **new maintenance surface**: every close ceremony gains a "reconcile the touched spec" step, and a spec that silently goes stale is worse than no spec (it lies with authority). The freshness gate (Plan 0112) mitigates but does not eliminate this — a structurally-valid spec can still be behaviorally wrong.
- We forgo OpenSpec's community tooling, its 25+-tool integrations, and any future upstream improvements to its format. If our bespoke pipeline ever becomes a maintenance burden, we will have chosen to carry it rather than delegate to a maintained framework.
- The living-spec layer sits *outside* the owner-skill vocabulary (`dev`/`strategy-author`/`backtester`/`ui-builder`/`human`) — it is architect-authored, which is a slightly unusual shape for a plan (see Plan 0112's owner-tag note).

### Neutral
- `docs/reference/` (generated apiref) and `docs/architecture/specs/` (hand-authored behavioral contracts) coexist and are complementary: the former is mechanical surface truth, the latter is behavioral intent. Neither replaces the other.

## Alternatives considered

### Alternative A — Adopt OpenSpec wholesale (replace plans + `done/` with `changes/` + `archive/`)
Rejected: it would rip out a mature pipeline (110+ plans, 100+ ADRs) that is wired into every sibling skill, and replace it with a thinner generic one that has no ADR concept and no awareness of this repo's ceremony. The migration cost is real and the capability delta is negative here.

### Alternative B — Run OpenSpec only for its `specs/`, keep our plans + ADRs
Rejected: it installs the whole CLI and its `/opsx:*` command surface (a new pinned npm dependency, a competing vocabulary) to use one-third of the tool, and OpenSpec's `specs/` are coupled to its `changes/` archival flow — we would be fighting the tool to keep our own plan pipeline. Cheaper to author the living-spec layer natively in the Markdown conventions we already have.

### Alternative C — Do nothing (status quo)
Rejected: the evaluation surfaced a real gap (no maintained per-subsystem behavioral contract; apiref is mechanical, `CLAUDE.md` is orientation). Doing nothing leaves the highest-value borrowed idea on the table. We take the idea, not the tool.

## Notes

- OpenSpec source and concepts: https://github.com/Fission-AI/openspec (evaluated 2026-07-21).
- The living-spec layer is implemented by Plan 0112. This ADR is accepted at that plan's close.
