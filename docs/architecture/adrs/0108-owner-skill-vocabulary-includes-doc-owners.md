# ADR-0108 — The plan-phase owner-skill vocabulary includes `architect` and `skill-creator`

> **Status:** proposed
> **Date:** 2026-09-04
> **Related plan(s):** 0115 (rollout), 0114 (the plan that surfaced it), 0112 (the first occurrence)

## Context

Every plan phase carries a machine-readable `**Owner skill:**` tag with exactly one value from a fixed vocabulary. The implementing skills branch on it: `dev` reads the tag at the start of each phase and refuses to implement a phase owned by someone else, handing off instead. A missing or malformed tag is a Mode 4 review **blocker**.

The vocabulary as written is five values — `dev`, `strategy-author`, `backtester`, `ui-builder`, `human` — enumerated in five places (`architect/SKILL.md` twice, `architect/references/templates/plan.md`, `dev/SKILL.md`, and the `plans/README.md` vocabulary table).

That list omits two skills that own real, plan-shaped work in this repo:

- **`architect`** owns `docs/architecture/` — ADRs, plans, diagrams, and since [ADR-0106](0106-spec-system-posture-and-living-specs.md) the living specs under `docs/architecture/specs/`. Living-spec reconciliation is a discrete, committable chunk of work with its own done-when (`specs --check` green), which is exactly the shape a phase has.
- **`skill-creator`** owns `.claude/skills/` per CLAUDE.md. Correcting a skill's documented contract is likewise discrete and committable.

The gap has now been hit three times, and each time it was papered over differently:

1. **Plan 0112** put the living-spec layer outside the vocabulary. ADR-0106 recorded this explicitly rather than resolving it: *"The living-spec layer sits outside the owner-skill vocabulary (`dev`/`strategy-author`/`backtester`/`ui-builder`/`human`) — it is architect-authored, which is a slightly unusual shape for a plan (see Plan 0112's owner-tag note)."*
2. **Plan 0114 phase 3** (living-spec invariants) was tagged `architect` — out of vocabulary.
3. **Plan 0114 phase 4** (advisor skill docs) was tagged `skill-creator` — out of vocabulary. `dev` correctly refused to implement it and escalated, which is the protocol working; but the escalation had no good answer available.

The three workarounds available today are all bad. Tagging such a phase `human` misdescribes it — the work is agent-executable and has a named owning skill. Tagging it `dev` violates the ownership map in CLAUDE.md and hands skill-contract or spec edits to a skill whose review lens does not cover them. Leaving it untagged is a review blocker by construction. Meanwhile the *rule* that the tag is drawn from a fixed vocabulary is load-bearing and worth keeping: it is what makes the handoff mechanical rather than interpretive.

So the vocabulary is simply incomplete relative to the ownership map it is supposed to encode.

## Decision

We extend the plan-phase owner-skill vocabulary from five values to **seven**, adding `architect` and `skill-creator`:

`dev` · `ui-builder` · `strategy-author` · `backtester` · **`architect`** · **`skill-creator`** · `human`

The tag stays exactly one value from a fixed, closed set; every other property of the protocol is unchanged. Two consequences of the addition are made explicit:

- **`architect`-owned phases are the one case where the plan author also implements.** That is already what happens (the close ceremony's spec reconciliation is architect work landing in architect-owned files); naming it removes the "slightly unusual shape" ADR-0106 had to apologise for. It does **not** license architect to implement code phases — the boundary is the directory it owns, `docs/architecture/`.
- **Handoffs to `architect` and `skill-creator` are manual**, like every boundary except `dev` ↔ `ui-builder`. Auto-handoff stays scoped to that one high-volume implementer pair; there is no evidence yet of enough volume at the new boundaries to justify widening it, and architect↔implementer handoffs deliberately keep the fresh-context gate.

The vocabulary remains **closed**: the read-only analyst skills (`market-analyst`, `defi-analyst`) and `advisor` are deliberately *not* added. They consume the system and write to gitignored `runs/`; they do not own committed source or docs, so they never own a plan phase.

## Consequences

### Positive
- **The vocabulary now encodes the ownership map** instead of contradicting it. A phase that edits `docs/architecture/specs/` or `.claude/skills/` has a correct tag available, so the "which of three bad workarounds" question stops recurring.
- **The Mode 4 blocker becomes honest.** Today an in-vocabulary check can fail a plan that tagged its phases *correctly* per CLAUDE.md; after this, an out-of-vocabulary tag is unambiguously an error.
- **Living-spec reconciliation becomes a first-class phase.** ADR-0106's carve-out note is superseded in practice, which matters as the spec layer grows.

### Negative
- **Two more values for implementing skills to branch on**, and the branch table lives in prose across five files — so this ADR's rollout is itself the kind of multi-site doc edit that drifts. Plan 0115 fixes all five sites in one pass; a future sixth site is a real risk with no gate behind it.
- **`architect` as a phase owner blurs the author/implementer split** that the rest of the protocol works hard to keep. The mitigation is the directory boundary, which is a convention, not an enforced rule: nothing stops an architect-tagged phase from being written to touch `src/`.
- **ADR-0106 now carries a parenthetical that is out of date.** ADRs are append-only, so it is not edited; a reader who finds that line must reach this ADR to learn the vocabulary moved. That is the documented cost of append-only ADRs, paid here.

### Neutral
- No code, schema, migration, or tool-surface change. This is a process contract.

## Alternatives considered

### Alternative A — Tag such phases `human`
The user runs `/skill-creator` or `/architect` themselves. Rejected: it misdescribes agent-executable work as a user task, and `human` phases are the ones that legitimately block a close ceremony (live smokes). Overloading it would make "outstanding `human` phase" stop meaning "waiting on the user".

### Alternative B — Fold the work into `dev`
Let `dev` edit `docs/architecture/specs/` and `.claude/skills/`. Rejected: it contradicts CLAUDE.md's ownership map, and the skill boundaries exist precisely because each owner carries a different review lens. `dev` implementing its own SKILL.md's contract is a conflict of interest in the same class as an implementer reviewing their own work.

### Alternative C — Drop the fixed-vocabulary rule; allow any skill name
Rejected: the closed set is what makes the handoff mechanical. An open set turns every tag into a lookup against an implicit list, and typos stop being detectable.

### Alternative D — Leave the vocabulary alone and keep recording exceptions per plan
The ADR-0106 approach. Rejected on the third occurrence: three plans have now hit it, each resolving it differently, which is the definition of a rule that needs fixing rather than annotating.

## Notes

- This ADR **amends** the plan-phase protocol described in `architect/SKILL.md` and `dev/SKILL.md`; it supersedes nothing. ADR-0106's parenthetical vocabulary enumeration is left as written (append-only) and is superseded in substance by this decision.
- Rollout is Plan 0115: one `architect` phase for the `plans/README.md` vocabulary table, one `skill-creator` phase for the four skill-file enumerations. Plan 0114's phase 3 and phase 4 tags become valid on this ADR's acceptance.
- The vocabulary's canonical statement stays the `plans/README.md` table; the skill files restate it because the implementing skills must branch on it without reading `docs/`.
