# ADR-NNNN — <decision title>

> **Status:** proposed | accepted | superseded by ADR-NNNN
> **Date:** YYYY-MM-DD
> **Related plan(s):** NNNN-foo (if applicable)

## Context

What forces are at play? What constraint or tradeoff made this a *decision* (i.e., a thing that could reasonably go either way) rather than a no-brainer?

Two to four short paragraphs. Cite concrete facts: a benchmark, a deadline, a dependency, a team skill constraint. ADRs are credible because of the context, not the prose.

## Decision

One paragraph, active voice, present tense.

> We will use SQLite (via `sqlite3` from the standard library) for cached market data, with a `bars` table keyed by (symbol, timeframe, ts).

If the decision has nuance (e.g. "use X *unless* Y"), capture that here, not in a footnote.

## Consequences

### Positive
- What this unlocks. New capabilities, simplified code paths.

### Negative
- What this costs. The price we're paying. **This is the most important section — be honest.**

### Neutral
- Things that change but aren't clearly better or worse. Optional section.

## Alternatives considered

For each rejected alternative, one paragraph: what it was, the one decisive reason we rejected it. If you have more than three alternatives listed, you're probably padding — keep it to the ones that were genuinely in contention.

### Alternative A — <name>
Why rejected.

### Alternative B — <name>
Why rejected.

## Notes

Free-form. Links to benchmarks, prior discussions, prototypes. Skip if empty.
