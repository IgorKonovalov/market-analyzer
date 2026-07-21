# Living behavioral specs

One hand-authored **behavioral contract per core subsystem** — the invariants and
`WHEN/THEN` scenarios that are true of the *running system today*, reconciled at
each plan's close so they can't drift. This layer is the one idea borrowed from
OpenSpec ([ADR-0106](../adrs/0106-spec-system-posture-and-living-specs.md)) without
adopting the tool.

## What lives here, and what doesn't

Three artifacts answer three different questions; keep them distinct:

| Artifact | Question | Lifecycle |
|----------|----------|-----------|
| **Plans** (`plans/`) | *What are we building next?* | Expire → `git mv` to `plans/done/` at close |
| **ADRs** (`adrs/`) | *Why did we choose this over the alternatives?* | Append-only; superseded, never edited |
| **Specs** (`specs/`, here) | *What does the system do now, by behavior?* | Living — reconciled at each close |
| generated apiref (`docs/reference/`) | *What is the mechanical surface?* (params, payloads) | Regenerated from the live sidecar (ADR-0064) |

A spec is **behavioral intent**, not mechanical surface: "a decision at bar `i` sees
only `bars[0..=i]`" belongs here; the exact params of `run_backtest` belong in
generated apiref. Specs are per *subsystem behavioral contract*, not per file — if a
subsystem has no non-obvious invariant, it gets no spec.

## Index

| Spec | Subsystem | Governing ADRs |
|------|-----------|----------------|
| [backtest-engine.md](backtest-engine.md) | Pure backtest orchestrator — determinism + no-lookahead contract | 0018, 0004, 0007, 0050 |
| [data-provider.md](data-provider.md) | `MarketDataProvider` Protocol + the `as_of` anti-lookahead seam | 0007, 0009, 0031, 0032 |
| [advisory-boundary.md](advisory-boundary.md) | Conditions-vs-calls boundary + the one advisor carve-out | 0029, 0015, 0025, 0068 |
| [mcp-tool-surface.md](mcp-tool-surface.md) | One-verb-per-tool granularity + `EXPECTED_FULL_TOOLSET` budget | 0104, 0014, 0015, 0064 |

`_template.md` is the shape to copy for a new spec; it and this `README.md` are
**excluded** from the freshness gate.

## The reconcile-at-close convention

A spec stays fresh because reconciliation is bound to the moment behavior changes —
the architect's **close ceremony** (the same session that flips a plan to `done/`).
When a plan's close reconciles a spec, bump that spec's `Reconciled-through:` line to
the closing plan's number. This is a standing close-ceremony step (see the architect
skill's SKILL.md), not an optional extra: a spec whose behavior changed but whose
`Reconciled-through:` didn't move is lying with authority, which is worse than no spec.

Behavioral *accuracy* is architect judgment at reconcile time. What is machine-checked
is *structural freshness only.*

## The freshness gate

`specs --check` (Plan 0112 phase 2, a `dev`-owned CI check sibling to `apiref --check`)
enforces structure, not truth. It fails when any spec here (excluding `_template.md`
and `README.md`):

- is missing a required section (`## Invariants`, `## Scenarios`), or
- is missing a `Reconciled-through:` line, or
- references a `Plan NNNN` that resolves to neither `plans/` nor `plans/done/`.

Keep the section headings and the `Reconciled-through: Plan NNNN` line exact so the
gate stays green. A spec that lost its `Reconciled-through:` line or points at a
non-existent plan turns CI red rather than passing silently.

## Adding a spec

1. Copy `_template.md` to `docs/architecture/specs/<subsystem>.md`.
2. Fill the header (Subsystem / Source / `Reconciled-through:` / Governing ADRs), the
   invariants, the scenarios, and the honest gaps. Ground each claim in an ADR and,
   where useful, the source file that enforces it.
3. Add a row to the index above.
4. Specs are added *lazily as plans touch a subsystem* — four seed contracts is the
   intended ceiling for the initial layer, not a target to pad toward.
