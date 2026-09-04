# Cross-skill plan handoff protocol

When a plan's phases mix owner skills (e.g. Plan 0004: phases 1–4, 6 owned by `dev`, phases 5 and 7 owned by `ui-builder`), the active implementing skill **stops at the boundary, commits, and emits a structured handoff prompt** that the user pastes into a fresh sibling session. This file is the source of truth for both ends.

Both `dev` and `ui-builder` (and any future implementing sibling) read this file at session start and reference it from their SKILL.md. Do not invent ad-hoc handoff formats — the structured form is the contract.

## When the handoff fires

At the **start of each phase**, after re-anchoring on the phase block:

1. Read the phase's `**Owner skill:**` line.
2. If the owner equals the current skill (`dev` running, owner is `dev`): proceed normally.
3. If the owner is a sibling (`dev` running, owner is `ui-builder`): **do not implement the phase**. Run the handoff protocol below.
4. If the owner is `human`: that's a user task, not a sibling skill. Surface it and stop — the user picks up.

Exception: the user can override at Step 2 ("go") by explicitly authorizing in-session cross-skill work — e.g. "go, you do the ui-builder phases too." This is allowed because the user has the final word, but the active skill must **echo back the override in one sentence** before starting ("Acknowledged: I'll implement phases 1–7 in this session including the two ui-builder-owned phases.") so the decision is on the record.

## Auto-handoff vs manual handoff

Scoped exception: when the boundary is `dev` ↔ `ui-builder`, the sender invokes the sibling in-session via the Skill tool (`Skill(skill="<sibling>", args="<payload>")`) instead of stopping and emitting the prompt for the user to paste. The receiving skill still runs the abbreviated restatement and waits for the user's "go" before writing code — **auto-handoff removes the user's copy-paste step, not the gate.**

All other boundaries stay manual: `dev`/`ui-builder` → `strategy-author`, → `backtester`, → `architect`, → `skill-creator`, and the reverses. The sender emits the payload as its final message and stops; the user pastes it into a fresh `/<owner>` session.

Auto-handoff is scoped to `dev` ↔ `ui-builder` only because those two siblings pair the most in mixed-owner plans (Plans 0006, 0007, 0008) — the friction-removal pays for itself there. Other boundaries stay manual until the same volume emerges.

**Architect ↔ implementer handoffs always stay manual either way.** The "go" approval at session start and the fresh-session close-ceremony review are gates whose value comes from the fresh-context boundary; a Skill-tool invocation in the same session can't replace either. Auto-handoff is implementer ↔ implementer only.

The payload content is identical for both variants. The only difference is the transport.

## Handoff protocol — sender side

When the boundary fires:

1. **Finish and commit the last in-scope phase.** Run its done-when. Commit per convention.
2. **Verify `git status` is clean.** No uncommitted edits leak across the boundary; the receiving skill should land on a clean tree.
3. **Build the handoff payload** from the template below, filling in every bracketed slot.
4. **Route by next owner:**
   - **`ui-builder` (when sender is `dev`) or `dev` (when sender is `ui-builder`) → auto-handoff.** Announce the handoff in one line ("Phase N owned by <sibling> — handing off via /<sibling>."), then invoke `Skill(skill="<sibling>", args="<payload>")`. Once the call returns, the sender's session is done — do not loop back.
   - **`strategy-author`, `backtester`, `architect`, `skill-creator`, or any other owner → manual handoff.** Emit the payload as your final message and stop. (`architect` and `skill-creator` are owners but not implementer *siblings*; ADR-0108 keeps both boundaries manual.)
5. **Stop.** Do not start the sibling-owned phase yourself. Do not re-prompt or re-explain after emitting (manual) or after the Skill call returns (auto) — the structured prompt is self-contained.

## Handoff prompt template

The active skill emits this verbatim (filling in slots). The user copy-pastes it as the first message of a fresh `/<sibling>` session.

```
# Cross-skill plan handoff

**From:** `<current-skill>`
**To:** `<sibling-skill>`
**Plan:** [<NNNN-slug>](docs/architecture/plans/<NNNN-slug>.md)
**Boundary:** end of phase <N>, start of phase <M>

## Completed this session (commits)

- `<sha>` — phase <N1>: <one-line summary>
- `<sha>` — phase <N2>: <one-line summary>
- ...

## Remaining phases

| Phase | Owner | One-line | Status |
|-------|-------|----------|--------|
| <M>   | `<sibling-skill>` | <one-line> | **next — yours** |
| <M+1> | `<some-skill>`    | <one-line> | pending |
| ...   | ...               | ...        | ...     |

## Next phase detail (for the receiving skill)

**Phase <M> — <name>** (from plan section `### Phase <M>`):

- **What:** <copy the phase's "What" verbatim>
- **Files touched:** <copy verbatim>
- **Done when:** <copy verbatim>

## Context the receiving skill should know

- <any mid-session scope expansions the user approved>
- <any blockers surfaced and routed elsewhere>
- <anything in the completed work that affects the next phase — new file paths, schema changes, etc.>
- <nothing? say "none — phase is independent">

## Receiving-skill instructions

You are entering an in-progress plan, not starting fresh. Skip the full Step 1 restatement. Instead:

1. Confirm you've loaded the plan file at the path above.
2. Confirm the boundary: "Picking up Plan <NNNN> at phase <M> (handoff from <current-skill>). I'll implement phases <M>–<last-of-mine> this session."
3. List the phases you'll own this session (the contiguous run starting at <M> up to the next non-yours boundary).
4. Surface any ambiguity in the *next phase only* — don't re-litigate completed work.
5. Wait for the user's "go" before writing code, same as a fresh session.

When you hit the next owner boundary (or run out of phases), use this same template to hand off again — or, if the plan is complete, run the close-ceremony handoff per your SKILL.md.
```

## Handoff protocol — receiver side

The trigger is the literal heading `# Cross-skill plan handoff` at the start of the incoming message — whether it arrived via the user pasting it (manual handoff) or as the `args` of a Skill-tool invocation from a sibling (auto-handoff for `dev` ↔ `ui-builder`). Recognize either as a structured handoff (not a fresh "implement plan X" prompt):

1. **Load the plan file.** Path is in the handoff under `**Plan:**`.
2. **Read the plan in full** — TL;DR, Decision, every phase (not just yours), Related ADRs, Risks. You still need the whole-plan context; the handoff is the bridge, not the brief.
3. **Read the listed commits** to confirm the prior work landed (`git log --oneline -n <count of completed phases>` from the working tree should match the handoff's commit list).
4. **Run the receiving-skill instructions** verbatim — abbreviated restatement, no full Step 1 re-do.
5. **Wait for "go."** Same gate as a fresh session. The user may have intervening guidance, or may want to redirect.

If the handoff is malformed (missing commit list, wrong plan path, slot left bracketed): stop and ask the user to fix it. Do not guess — that's how silent scope drift starts.

## When the plan is finished

After the final phase commits, the active skill runs the **close-ceremony handoff** (per its SKILL.md Step 4), not this cross-skill handoff. The close ceremony routes to `/architect` and triggers the end-of-plan review. The two protocols don't compose — close ceremony is for the architect; this template is for sibling implementers.

## Edge cases

- **Owner tag missing.** Phase has no `**Owner skill:**` line. Stop and route to `/architect` — the plan is incomplete, and implementing under a guess silently violates the contract. Mode 4 review flags this as a blocker before the plan goes in-progress, but if you find one in flight, treat it as a plan bug.
- **Owner tag value not in vocabulary.** The vocabulary is seven values and closed (ADR-0108): `dev`, `ui-builder`, `strategy-author`, `backtester`, `architect`, `skill-creator`, `human`. Anything else — e.g. `**Owner skill:** mobile-builder` — gets the same handling: stop, route to architect to fix the plan.
- **Phase owner changes mid-phase via plan amendment.** Don't happen. ADRs and plans are append-only after going in-progress; the only edit allowed is `Status:` flips. If you genuinely need the phase ownership to change, the answer is a new plan that supersedes the old one, not an in-place edit.
- **Two consecutive phases share an owner.** Stay in-session. The handoff fires on owner *change*, not on every phase boundary.
- **User override at Step 2.** Allowed and on the record. Echo it back in one sentence; proceed across owners; do not hand off.
