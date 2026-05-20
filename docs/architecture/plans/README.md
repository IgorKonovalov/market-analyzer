# Plans

Implementation plans for `market-analyser`. Each plan is one file (`NNNN-<slug>.md`), authored by `architect` and implemented by the sibling skill(s) named on each phase. Completed plans live in [`done/`](done) — the architect moves a plan there as part of the close ceremony, never the implementer.

## Active roster

| #    | File                                                          | Status         | Summary |
|------|---------------------------------------------------------------|----------------|---------|
| 0002 | [0002-strategy-interface](0002-strategy-interface.md)         | approved       | Strategy contract module (`Signal`, `Params`, `META`, `StrategyProtocol`) + RSI reference + signals-to-trades adapter + `Trade` type + 5 reference strategies + `strategies list` CLI. Three skill boundaries. Reframed 2026-05-19 at approval: phase 3 narrowed to adapter only; engine + metrics + `BacktestResult` punted to follow-up. |

## Recently closed

| #    | File                                                                            | Closed     | Summary |
|------|---------------------------------------------------------------------------------|------------|---------|
| 0001 | [0001-bootstrap](done/0001-bootstrap.md)                                        | 2026-05-18 | Walking-skeleton Electron + Python-sidecar bootstrap with OHLCV chart for one symbol. Phases 1–5 + 4.1 shipped; closed after Plan 0004 landed. |
| 0003 | [0003-excise-vendored-upstream](done/0003-excise-vendored-upstream.md)          | 2026-05-19 | Rewrote the Yahoo OHLCV fetch in-house (`data/adapters/_yahoo_fetch.py`), deleted `data/vendored/` and `vendored.lock`, scrubbed `tradingview-mcp` mentions across `docs/`, `CLAUDE.md`, and the (gitignored) skills tree. Implementation shipped in commits `2337ee6`, `1df1be0`, `ae099e4`, `def5e08`; closed cleanly with one minor finding (done-when grep allow-list narrower than the substantive ADR append-only policy — body retentions in ADR-0004 and ADR-0007 are intentional). |
| 0004 | [0004-bootstrap-review-followups](done/0004-bootstrap-review-followups.md)      | 2026-05-18 | Cleared the architect-review deltas from Plan 0001 — silent cache truncation, post-restart 401, supervisor-spec stub, missing CSP-block test, secret-out-of-argv (now [ADR-0011](../adrs/0011-bearer-secret-transport.md)), renderer DX cluster, OhlcvView empty-state affordance. |
| 0005 | [0005-dependency-cooldown](done/0005-dependency-cooldown.md)                    | 2026-05-19 | Landed the dependency-discipline pair: `[tool.uv] exclude-newer = "2026-05-05"` + `minimumReleaseAge: 20160` in `pnpm-workspace.yaml` (cooldown; ADR-0012), and every direct dep in `pyproject.toml` + `desktop/package.json` rewritten to exact `==X.Y.Z` / `X.Y.Z` pins (ADR-0013). User-authorized single-commit landing; phase-1 corrected ADR-0012's mechanism (kebab-case in `.npmrc` → camelCase in `pnpm-workspace.yaml`) and bumped CI pnpm 9 → 11.1.2. Followups captured in the plan body. |
| 0006 | [0006-annotations-via-mcp](done/0006-annotations-via-mcp.md)                    | 2026-05-20 | Mounted MCP server (Streamable HTTP, rev 2025-03-26) on the sidecar at `/mcp` with its own long-lived `mcp-secret.json`. Three MCP tools (`get_ohlcv`, `write_annotation`, `list_annotations`) + `annotations` SQLite table + Settings page (reveal/copy/rotate) + 1 Hz chart-marker polling. Six phases, mixed `dev` + `ui-builder`. Two prior-review followups (CI guard + `.gitignore` for `mcp-secret*.json`) shipped before close; two new followups carried in the plan body (`get_ohlcv` timeframe validation; stale bootstrap-component-map schema). See [ADR-0014](../adrs/0014-mcp-as-second-sidecar-protocol.md). |

## Recommended execution order

Plan 0006 closed on 2026-05-20, putting the MCP server, annotations table, Settings page, and chart-marker polling in place. Plan 0002 (strategy interface) is unblocked and is next — its `contracts/` module is consumed by every plan that follows, and the three skill handoffs (`dev` → `backtester` → `strategy-author` → `dev`) were deliberately preserved at approval as cheap integration checks.

Execution sequence:

```
1.  /dev          Plan 0002          (mixed-skill: dev → backtester → strategy-author →
                                      dev; hand off at each owner boundary)
2.  /architect    close Plan 0002    (fresh architect session)
```

Plan 0002 keeps three skill handoffs (`dev` → `backtester` → `strategy-author` → `dev`). At approval (2026-05-19) the architect considered collapsing to two — either by moving phase 5 (CLI) ahead of phase 4, or by making strategy-author phase 4 tests compare signal lists instead of trade lists. Both options were rejected: phase 5's done-when (six rows printed by `strategies list`) is the integration check that proves discovery + contract + CLI work together, and phase 4's done-when (trade list matches reference byte-for-byte after `signals_to_trades`) is the integration check that proves the contract round-trips through the adapter. Cheap handoffs at clean owner boundaries are worth preserving over fewer-but-weaker acceptance criteria.

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

Don't guess. The execution sequence above is the source of truth as of 2026-05-20 (Plans 0001 + 0003 + 0004 + 0005 + 0006 closed; Plan 0002 next). If reality has drifted (the user names a plan not in that sequence, or a status disagrees with a recent commit), trust `git log` and the plan's own `Status:` line over this README — and surface the drift so the README gets refreshed.
