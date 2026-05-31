# desktop — Electron + React shell

The Electron + React + TypeScript desktop shell for `market-analyser`. See
[ADR-0008](../docs/architecture/adrs/0008-electron-shell-conventions.md) for the
build pipeline and IPC conventions.

## Layout

| Directory           | Owns                                                | tsconfig                 |
| ------------------- | --------------------------------------------------- | ------------------------ |
| `electron/`         | Main process, sidecar supervisor, IPC handlers      | `tsconfig.main.json`     |
| `electron/preload/` | Preload script + per-domain `window.api` modules    | `tsconfig.preload.json`  |
| `renderer/`         | React + Vite renderer                               | `tsconfig.renderer.json` |
| `shared/`           | IPC channels, Zod schemas, types used by both sides | (every tsconfig)         |
| `scripts/`          | esbuild build scripts for main + preload            | n/a                      |
| `tests/`            | Playwright e2e specs                                | n/a                      |

## Scripts

- `pnpm dev` — runs esbuild watchers + Vite dev server + Electron.
- `pnpm build` — produces `dist/main/index.cjs`, `dist/preload/index.cjs`, and `dist/renderer/`.
- `pnpm typecheck` — runs all four tsconfigs in `--noEmit` mode.
- `pnpm test` / `pnpm test:main` / `pnpm test:all` — Jest renderer + main.
- `pnpm test:e2e` — Playwright (requires `pnpm build` to have produced `dist/`).
- `pnpm package:win` — produces an NSIS installer under `release/`.

## Renderer views

Candlestick chart (symbol picker, agent-mode toggle, agent-driven overlays),
backtest result + recent-runs views, and a settings page. The renderer reaches
the sidecar only through the typed fetch client (`renderer/api/client.ts`),
which injects the bearer token.
