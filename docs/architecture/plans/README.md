# Plans

Implementation plans for `market-analyser`. Each plan is one file (`NNNN-<slug>.md`), authored by `architect` and implemented by the sibling skill(s) named on each phase. Completed plans live in [`done/`](done) — the architect moves a plan there as part of the close ceremony, never the implementer.

## Active roster

| #    | File                                                          | Status         | Summary |
|------|---------------------------------------------------------------|----------------|---------|
| 0002 | [0002-strategy-interface](0002-strategy-interface.md)         | draft          | Strategy contract module (`Signal`, `Params`, `META`, `StrategyProtocol`) + RSI reference + signals-to-trades adapter + port of 5 vendored strategies + `strategies list` CLI. Three skill boundaries. |
| 0003 | [0003-excise-vendored-upstream](0003-excise-vendored-upstream.md) | draft      | Rewrite the Yahoo OHLCV fetch in-house, delete `data/vendored/` and `vendored.lock`, scrub references across docs/skills. Single-skill plan (all `dev`). Unblocked by the 2026-05-18 close of Plans 0001 + 0004. |

## Recently closed

| #    | File                                                                            | Closed     | Summary |
|------|---------------------------------------------------------------------------------|------------|---------|
| 0001 | [0001-bootstrap](done/0001-bootstrap.md)                                        | 2026-05-18 | Walking-skeleton Electron + Python-sidecar bootstrap with OHLCV chart for one symbol. Phases 1–5 + 4.1 shipped; closed after Plan 0004 landed. |
| 0004 | [0004-bootstrap-review-followups](done/0004-bootstrap-review-followups.md)      | 2026-05-18 | Cleared the architect-review deltas from Plan 0001 — silent cache truncation, post-restart 401, supervisor-spec stub, missing CSP-block test, secret-out-of-argv (now [ADR-0011](../adrs/0011-bearer-secret-transport.md)), renderer DX cluster, OhlcvView empty-state affordance. |

## Recommended execution order

With Plans 0001 and 0004 closed on 2026-05-18, Plan 0003 is unblocked. Plan 0002 stays parked until the data-layer rewrite (0003) settles, because Plan 0002's `contracts/` module will be consumed by every plan that follows and benefits from a stable, post-vendoring baseline.

Execution sequence:

```
1.  /dev          Plan 0003          (dev session — single-skill plan)
2.  /architect    close Plan 0003    (fresh architect session)
3.  ...           Plan 0002          (sequence at draft-approval time; three skill boundaries)
```

Plan 0002 has three skill handoffs (`dev` → `backtester` → `strategy-author` → `dev`); consider whether reordering to two could be useful at draft-approval time.

## Status vocabulary

| Status                              | Meaning |
|-------------------------------------|---------|
| `draft`                             | Author wrote it; no user "go" yet. Implementers ignore. |
| `approved`                          | User signed off at the interview's end. Implementers may pick up. |
| `in-progress`                       | An implementing skill flipped it at Step 2 of its session. |
| `implementation complete — pending …` | All phases shipped; close ceremony blocked on a named followup plan or unresolved review delta. |
| `done`                              | Architect close ceremony fired; plan file lives in `done/`. |
| `abandoned`                         | User killed it before completion. Stays in this directory for the record. |
| `superseded by NNNN`                | A later plan replaced this one. (Rare — usually plans cleanly close.) |

Only `architect` and the implementing skill at Step 2 are allowed to mutate `Status:`. Implementers flip `draft → in-progress`; architect handles every other transition.

## Owner-skill vocabulary (per phase)

Each phase carries `**Owner skill:**` with exactly one value from the fixed set, backticked:

- `` `dev` `` — Python sidecar code, persistence, CI, tooling, Electron shell phases that aren't UI.
- `` `ui-builder` `` — anything under `desktop/`.
- `` `strategy-author` `` — strategies in `src/market_analyser/strategies/`.
- `` `backtester` `` — engine and run artifacts in `src/market_analyser/backtest/` and `runs/`.
- `` `human` `` — user-only task (rare; reserved for things Claude shouldn't touch).

Plans with mixed-owner phases hand off at every boundary per the [cross-skill handoff protocol](../../../.claude/skills/architect/references/templates/cross-skill-handoff.md). Missing or ambiguous tags fail Mode 4 review as blockers.

## Conventions

- **Numbering** is sequential and zero-padded to four digits. Next free number is **0005**. ADR numbers are an independent sequence (see [`../adrs/`](../adrs/)). Architect runs `Glob docs/architecture/plans/*.md` before drafting to pick the next number, never trusting memory.
- **One plan per file.** No "Plan 0004a" / "Plan 0004b" splits — if the work grows, write a new numbered plan and reference the parent.
- **Plans aren't ADRs.** A plan says *what we're building this week and how*; an ADR says *why we chose this design over the alternatives*. Plans expire; ADRs don't. If a plan's decision warrants permanent capture, the architect also writes an ADR (Mode 2).
- **Plans don't move until the architect's close ceremony.** Implementers commit per phase but never `git mv` a plan to `done/`. The close ceremony reviews the whole plan in one pass, then flips status + moves the file in a single architect-authored commit.
- **In-progress plans are append-only on substance.** The only mid-flight edit is the `Status:` line and minor honesty fixes (e.g. correcting a stale owner tag). Structural amendments — adding phases, rewriting done-when — happen via a new followup plan, not in-place.
- **Cross-references stay link-shaped.** When one plan references another's phase, use a markdown link (`[Plan 0004 phase 7](0004-...md)`) so the cross-ref survives renumbering and the close-ceremony move to `done/`.

## When you don't know which plan to start

Don't guess. The execution sequence above is the source of truth as of 2026-05-18 (Plans 0001 + 0004 closed; Plan 0003 next). If reality has drifted (the user names a plan not in that sequence, or a status disagrees with a recent commit), trust `git log` and the plan's own `Status:` line over this README — and surface the drift so the README gets refreshed.
