# Plans

Implementation plans for `market-analyser`. Each plan is one file (`NNNN-<slug>.md`), authored by `architect` and implemented by the sibling skill(s) named on each phase. Completed plans live in [`done/`](done) — the architect moves a plan there as part of the close ceremony, never the implementer.

## Active roster

| #    | File                                                          | Status         | Summary |
|------|---------------------------------------------------------------|----------------|---------|
| 0002 | [0002-strategy-interface](0002-strategy-interface.md)         | draft          | Strategy contract module (`Signal`, `Params`, `META`, `StrategyProtocol`) + RSI reference + signals-to-trades adapter + 5 reference strategies + `strategies list` CLI. Three skill boundaries. |
| 0005 | [0005-dependency-cooldown](0005-dependency-cooldown.md)       | draft          | Dependency discipline pair: (a) 14-day minimum release age via `[tool.uv] exclude-newer` + pnpm `minimum-release-age`, and (b) every direct dep in `pyproject.toml` / `desktop/package.json` rewritten from `>=` / `^` ranges to exact `==X.Y.Z` pins. Single-skill plan (all `dev`, 5 phases). See [ADR-0012](../adrs/0012-dependency-cooldown.md) + [ADR-0013](../adrs/0013-pin-direct-dependencies.md). Independent of 0002. |
| 0006 | [0006-annotations-via-mcp](0006-annotations-via-mcp.md)       | approved       | Mount MCP server (Streamable HTTP, rev 2025-03-26) on the existing sidecar at `/mcp`, sharing the renderer's port with its own long-lived secret in `mcp-secret.json`. Three MCP tools (`get_ohlcv`, `write_annotation`, `list_annotations`), a new `annotations` SQLite table, Settings page to surface + rotate the MCP secret, and chart-marker rendering via 1 Hz polling. Six phases, mixed `dev` + `ui-builder`. See [ADR-0014](../adrs/0014-mcp-as-second-sidecar-protocol.md). Depends on Plan 0005 for the `mcp` SDK pin if 0005 lands first. |

## Recently closed

| #    | File                                                                            | Closed     | Summary |
|------|---------------------------------------------------------------------------------|------------|---------|
| 0001 | [0001-bootstrap](done/0001-bootstrap.md)                                        | 2026-05-18 | Walking-skeleton Electron + Python-sidecar bootstrap with OHLCV chart for one symbol. Phases 1–5 + 4.1 shipped; closed after Plan 0004 landed. |
| 0003 | [0003-excise-vendored-upstream](done/0003-excise-vendored-upstream.md)          | 2026-05-19 | Rewrote the Yahoo OHLCV fetch in-house (`data/adapters/_yahoo_fetch.py`), deleted `data/vendored/` and `vendored.lock`, scrubbed `tradingview-mcp` mentions across `docs/`, `CLAUDE.md`, and the (gitignored) skills tree. Implementation shipped in commits `2337ee6`, `1df1be0`, `ae099e4`, `def5e08`; closed cleanly with one minor finding (done-when grep allow-list narrower than the substantive ADR append-only policy — body retentions in ADR-0004 and ADR-0007 are intentional). |
| 0004 | [0004-bootstrap-review-followups](done/0004-bootstrap-review-followups.md)      | 2026-05-18 | Cleared the architect-review deltas from Plan 0001 — silent cache truncation, post-restart 401, supervisor-spec stub, missing CSP-block test, secret-out-of-argv (now [ADR-0011](../adrs/0011-bearer-secret-transport.md)), renderer DX cluster, OhlcvView empty-state affordance. |

## Recommended execution order

Plan 0003 closed on 2026-05-19; the post-vendoring baseline Plan 0002 was waiting on is now in place. Plan 0005 (dependency discipline — cooldown + exact pins) and Plan 0006 (annotations via MCP) are both unblocked. Plan 0005 should land first because Plan 0006's phase 1 adds the `mcp` Python SDK as a new dependency, and that addition is cheaper to do under an already-enforced pinning policy than to retrofit. Plan 0002's `contracts/` module will be consumed by every plan that follows, so it stays parked until the dependency baseline and the MCP foundation are both in place.

Execution sequence:

```
1.  /dev          Plan 0005          (dev session — single-skill, 5 phases, independent)
2.  /architect    close Plan 0005    (fresh architect session)
3.  /dev or       Plan 0006          (mixed-skill: dev phases 1–4, ui-builder phases 5–6 —
    /ui-builder                       hand off at phase 5 per the cross-skill protocol)
4.  /architect    close Plan 0006    (fresh architect session)
5.  ...           Plan 0002          (sequence at draft-approval time; three skill boundaries)
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

- **Numbering** is sequential and zero-padded to four digits. Next free number is **0007**. ADR numbers are an independent sequence (see [`../adrs/`](../adrs/)). Architect runs `Glob docs/architecture/plans/*.md` before drafting to pick the next number, never trusting memory.
- **One plan per file.** No "Plan 0004a" / "Plan 0004b" splits — if the work grows, write a new numbered plan and reference the parent.
- **Plans aren't ADRs.** A plan says *what we're building this week and how*; an ADR says *why we chose this design over the alternatives*. Plans expire; ADRs don't. If a plan's decision warrants permanent capture, the architect also writes an ADR (Mode 2).
- **Plans don't move until the architect's close ceremony.** Implementers commit per phase but never `git mv` a plan to `done/`. The close ceremony reviews the whole plan in one pass, then flips status + moves the file in a single architect-authored commit.
- **In-progress plans are append-only on substance.** The only mid-flight edit is the `Status:` line and minor honesty fixes (e.g. correcting a stale owner tag). Structural amendments — adding phases, rewriting done-when — happen via a new followup plan, not in-place.
- **Cross-references stay link-shaped.** When one plan references another's phase, use a markdown link (`[Plan 0004 phase 7](0004-...md)`) so the cross-ref survives renumbering and the close-ceremony move to `done/`.

## When you don't know which plan to start

Don't guess. The execution sequence above is the source of truth as of 2026-05-19 (Plans 0001 + 0003 + 0004 closed; Plan 0005 next, then Plan 0006, Plan 0002 still parked). If reality has drifted (the user names a plan not in that sequence, or a status disagrees with a recent commit), trust `git log` and the plan's own `Status:` line over this README — and surface the drift so the README gets refreshed.
