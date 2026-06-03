# market-analyser

Desktop trading-analysis app for the user's own use. Python sidecar (FastAPI on `127.0.0.1`) + Electron + React + TypeScript renderer + SQLite cache, with a Streamable-HTTP MCP server mounted on the sidecar at `/mcp`.

**Primary control surface: Claude Code (CLI) via MCP** ([ADR-0015](docs/architecture/adrs/0015-claude-code-primary-control-surface.md)). The user drives the app by talking to an agent, which calls MCP tools on the sidecar. The Electron viewer is a live visualisation surface — it subscribes to a sidecar event stream ([ADR-0017](docs/architecture/adrs/0017-live-ui-updates-via-sse.md)) and renders agent-issued chart commands. The sidecar runs as a standalone process ([ADR-0016](docs/architecture/adrs/0016-standalone-sidecar-mode.md)): Electron auto-attaches via a lockfile if one is already running, and closing the viewer does not stop the sidecar. Data layer written in-house per [ADR-0009](docs/architecture/adrs/0009-rewrite-data-layer-in-house.md) (supersedes ADR-0003's vendoring policy).

This file is the orientation map for the project's skill ecosystem. Skills do the actual work; this file says **which skill** does which kind of work and **how they hand off**.

## Skill ecosystem

Nine skills under `.claude/skills/`. Each has its own SKILL.md + references. Trust their descriptions — Claude Code triggers them automatically. This table is the orientation, not the trigger.

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
| `safe-commit`      | The commit ceremony (explicit-path staging, gates, message file) | Any imminent commit — "commit this", "commit the phase"  |

**Hard splits to remember:**
- **TradFi vs DeFi.** Stocks/indices/futures → `market-analyst`. Pools/LPs/lending → `defi-analyst`. Never both.
- **Analyst vs backtester.** "What's the current condition" → analyst. "How would this have done historically" → backtester.
- **Author vs implementer.** Architect designs; dev/sibling skills implement. Never invert.

## Canonical workflows

### New feature (most common)

```
architect (design + plan)  →  user "go"  →  dev or sibling (implement all phases)  →  fresh architect session (close ceremony: review + status flip + move plan to plans/done/ + merge the implementation branch if one exists)
```

- One plan, one session per implementer — or, for plans the index marks disjoint, one **git worktree** per implementer running in parallel (see [plans/README § Parallel execution](docs/architecture/plans/README.md#parallel-execution)). Sibling-owned phases get routed to that sibling (per the plan's `Owner skill:` tag).
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
- **Determinism.** Same inputs → same outputs in the financially-meaningful path: no `set` iteration, no wall-clock reads, no unseeded randomness in strategy / metric / equity-curve computation. A backtest re-run from `spec.json` produces a `result.json` that is byte-identical **modulo run provenance** — `run_id`, `started_at`, and `finished_at` are the documented exceptions ([ADR-0018](docs/architecture/adrs/0018-backtest-result-schema.md); the engine's golden test pins `model_dump(exclude={"run_id", "started_at", "finished_at"})` equality cross-process).
- **No secrets in code or logs.** Bearer tokens, API keys, the IPC per-launch secret — never persisted, never logged. `secrets.json` (when it exists) lives outside the repo.
- **Security defaults are not optional.** Electron renderer: `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`, double-CSP. Renderer never imports Node, never reaches the network except via the typed sidecar fetch client (which injects the bearer).
- **Validate at boundaries.** Pydantic for sidecar inputs, Zod for IPC payloads, typed responses for sidecar HTTP. Don't validate again inside trusted code paths.
- **Conditions are facts, decisions are the user's.** Analyst skills (`market-analyst`, `defi-analyst`) report conditions; they never recommend buy/sell/exit/rebalance.
- **Commit hygiene under concurrency.** Parallel sessions share one working tree, so stage only the files you changed, by explicit path — **never `git add -A` / `.` / `--all` / `:/`** (a `PreToolUse` hook denies broad staging). `git status` first; never stage, stash, or `checkout` another session's in-progress files. Never rewrite history (no amend/rebase/reset), never push. The `/safe-commit` skill is the ceremony that encodes all of this.

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

## Dependency discipline

Two policies, both tracked in git, both applied to PyPI (via `uv`) and the npm registry (via `pnpm`):

- **Cooldown.** Package versions younger than **14 days** are refused at resolution time. Tracked as `[tool.uv] exclude-newer = "YYYY-MM-DD"` in `pyproject.toml` and `minimumReleaseAge: 20160` (minutes) in `pnpm-workspace.yaml`. See [ADR-0012](docs/architecture/adrs/0012-dependency-cooldown.md).
- **Exact pinning.** Every direct dependency in `pyproject.toml` and `desktop/package.json` is `==X.Y.Z` (Python) or `X.Y.Z` (Node). No `>=`, no `^`, no `~`. Dev tooling is pinned with the same rule as runtime deps. See [ADR-0013](docs/architecture/adrs/0013-pin-direct-dependencies.md).

### Operational handles

- **Weekly cutoff bump.** Every ~7 days, advance `exclude-newer` by ~7 days and update `minimumReleaseAge`'s effective floor as a regular chore commit. The cooldown is meant to lag, not stall — let it drift and ordinary dependency-update work starts failing for unrelated reasons.
- **CVE-driven bump.** When a security patch lands inside the cooldown window, bump the relevant cutoff past the patch's publish date, run `uv lock` and/or `pnpm install`, and land manifest + lockfile in a **single commit** whose message names the CVE.
- **Every direct-dep upgrade is a manifest edit.** Bumping `fastapi` from `0.136.1` to `0.137.0` is a one-line `pyproject.toml` change plus `uv lock` plus a commit. `uv lock --upgrade` against a `>=` range is not how upgrades happen here. Same for `pnpm`: edit `desktop/package.json`, then `pnpm install`, both in the same commit.

### Non-negotiables

- **No per-package cooldown allowlist.** `pnpm`'s `minimumReleaseAgeExclude` and any hypothetical per-package exemption mechanism are off the table. ADR-0012 explains why: an allowlist becomes a quietly-growing exception register that undermines the audit property the policy exists to provide.
- **No range operators in manifests.** Not in runtime deps, not in dev tooling, no `~=`/`^`/`>=` exceptions. ADR-0013 explains why we pin both groups under the same rule.

## Pitfalls to avoid

- **Don't skip the architect for cross-cutting decisions.** New IPC channel, new dependency, CSP change, new ADR-shaped question → architect, even if it feels like a small edit.
- **Don't fabricate when blocked.** The analyst, backtester, and ui-builder skills are explicit about this: if the data/engine/shell isn't there, surface it; don't paper over.
- **Don't ad-hoc shared infrastructure in a single skill.** Pattern detectors, indicator math, the BacktestResult schema — these get consumed by multiple skills, so they live in `src/`, not in a one-off script. Architect designs, dev implements.
- **Don't push.** Implementers commit; the user pushes. CI runs on push and tag. Auto-update is deferred to a future packaging plan.
- **Don't bypass the typed fetch client** in the renderer. Every sidecar call goes through `desktop/renderer/api/client.ts` so the bearer token is injected once.

## When the project state has moved

Skill `references/project-context.md` files describe state at the time they were written. Reality moves faster. If a skill's docs claim a module exists and you can't find it (or vice versa), trust `Glob` over the docs and surface the drift. The skill-creator workflow has eval tooling to catch this; routine drift gets fixed lazily as it's noticed.
