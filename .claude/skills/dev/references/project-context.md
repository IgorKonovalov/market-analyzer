# Project context — dev

The dev-specific view of the `market-analyser` project. This is what you need to actually execute plans (every phase of them, in one session). For architecture, ADR rationale, and the running ADR list, see `.claude/skills/architect/references/project-context.md` (the architect's view is the source of truth).

## Repo root

```
<repo-root>/market-analyser
```

Top-level layout (target state — what exists right now depends on which plans/phases have shipped):

```
market-analyser/
├── pyproject.toml             # Python project, uv-managed
├── uv.lock
├── .pre-commit-config.yaml
├── .github/workflows/         # ci.yml, release.yml
├── docs/architecture/
│   ├── plans/                 # implementation plans (NNNN-<slug>.md)
│   │   └── done/              # completed plans live here (architect moves them)
│   ├── adrs/                  # architecture decisions
│   └── diagrams/              # mermaid in standalone .md files
├── desktop/                   # Electron + React + TS shell (owned by ui-builder)
├── src/market_analyser/       # Python sidecar package
│   ├── api/                   # FastAPI app and routes
│   ├── data/                  # MarketDataProvider, adapters
│   ├── persistence/           # SQLAlchemy models, repositories, migrations
│   ├── strategies/            # owned by strategy-author
│   ├── backtest/              # owned by backtester
│   └── contracts/             # shared types (Bar, Signal, StrategyMeta, ...)
└── tests/                     # pytest tests, mirroring src/market_analyser/
```

Not everything above exists yet. Always check `Glob` before you assume a directory is there — if a phase creates `desktop/` for the first time, you'll create the directory tree as part of implementing that phase.

## Sibling-skill ownership map

When a plan phase tags an `Owner skill` other than `human`/`dev`, the natural implementer is the sibling skill. You can still implement (the user has final say), but raise the flag.

| Owner skill       | Code area                                       | What it knows that you don't                                  |
|-------------------|-------------------------------------------------|----------------------------------------------------------------|
| `strategy-author` | `src/market_analyser/strategies/`               | Strategy contract (ADR-0004), lookahead/determinism patterns, indicator usage |
| `backtester`      | `src/market_analyser/backtest/`                 | Backtest engine internals, run-result schema, equity-curve math, persistence of runs |
| `ui-builder`      | `desktop/`                                      | Electron security defaults (ADR-0008), React renderer patterns, `lightweight-charts` quirks, sidecar IPC discipline |
| `human` / `dev`   | Everything else (API, data layer, persistence, tooling, CI, vendoring) | — |

When in doubt, the plan's `Owner skill` field is authoritative. When the field says `human`, that's you.

## Canonical commands

These are the commands plans typically call out in done-when criteria. Use exactly these — don't substitute `pip` for `uv`, or `npm` for `pnpm`, unless a plan tells you to.

### Python (sidecar)

| Task                      | Command                                              |
|---------------------------|------------------------------------------------------|
| Install / sync env        | `uv sync`                                            |
| Add a runtime dep         | `uv add <package>`                                   |
| Add a dev dep             | `uv add --dev <package>`                             |
| Run the sidecar (dev)     | `uv run python -m market_analyser.api --port=0 --secret=test` |
| Run tests                 | `uv run pytest`                                      |
| Run tests with coverage   | `uv run pytest --cov=src --cov-fail-under=85`        |
| Lint                      | `uv run ruff check`                                  |
| Format check              | `uv run ruff format --check`                         |
| Apply formatting          | `uv run ruff format`                                 |
| Type-check (strict)       | `uv run mypy --strict src tests`                     |
| Security audit            | `uv run pip-audit`                                   |
| Network-marked tests      | `uv run pytest -m network`  *(skipped in CI by default)* |

### Desktop (Electron, ui-builder territory)

Run from `desktop/` — pnpm is the package manager per ADR-0008.

| Task                  | Command                                  |
|-----------------------|------------------------------------------|
| Install deps          | `pnpm install`                           |
| Dev (run app)         | `pnpm --filter desktop dev`              |
| Build all             | `pnpm --filter desktop build`            |
| Unit tests            | `pnpm --filter desktop test`             |
| Main-process tests    | `pnpm --filter desktop test:main`        |
| All tests (incl. e2e) | `pnpm --filter desktop test:all`         |
| Type-check (4 configs)| `pnpm --filter desktop typecheck`        |
| Lint                  | `pnpm --filter desktop lint`             |
| Package (Windows)     | `pnpm --filter desktop package:win`      |

### Pre-commit and git

| Task                          | Command                                          |
|-------------------------------|--------------------------------------------------|
| Install hooks (one-time)      | `pre-commit install`                             |
| Run hooks on staged files     | `pre-commit run`                                 |
| Run hooks on all files        | `pre-commit run --all-files`                     |
| Conventional-commit lint      | enforced by `commitizen` via the commit-msg hook |

**Never** pass `--no-verify` or `--no-gpg-sign` to git commands. If a hook fails, the cause is the issue, not the hook.

## Plan numbering & file naming

- Plans: `docs/architecture/plans/NNNN-<slug>.md` — zero-padded 4-digit sequence.
- ADRs: `docs/architecture/adrs/NNNN-<slug>.md` — independent sequence.
- Reviews: in-conversation only (no file). There is no `docs/architecture/reviews/` directory; don't create one.
- Completed plans: `docs/architecture/plans/done/NNNN-<slug>.md` — architect moves them here after the last phase ships; you don't touch this.

## The plan's status field

Every plan opens with a status line:

```
> **Status:** draft | in-progress | done | abandoned
```

Your only allowed plan edit is flipping `draft` → `in-progress` when the user opens the Step 2 gate. Everything else (especially `done`) is the architect's call during the close ceremony.

## Where to escalate

When you're stuck, the escalation paths are:

| Situation                                                    | Where to go                                                 |
|--------------------------------------------------------------|-------------------------------------------------------------|
| Plan is wrong / contradicts reality                          | Stop, surface to user, suggest `/architect` for plan update |
| ADR is wrong / needs supersession                            | Stop, surface to user, suggest `/architect`                 |
| Phase scope needs to expand                                  | Stop, surface to user — they decide                          |
| Done-when check is impossible                                | Stop, surface to user — they decide whether to escalate     |
| Phase is sibling-owned and feels out of your depth           | Recommend switching to the sibling skill in Step 1          |
| Security-checklist item can't be implemented as stated       | Stop. Security items don't get punted silently.             |
