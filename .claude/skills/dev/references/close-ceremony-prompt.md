# Close-ceremony prompt template

The message you send the user at the end of Step 4, **after the final phase of the plan is implemented and committed**. Its purpose is to (a) confirm the plan landed, (b) hand a clear, complete brief to the architect skill, and (c) get the user to start a fresh session — not continue in this one — because architect needs a clean context window for the review.

## Why a fresh session

The architect's review pass is qualitatively different from implementation: it compares code against plan + ADRs, checks for layering violations, looks for security/data-integrity gaps across the whole plan's worth of changes. That's hard to do well in a context already full of implementation reasoning, file reads, and tool output. A fresh session forces architect to re-read the plan and the code with fresh eyes — exactly the perspective a reviewer needs.

## The template

Replace `<…>` placeholders with the plan's actuals. Keep the structure; the architect skill is tuned to receive briefs in this shape.

```
Plan implemented and committed. Ready for the architect close ceremony.

**Plan:** <plan-number> — <plan-title>  (`docs/architecture/plans/<NNNN-slug>.md`)
**Phases shipped:** <count> (<phase-1-name>, <phase-2-name>, …)
**Commits made this session:**
<paste output of: git log --oneline -n <count>>

**Done-when results (final phase):**
- [<pass|fail>] <criterion 1 verbatim from the plan>
- [<pass|fail>] <criterion 2 verbatim from the plan>
- ...

**Notes for architect** *(optional — include only if relevant)*:
- <any deviation from the plan and why, with user approval noted>
- <any underspecified spot you filled in with a judgment call>
- <any followups you noticed but didn't act on>

---

**Next step:** start a fresh session and invoke `/architect` with this brief. Architect will:

1. Review the whole plan against the ADRs and deliver the review **in-conversation** (no review file).
2. Flip the plan's `Status:` line to `done`.
3. Move the plan file to `docs/architecture/plans/done/<NNNN-slug>.md` (creating the directory if it doesn't exist).

After the review, you can push the commits when you're ready.
```

## Guidance on filling it in

- **Plan identifier**: pull verbatim from the plan header. Don't paraphrase the title.
- **Phases shipped**: the count and one-line list lets the architect orient quickly. Use the phase names from the plan headings.
- **Commits made this session**: run `git log --oneline -n <N>` where `<N>` is the number of commits you just made (typically one per phase, sometimes more), paste the output. The user wants to scan the log before they push.
- **Done-when results**: copy each criterion from the **final phase's** done-when list, prefix with `[pass]` or `[fail]`. If anything is `[fail]`, you shouldn't be at Step 4 in the first place — go back and fix it, or escalate. The only legitimate non-pass is "[skipped — explicitly approved by user]" with a short reason. If earlier phases had failures, surface them in Notes.
- **Notes for architect**: keep it short. The architect re-reads the plan + ADRs + code; they don't need a recap of what you did. They need the *deltas* — what's not in the code that the plan implies, or what's in the code that the plan didn't anticipate.

## What NOT to include

- Don't paste the full diff. The architect reads files; that's their job.
- Don't write a self-review ("I think this looks good"). The architect will judge.
- Don't include reasoning you've already had with the user this session. The fresh session is fresh; the brief is the bridge.
- Don't include secrets, tokens, or anything from `.env` in commit messages, the brief, or any artifact.
