# market-analyser

Desktop trading-analysis app. Python sidecar (FastAPI on localhost) + Electron + React + TypeScript renderer + SQLite cache. Data layer written in-house per ADR-0009 (supersedes ADR-0003's vendoring policy).

This file is the orientation map for the project's skill ecosystem. Skills do the actual work; this file says **which skill** does which kind of work and **how they hand off**.

## Skill ecosystem

Eight skills under `.claude/skills/`. Each has its own SKILL.md + references. Trust their descriptions — Claude Code triggers them automatically. This table is the orientation, not the trigger.

| Skill              | Owns                                            | Triggers on                                                       |
|--------------------|-------------------------------------------------|-------------------------------------------------------------------|
| `architect`        | `docs/architecture/` (ADRs, plans, diagrams)    | Design questions, "should we...", new ADRs, plan authoring, reviews |
| `dev`              | Python sidecar + tooling + CI                   | "Implement plan N", "do phase X", architect-authored work         |
| `strategy-author`  | `src/market_analyser/strategies/`               | Writing/editing/porting trading strategies                        |
| `backtester`       | `src/market_analyser/backtest/`, `runs/`        | Running backtests, computing metrics, building the engine         |
| `ui-builder`       | `desktop/`                                      | React views, charts, Electron shell, IPC, renderer plumbing       |
| `market-analyst`   | Read-only TradFi analysis → `runs/analysis/`    | Candlestick scans, trend/momentum snapshots, screeners            |
| `defi-analyst`     | Read-only DeFi analysis → `runs/defi/`          | Pool screens, LP positions, lending health, on-chain audits       |
| `skill-creator`    | `.claude/skills/`                               | Creating, editing, or measuring skills (meta)                     |

**Hard splits to remember:**
- **TradFi vs DeFi.** Stocks/indices/futures → `market-analyst`. Pools/LPs/lending → `defi-analyst`. Never both.
- **Analyst vs backtester.** "What's the current condition" → analyst. "How would this have done historically" → backtester.
- **Author vs implementer.** Architect designs; dev/sibling skills implement. Never invert.

## Canonical workflows

### New feature (most common)

```
architect (design + plan)  →  user "go"  →  dev or sibling (implement all phases)  →  fresh architect session (close ceremony: review + status flip + move plan to plans/done/)
```

- One plan, one session per implementer. Sibling-owned phases get routed to that sibling (per the plan's `Owner skill:` tag).
- The implementer never reviews their own work or moves plans to `done/`. That's architect's close ceremony.
- Implementer commits per phase (conventional-commit) but never pushes.

### Bug fix / small refactor (no architect needed)

Direct implementation, conventional-commit, no plan, no review. If the fix reveals an ADR is wrong → escalate to architect.

### Trading work (strategy / analysis / backtest)

- **Idea-stage** ("what should I watch", "what's the regime"): `market-analyst` Mode 4 (brainstorm) — no code, no fetches.
- **Want a real read on a symbol**: `market-analyst` Mode 1 (pattern scan) or Mode 2 (snapshot). Reads cached bars.
- **Want to encode it**: `strategy-author` writes the strategy module.
- **Want to test it**: `backtester` runs against historical bars.
- **Want to see it in the app**: `ui-builder` renders the result.

### When you don't know which skill

Don't guess. Read the skill descriptions in `.claude/skills/*/SKILL.md` frontmatter, or ask. Most of the time the user's phrasing routes correctly via descriptions; the wrong skill speaking up is usually a description-tuning bug, not an orchestration bug.

## Cross-cutting non-negotiables

These apply to **every** skill that writes code or analysis in this repo.

- **No lookahead bias.** A decision at bar `i` only sees data from `bars[0..=i]`. Backtests, strategies, analyses — same rule. Indicators must be trailing, not centered.
- **Determinism.** Same inputs → same outputs, byte-identical. No `set` iteration, no wall-clock reads, no unseeded randomness. Backtests should re-run from `spec.json` and produce identical `result.json`.
- **No secrets in code or logs.** Bearer tokens, API keys, the IPC per-launch secret — never persisted, never logged. `secrets.json` (when it exists) lives outside the repo.
- **Security defaults are not optional.** Electron renderer: `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`, double-CSP. Renderer never imports Node, never reaches the network except via the typed sidecar fetch client (which injects the bearer).
- **Validate at boundaries.** Pydantic for sidecar inputs, Zod for IPC payloads, typed responses for sidecar HTTP. Don't validate again inside trusted code paths.
- **Conditions are facts, decisions are the user's.** Analyst skills (`market-analyst`, `defi-analyst`) report conditions; they never recommend buy/sell/exit/rebalance.

## Where things live

```
docs/architecture/
├── adrs/         # NNNN-<slug>.md — accepted/superseded decisions
├── plans/        # NNNN-<slug>.md — drafts and in-progress
│   └── done/     # architect moves them here after close ceremony
├── diagrams/     # mermaid in standalone .md files
└── roadmap.md    # aspirational direction — not committed scope

src/market_analyser/
├── api/          # FastAPI app + routes — dev owns
├── data/         # MarketDataProvider Protocol, adapters — dev owns
├── persistence/  # SQLite + Alembic + repositories — dev owns
├── strategies/   # strategy-author owns
├── backtest/     # backtester owns
└── analysis/     # market-analyst's deps (patterns, indicators surface — to be authored)

desktop/          # ui-builder owns end-to-end (Electron main + preload + React renderer)

runs/             # gitignored — backtest, analysis, defi artifacts
positions/        # gitignored — defi-analyst's positions.yaml (sensitive)
```

ADRs that gate frequent decisions:
- **ADR-0002** — IPC over localhost HTTP with bearer auth
- **ADR-0004** — strategy interface (pure function + pydantic Params)
- **ADR-0005** — why Electron (supersedes ADR-0001's Tauri pick)
- **ADR-0006** — persistence layout (SQLite + config.json)
- **ADR-0007** — MarketDataProvider Protocol
- **ADR-0008** — Electron shell conventions (build pipeline, IPC discipline, CSP, packaging)
- **ADR-0009** — data layer written in-house (supersedes ADR-0003)

## Pitfalls to avoid

- **Don't skip the architect for cross-cutting decisions.** New IPC channel, new dependency, CSP change, new ADR-shaped question → architect, even if it feels like a small edit.
- **Don't fabricate when blocked.** The analyst, backtester, and ui-builder skills are explicit about this: if the data/engine/shell isn't there, surface it; don't paper over.
- **Don't ad-hoc shared infrastructure in a single skill.** Pattern detectors, indicator math, the BacktestResult schema — these get consumed by multiple skills, so they live in `src/`, not in a one-off script. Architect designs, dev implements.
- **Don't push.** Implementers commit; the user pushes. CI runs on push and tag. Auto-update is deferred to a future packaging plan.
- **Don't bypass the typed fetch client** in the renderer. Every sidecar call goes through `desktop/renderer/api/client.ts` so the bearer token is injected once.

## When the project state has moved

Skill `references/project-context.md` files describe state at the time they were written. Reality moves faster. If a skill's docs claim a module exists and you can't find it (or vice versa), trust `Glob` over the docs and surface the drift. The skill-creator workflow has eval tooling to catch this; routine drift gets fixed lazily as it's noticed.
