# ADR-0008 — Electron shell conventions: build pipeline, tsconfigs, IPC discipline, security defaults

> **Status:** accepted
> **Date:** 2026-05-17
> **Related plan(s):** [0001-bootstrap](../plans/0001-bootstrap.md)
> **Related ADRs:** [ADR-0002](0002-ipc-local-http.md), [ADR-0005](0005-desktop-shell-electron.md)

## Context

[ADR-0005](0005-desktop-shell-electron.md) decided we use Electron, but it left the tactical question of *how* — build pipeline, process-specific TypeScript configs, IPC discipline, packaging — to be decided in the plan. There is a sibling project at `../music_production_suite` (~0.6.0, ~20+ IPC channels, three-process build, GitHub release pipeline, husky hooks, two-config Jest setup, code-signing-ready electron-builder config) that has already discovered the answers and shipped against them. The relevant trade is: re-derive everything from scratch, or adopt the conventions that fit and document what we deliberately diverge on.

The patterns in music_production_suite that are worth lifting are the ones that show up early and never change: how the main/preload/renderer get built, how channels are named, how the preload bridges to the renderer, what BrowserWindow defaults are safe, what the packaging config looks like. The patterns that don't transfer are the ones tied to its specific domain (Puppeteer, WebTorrent, audio streaming protocols) — those are noise for us.

There is one structural difference between the two projects that shapes everything below: **music_production_suite runs ~all business logic in the Electron main process**, with IPC carrying every domain operation between renderer and main. **market-analyser does not** — domain logic lives in the Python sidecar (per [ADR-0002](0002-ipc-local-http.md)), and the renderer talks to it over local HTTP. Our Electron main process is intentionally thinner: it supervises the sidecar, owns OS integration (file dialogs, window state, app menu, auto-update), and forwards the per-launch bearer secret to the renderer. So we adopt music_production_suite's IPC patterns but at much smaller scope — single-digit channels, not dozens.

## Decision

We adopt the following conventions for the `desktop/` workspace, modeled on `music_production_suite`. Concrete config snippets are in this ADR rather than the plan because they describe durable structure, not a one-time phase.

### Build pipeline

Three artifacts, three bundlers, one set of scripts. No Electron Forge, no `@electron/rebuild`, no monolithic webpack config.

- **Main process** — `esbuild`, output `dist/main/index.cjs`. Built by `desktop/scripts/build-main.mjs`. Externals: `electron` plus any native-binding deps we add later. Platform `node`, format `cjs`, target the Node version Electron ships.
- **Preload script** — `esbuild`, output `dist/preload/index.cjs`. Built by `desktop/scripts/build-preload.mjs`. Externals: `electron` only.
- **Renderer** — `vite` + `@vitejs/plugin-react`, output `dist/renderer/`. Built by `vite build`. Dev server on a fixed port (`5173`).

Dev mode: `concurrently "vite" "wait-on http://localhost:5173 && cross-env NODE_ENV=development electron ."`. The main process loads from the Vite dev server (`http://localhost:5173`) when `NODE_ENV=development`, from the bundled `dist/renderer/index.html` otherwise. `cross-env` is non-negotiable — Windows is the primary dev OS.

### TypeScript configuration

Four tsconfigs, deliberately. A single config does not survive the multi-process boundary because main and preload need `module: CommonJS` while renderer needs `ESNext`.

- `desktop/tsconfig.json` — base config. `strict: true`, `noUnusedLocals`, `noUnusedParameters`, `noImplicitReturns`, `noFallthroughCasesInSwitch`, `isolatedModules`, `forceConsistentCasingInFileNames`. Path aliases: `@/* → renderer/*`, `@shared/* → shared/*`.
- `desktop/tsconfig.main.json` — extends base; `module: CommonJS`, `outDir: dist/main`, `types: ["node"]`, includes `electron/**/*` + `shared/**/*`.
- `desktop/tsconfig.preload.json` — extends base; `module: CommonJS`, `outDir: dist/preload`, `types: ["node"]`, includes `electron/preload/**/*` + `shared/**/*`.
- `desktop/tsconfig.renderer.json` — extends base; `module: ESNext`, `jsx: react-jsx`, includes `renderer/**/*` + `shared/**/*`.

The `typecheck` script in `desktop/package.json` runs all three `--noEmit` in sequence: `tsc --noEmit --project tsconfig.renderer.json && tsc --noEmit --project tsconfig.main.json && tsc --noEmit --project tsconfig.preload.json`. CI runs `typecheck` as a separate job from `build`.

A `desktop/shared/` directory holds code used by more than one process: IPC channel constants, shared types, validation schemas. Importable from any process via the `@shared/*` alias.

### IPC discipline (renderer ↔ main)

Small surface, strict conventions. The renderer ↔ sidecar HTTP path handles domain operations; this section is about renderer ↔ main only.

- **Channel name constants** live in `desktop/shared/ipc-channels.ts` as a `const IPC_CHANNELS = { ... }` object. Never bare strings in handlers or preload bindings.
- **Preload API is namespaced.** A single `window.api` object is assembled in `desktop/electron/preload/index.ts` from per-domain modules under `desktop/electron/preload/api/`. Each module exports its namespace; `index.ts` only imports and merges. The combined type is exported as `ElectronAPI = typeof api` so the renderer can `declare global { interface Window { api: ElectronAPI } }` for full type inference.
- **Three IPC shapes, no others:**
  1. **Request-response** — `ipcMain.handle(channel, handler)` ↔ `ipcRenderer.invoke(channel, ...args)`. Handler returns a value or Promise.
  2. **Main-to-renderer push** — handler calls `event.sender.send(channel, payload)` (or `BrowserWindow.getAllWindows().forEach(...)` for broadcast); preload wraps `ipcRenderer.on` and **returns a cleanup function** that the renderer calls on unmount. No fire-and-forget event listeners without a cleanup path.
  3. **Custom protocols** — used only for streaming OS files to the renderer (if we later need to serve large CSVs or local images to the chart layer). Registered with `protocol.registerSchemesAsPrivileged` *before* `app.whenReady()`.
- **Every IPC payload is validated** at the main-process handler with a Zod schema co-located with the channel constant. No `any`, no trust in the renderer.
- **Handler registration** is a single `registerIpcHandlers()` call in `desktop/electron/main.ts`, which composes per-domain `register*Handlers()` functions from `desktop/electron/ipc/*Handlers.ts`. Mirror this for any `cleanup*Handlers()` symmetry — main process listens for `before-quit` and unregisters resources cleanly.

Initial channel set (week one and immediately after):

| Channel                  | Direction | Purpose                                                                |
|--------------------------|-----------|------------------------------------------------------------------------|
| `app:get-info`           | R→M       | Version, sidecar status snapshot for the UI footer.                    |
| `sidecar:get-port`       | R→M       | Returns `{ port, secretToken }` the renderer's `fetch` client needs.   |
| `sidecar:status`         | M→R       | Push events when the sidecar crashes, restarts, or becomes ready.      |
| `dialog:open-directory`  | R→M       | Native directory picker (for "where to save exports" later).           |
| `shell:open-external`    | R→M       | Whitelisted external-URL opener for help/docs links.                   |

Anything domain-shaped (`ohlcv:get`, `strategy:run`, etc.) goes through the **HTTP path to the sidecar**, never through Electron IPC. This is a hard rule.

### Security defaults

Adopt the validated defaults from music_production_suite verbatim — every line below is on the security-incident path if disabled.

```ts
new BrowserWindow({
  webPreferences: {
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
    preload: join(__dirname, '../preload/index.cjs'),
  },
  show: false,
})
window.once('ready-to-show', () => window.show())
```

Content Security Policy is enforced **twice**, deliberately:

1. As a `<meta http-equiv="Content-Security-Policy">` in `desktop/renderer/index.html`.
2. As an HTTP response header set in `desktop/electron/main.ts` via `session.defaultSession.webRequest.onHeadersReceived`, which strips any incoming `content-security-policy` header (case-insensitively — Vite's dev server sends lower-case) and writes ours.

The CSP allows `'unsafe-inline'` in `script-src` only when `app.isPackaged === false` (Vite HMR needs it); production strips it.

```
default-src 'self';
script-src 'self' <'unsafe-inline' in dev only>;
style-src 'self' 'unsafe-inline';
img-src 'self' data: https:;
connect-src 'self' http://127.0.0.1:*;
```

The `connect-src http://127.0.0.1:*` line is the only relaxation we make beyond music_production_suite's CSP — without it, the renderer can't `fetch` the Python sidecar. We bind the port range tighter in code than CSP allows: the renderer's fetch client only calls the one port handed to it via the preload bridge.

External URLs (help links, docs) open in the user's default browser via `shell.openExternal`, never in an in-app window. Any `BrowserWindow.webContents` `will-navigate` and `setWindowOpenHandler` events are intercepted: same-origin allowed, everything else routed to the OS browser.

### Packaging

`electron-builder`, configured in `desktop/package.json` under the `build` key. Targets:

- **Windows:** NSIS installer (`oneClick: false`, `allowToChangeInstallationDirectory: true`, `perMachine: true`, `createDesktopShortcut: true`, `deleteAppDataOnUninstall: false`). The `deleteAppDataOnUninstall: false` is important — we never want an uninstaller to wipe a trader's cached bars and backtest results without explicit prompting.
- **macOS:** DMG. Category `public.app-category.finance`.
- **Linux:** AppImage.

Output directory: `release/`. The `files` glob: `dist/**/*` + `resources/**/*`.

Auto-update is **deferred** (out of bootstrap scope). When we wire it: `publish: { provider: github, owner: <user>, repo: market-analyser }` and `electron-updater` in the main process. Until then, the `release.yml` GitHub Action only builds and uploads artifacts; it does not publish.

In CI, `CSC_IDENTITY_AUTO_DISCOVERY: false` to disable auto-discovered code-signing identities — we don't want CI to accidentally sign with a developer's local cert. Real code-signing is its own followup ADR before the first public release.

### Linting and formatting

Same shape as music_production_suite. ESLint + Prettier + lint-staged + Husky.

- `eslint` + `@typescript-eslint` + `eslint-plugin-react` + `eslint-plugin-react-hooks`.
- `prettier` with project defaults.
- `lint-staged` runs `eslint --fix` + `prettier --write` on staged TS/TSX, `prettier --write` on JSON/CSS/MD.
- `husky` pre-commit: `lint-staged` + `typecheck`. Pre-push: full test suite (TS + Python).

### Renderer testing

Jest for unit/component tests; Playwright for e2e. Two Jest configs:

- `desktop/jest.config.ts` — renderer + shared. Test files match `**/utils.{test,spec}.ts(x)`, `**/utils/*.{test,spec}.ts(x)`, `**/hooks/*.{test,spec}.ts(x)`, `**/store/**/*.{test,spec}.ts(x)`. Coverage *only* targets business logic — utils, hooks, stores. Components are not snapshot-tested.
- `desktop/jest.config.main.ts` — main-process tests. Test files match `electron/**/*.{test,spec}.ts(x)` and `shared/**/*.{test,spec}.ts(x)`. `testEnvironment: 'node'`. Coverage targets `electron/**/*` excluding `electron/main.ts`.

`test:all` runs both Jest configs in sequence. Playwright e2e tests under `desktop/tests/` cover the cold-start golden path (window opens → sidecar healthy → chart renders).

## Consequences

### Positive
- Three small, focused bundlers vs one monolithic config. Build failures point to the right process immediately. `dev` startup is measured in seconds.
- Per-process `tsconfig` catches `import` mistakes early (renderer importing a Node-only API fails at typecheck, not at runtime).
- IPC channel constants + Zod-validated payloads make the renderer↔main boundary discoverable and safe; the `cleanup function` convention for push events prevents the most common Electron memory leak.
- BrowserWindow security defaults match the OWASP-style checklist for Electron; the double-CSP (HTML meta + HTTP header) defends against both renderer-side bypass attempts and Vite's dev-server CSP overrides.
- Packaging is a single `electron-builder` config in `package.json`; no separate build tool to learn.
- Husky pre-push test gate prevents a class of "I forgot to run tests" pushes that would otherwise pollute the branch.

### Negative
- Four tsconfigs is more surface than one. Onboarding cost: a developer needs to know which config covers their file. Mitigation: a `desktop/README.md` table maps directory → config.
- Esbuild bundles for main/preload mean source maps are slightly less faithful than `tsc`-only builds in stack traces. Acceptable; not a debugging blocker in practice.
- Three bundlers running concurrently in dev mean three log streams in the terminal. `concurrently` prefixes lines, which helps but does not solve.
- Adopting `lint-staged` + `husky` + `commitizen` means every commit triggers tooling. On Windows, the husky `pre-commit` shell hook needs `git config core.autocrlf` care; `husky` v9 handles this well.
- We pay a small startup cost rebuilding main + preload whenever those change in dev. Vite handles renderer hot-reload, but main/preload need a full Electron restart. Mitigated by `dev:debug` and `nodemon`-style restart scripts; not solved.

### Neutral
- Yarn vs pnpm vs npm: music_production_suite uses Yarn classic; market-analyser is free to pick. Recommendation: **pnpm** for the desktop workspace — faster, better disk usage, strict-by-default — but not a structural decision; document in the plan.
- The `electron-builder` `publish` block is defined but `release.yml` does not publish for now. When we turn it on, the same config flips publishing live; no rewrite.

## Alternatives considered

### Alternative A — Electron Forge
A monolithic toolchain that bundles webpack, electron-rebuild, packaging, and squirrel-update behind one CLI. Rejected because: (1) the sibling project has already validated the `esbuild × 2 + vite` approach and we get to copy it for free; (2) Electron Forge's defaults change between major versions in disruptive ways; (3) the layered approach lets us swap any one bundler without rewriting the others.

### Alternative B — `@electron/rebuild` + webpack
The traditional Electron stack. Rejected because webpack config sprawl is real and we have no need for the features that justify it (loaders for arbitrary asset types, code-splitting beyond what Vite gives us). `esbuild` is enough and 10–100x faster.

### Alternative C — Skip the preload entirely, expose `nodeIntegration: true`
The "I just want it to work" path. Rejected — `nodeIntegration: true` is a CVE waiting to happen for any app that displays content with user-supplied URLs or third-party iframes, and even our charting library could one day load a remote tile or fetch an external resource that we mis-configure. The cost of doing this right (one preload script with a `contextBridge.exposeInMainWorld('api', api)`) is small; the cost of doing it wrong is large.

### Alternative D — One tsconfig with `references`
TypeScript project references. Rejected only because the `extends`-with-overrides pattern in music_production_suite is simpler to read and to teach. Project references are not wrong; they're just heavier than this codebase needs.

## What we explicitly do NOT adopt from music_production_suite

These are listed so future readers know the omissions were deliberate, not oversights.

- **Chakra UI v3.** Heavy and opinionated; not aligned with our charting-first UI. The bootstrap renderer uses minimal CSS until `ui-builder` makes a component-library decision in its own ADR.
- **Zustand for state.** Possibly useful later but not needed for a single-symbol chart view. `ui-builder`'s call.
- **`zundo` (undo/redo middleware).** Domain-irrelevant.
- **`electron-store`** for renderer-side config. Our config (per [ADR-0006](0006-persistence-layout.md)) lives in the Python sidecar's `config.json`. The renderer reads it via the sidecar's HTTP `/config` endpoint; window state (size, position) can use `electron-store` only if we discover a need.
- **Puppeteer / Cheerio / WebTorrent / FFmpeg / Essentia.** Domain libraries for a different app.
- **Their release-only CI (`build.yml` triggers only on `workflow_dispatch` and `tags: v*`).** We keep a separate `ci.yml` (per Plan 0001 phase 1) that runs lint + typecheck + tests on every push and PR. `release.yml` is additive.
- **Single Jest config covering both processes.** Their two-config split is fine, but we go further: the sidecar is Python (pytest), so the desktop workspace already has a hard process boundary. Use their split for the TS side only.
- **Node 22+ requirement.** Electron 40 supports Node 22 internally; we don't need to constrain the user's system Node. Use whatever Node version Electron ships with for the main/preload runtime; require Node 20+ as the host build environment.

## Notes

- The full IPC surface design (channels, payloads, cleanup contract) is in this ADR rather than the plan because it locks the renderer↔main contract for years. Phase 4 of [Plan 0001](../plans/0001-bootstrap.md) lists which channels land in the bootstrap PR.
- The double-CSP pattern is the single most copy-worthy piece of music_production_suite's security code. It exists because Vite's dev server inserts a permissive CSP and that override must be stripped — case-insensitively — before our policy goes on the response. Documented at `src/main/window.ts` in music_production_suite if a reference implementation is needed.
- We deliberately keep the renderer↔main IPC surface single-digit. Every new channel proposal must justify why it isn't a sidecar HTTP endpoint instead. The strong default is "if it's domain logic, it's a sidecar endpoint".
