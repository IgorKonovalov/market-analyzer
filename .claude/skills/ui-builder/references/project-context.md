# Project context — ui-builder

The ui-builder-specific view of the `market-analyser` project. For architecture, ADR rationale, and the running ADR list, see `.claude/skills/architect/references/project-context.md` — the architect's view is the source of truth.

## Repo root

```
<repo-root>/market-analyser
```

## Your turf

```
desktop/
├── package.json                  # pnpm workspace, scripts, lint-staged config, electron-builder config
├── tsconfig.json                 # base TS config
├── tsconfig.main.json            # main process (module: CommonJS, types: ["node"])
├── tsconfig.preload.json         # preload (module: CommonJS, types: ["node"])
├── tsconfig.renderer.json        # renderer (module: ESNext, jsx: react-jsx)
├── vite.config.ts                # renderer dev server + build
├── .eslintrc.json
├── .prettierrc.json
├── jest.config.ts                # renderer + shared
├── jest.config.main.ts           # main-process tests (testEnvironment: node)
│
├── electron/                     # main process + preload
│   ├── main.ts                   # app lifecycle, sidecar supervisor, window factory
│   ├── window.ts                 # createWindow() with security defaults + double-CSP
│   ├── preload/
│   │   ├── index.ts              # assembles window.api, calls contextBridge.exposeInMainWorld
│   │   └── api/                  # per-domain preload modules
│   │       ├── app.ts
│   │       ├── sidecar.ts
│   │       ├── dialog.ts
│   │       └── shell.ts
│   └── ipc/                      # main-process handlers
│       ├── index.ts              # registerIpcHandlers() + cleanupServices()
│       ├── appHandlers.ts
│       ├── sidecarHandlers.ts
│       ├── dialogHandlers.ts
│       └── shellHandlers.ts
│
├── renderer/                     # React SPA — NEVER imports node, electron, fs, child_process
│   ├── index.html                # has <meta http-equiv="Content-Security-Policy"> matching the HTTP header
│   ├── main.tsx                  # React entry
│   ├── App.tsx                   # router root
│   ├── styles.css                # global tokens + minimum-legibility resets
│   ├── api/
│   │   └── client.ts             # typed fetch client; injects bearer + base URL
│   ├── components/               # PascalCase.tsx + co-located .module.css
│   ├── views/                    # route-level compositions
│   ├── hooks/                    # useFoo.ts
│   └── types/
│       ├── global.d.ts           # declare global { interface Window { api: ElectronAPI } }
│       └── sidecar/              # generated from OpenAPI — never hand-edit
│
├── shared/                       # importable from any process via @shared/*
│   ├── ipc-channels.ts           # IPC_CHANNELS const object — single source of truth for channel names
│   ├── schemas/                  # Zod schemas per IPC channel payload
│   └── types/                    # shared TS interfaces across processes
│
├── scripts/
│   ├── build-main.mjs            # esbuild → dist/main/index.cjs
│   ├── build-preload.mjs         # esbuild → dist/preload/index.cjs
│   └── gen-types.ts              # OpenAPI → desktop/renderer/types/sidecar/
│
├── tests/                        # Playwright e2e
│   ├── security.spec.ts
│   ├── sidecar-supervisor.spec.ts
│   └── ohlcv-view.spec.ts
│
├── dist/                         # build output (gitignored)
└── release/                      # electron-builder output (gitignored)
```

What exists right now depends on which Plan 0001 phases have shipped. Always `Glob desktop/**` before assuming a directory is there. If Phase 4 (shell + supervisor, owner `dev`) hasn't landed, `desktop/` may be sparse — that's a blocker for any view work, not a "let me stub the shell to test".

## Sibling-skill ownership map

| Owner skill       | Code area                                       | Their job vs yours                                            |
|-------------------|-------------------------------------------------|---------------------------------------------------------------|
| `architect`       | `docs/architecture/`                            | Decides architecture (ADRs, plans, diagrams). Route CSP/IPC/library questions here. |
| `dev`             | API routes, data layer, persistence, vendoring, CI | Owns the Python sidecar end-to-end. You consume its HTTP API; don't shim it from the renderer. |
| `strategy-author` | `src/market_analyser/strategies/`               | Writes strategies. You render their `Params.model_json_schema()` and call the sidecar to run them. |
| `backtester`      | `src/market_analyser/backtest/`, `runs/`        | Computes Sharpe / drawdown / equity. You render `BacktestResult`; never compute metrics in the renderer. |
| **`ui-builder` (you)** | `desktop/`                                  | Everything renderer-side + Electron main/preload + IPC channels. |

When in doubt, the plan's `Owner skill` field is authoritative.

## Canonical commands

Run from the **repo root**, not from `desktop/`. The `--filter desktop` selector targets the workspace.

| Task                          | Command                                          |
|-------------------------------|--------------------------------------------------|
| Install deps                  | `pnpm install`                                   |
| Dev (run app with HMR)        | `pnpm --filter desktop dev`                      |
| Dev (with main-process inspector) | `pnpm --filter desktop dev:debug`             |
| Build all (main + preload + renderer) | `pnpm --filter desktop build`             |
| Build just main               | `pnpm --filter desktop build:main`               |
| Build just preload            | `pnpm --filter desktop build:preload`            |
| Build just renderer           | `pnpm --filter desktop build:renderer`           |
| Type-check (all 4 tsconfigs)  | `pnpm --filter desktop typecheck`                |
| Renderer + shared unit tests  | `pnpm --filter desktop test`                     |
| Main-process tests            | `pnpm --filter desktop test:main`                |
| All tests (incl. e2e)         | `pnpm --filter desktop test:all`                 |
| Playwright e2e only           | `pnpm --filter desktop test:e2e`                 |
| Lint                          | `pnpm --filter desktop lint`                     |
| Lint + autofix                | `pnpm --filter desktop lint:fix`                 |
| Regenerate sidecar types      | `pnpm --filter desktop gen:types`                |
| Package Windows installer     | `pnpm --filter desktop package:win`              |
| Package macOS DMG             | `pnpm --filter desktop package:mac`              |
| Package Linux AppImage        | `pnpm --filter desktop package:linux`            |

### When the sidecar is your blocker

To poke a sidecar endpoint while developing a view, the sidecar runs on a different port every time. The renderer reads the port via `window.api.getSidecarPort()` — but for `curl` from your terminal:

```bash
# Boot the sidecar manually (separate terminal) with a known port + secret:
uv run python -m market_analyser.api --port=8765 --secret=devtest
# Then:
curl -H "Authorization: Bearer devtest" http://127.0.0.1:8765/ohlcv?symbol=AAPL&timeframe=1d
```

Do this for sanity checks, never as a substitute for the typed client in renderer code.

## The ADRs that gate every decision

Re-read these whenever you're unsure. They're short.

| ADR | What it pins |
|-----|--------------|
| [ADR-0002](../../../../docs/architecture/adrs/0002-ipc-local-http.md) | Localhost HTTP transport, per-launch bearer token, no CORS, `connect-src http://127.0.0.1:*` |
| [ADR-0005](../../../../docs/architecture/adrs/0005-desktop-shell-electron.md) | Why Electron (supersedes Tauri ADR-0001) |
| [ADR-0008](../../../../docs/architecture/adrs/0008-electron-shell-conventions.md) | Build pipeline, 4 tsconfigs, IPC discipline, security defaults, packaging — the longest ADR for a reason |
| [ADR-0004](../../../../docs/architecture/adrs/0004-strategy-interface.md) | Strategy `Params` model + `model_json_schema()` for auto-forms |
| [ADR-0006](../../../../docs/architecture/adrs/0006-persistence-layout.md) | Where SQLite + `config.json` live (you don't write to either, but you read config via sidecar) |
| [ADR-0007](../../../../docs/architecture/adrs/0007-market-data-provider.md) | The provider Protocol that backs `/ohlcv` and friends |

## Current state checkpoints

As of 2026-07-03 every checkpoint below is landed — treat them as the floor you build on, and `Glob` before assuming anything newer:

- **The renderer has 7 nav tabs / 8 views**: `OhlcvView` (chart), `RecentBacktestsView` + `BacktestView`, `LiveSignalView` (Signals), `RecommendationsView`, `NewsView`, `AlertsView`, `SettingsView` — plus the cross-view `AlertToaster` and `ThemeToggle`.
- **`CandlestickChart.tsx` is a known god component (~1041 lines)** with a standing decomposition follow-up (plans README) — any chart touch should lift per-effect reconcilers into hooks, not grow it further.
- **Theming** is renderer-owned (ADR-0039): `lib/theme.ts` is the single source of truth; read colors from CSS tokens, never hardcoded hex.
- **SSE dispatch** lives in `hooks/useEventStream.ts` (`dispatchEnvelope`); the newest payloads (`recommendation.completed`, `alert.triggered`) are Zod-`safeParse`d via `schemas/` — new event handling follows that pattern, and `types/events.ts` is hand-mirrored (guarded by `events.test.ts`, not `gen-types`).
- **Reactive agent-emitted surfaces** (Signals, Recommendations, Alerts) are dedicated views fed by App-level state set in `useEventStream` handlers — the house pattern any new reactive surface (e.g. Plan 0037's Forecast view) follows; no auto-switch on incoming events.

The architect's project-context lists the canonical state at any point — read it when in doubt rather than assuming.

## Plan numbering & status

- Plans: `docs/architecture/plans/NNNN-<slug>.md` — zero-padded.
- ADRs: `docs/architecture/adrs/NNNN-<slug>.md`.
- Reviews: in-conversation only. There is **no** `docs/architecture/reviews/` directory.
- Completed plans: `docs/architecture/plans/done/NNNN-<slug>.md` — architect moves them after close ceremony.

Plan status line: `Status: draft | in-progress | done | abandoned`. Your one allowed plan edit is flipping `draft` → `in-progress` when you open the Mode 2 gate.

## Where to escalate

| Situation                                          | Where to go                                              |
|----------------------------------------------------|----------------------------------------------------------|
| Plan or ADR contradicts reality                    | Stop, surface to user, suggest `/architect`              |
| Need a new IPC channel                             | Justify it, propose in chat, route to `/architect` if non-trivial |
| Need to relax CSP                                  | Stop. Route to `/architect`. No silent CSP edits.        |
| Need a component library, state-management lib, charting swap | Stop. Architect ADR decision.                  |
| Sidecar endpoint missing                           | Surface to user — that's `dev` work, not a renderer shim |
| Renderer needs Node API                            | Wrong process. Either a new IPC channel or a new sidecar endpoint. |
| Security-checklist item can't be met as stated     | Stop. Security items never get punted silently.          |
