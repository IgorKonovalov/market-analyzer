# ADR-0005 — Switch desktop shell from Tauri to Electron

> **Status:** accepted
> **Date:** 2026-05-17
> **Supersedes:** [ADR-0001](0001-tauri-vs-electron.md)
> **Related plan(s):** [0001-bootstrap](../plans/0001-bootstrap.md)

## Context

[ADR-0001](0001-tauri-vs-electron.md) (status: proposed) chose Tauri for the desktop shell, weighing cold-start time and binary size against ecosystem maturity. Before any code was written against that decision, the project direction shifted:

- The first user-visible feature is now an **OHLCV candlestick chart for one symbol**, not a polled BTC screener table. Charting libraries (lightweight-charts, Highcharts Stock, TradingView's lightweight-charts) target Chromium-class behavior; verifying each in three system webviews (WebKit on macOS, WebView2 on Windows, WebKitGTK on Linux) is friction we don't want at the bootstrap stage.
- We will also need a parameter-rendering UI for strategies (see [ADR-0004](0004-strategy-interface.md)). Mature form libraries and code editors (Monaco) are deeply Chromium-tested.
- No Rust expertise is on the project. The Tauri-side spawn-and-supervise pattern for the Python sidecar is documented but each platform-specific edge has to be discovered manually.
- The constraints that motivated Tauri in ADR-0001 — cold-start and binary size — are not user-facing constraints for a tool the user opens once a day and leaves running.

This supersession is happening before any shell code was written, so the cost of switching is purely the cost of redoing ADR-0001 and the abandoned Tauri-era bootstrap draft — measured in document edits, not refactoring.

## Decision

We will use **Electron** (with `electron-builder` for packaging and `electron-updater` later) as the desktop shell. The main process is responsible for spawning and supervising the Python sidecar; the renderer is a React + TypeScript SPA. The renderer never imports Node — `nodeIntegration: false`, `contextIsolation: true`. Communication between renderer and main process uses `contextBridge` preload scripts; communication between renderer and the Python sidecar uses local HTTP per [ADR-0002](0002-ipc-local-http.md).

## Consequences

### Positive
- Access to the mature web charting and component ecosystem with no per-webview compatibility risk. lightweight-charts, AG Grid, Monaco work out of the box.
- No Rust toolchain in the contributor onboarding path.
- Sidecar spawn/supervise patterns (`child_process.spawn`, `tree-kill`, `electron-builder` extra resources) are well documented.
- `electron-updater` and code-signing flows are widely used; we inherit a working release path.

### Negative
- Bundle size grows to ~120 MB; cold start ~1–2 s. Accepted — not a user-facing constraint for this tool.
- Baseline ~150 MB Chromium RAM before our UI loads.
- The renderer's web threat model is broader than Tauri's. Mitigation requires explicit and tested settings: `nodeIntegration: false`, `contextIsolation: true`, strict CSP, no remote module, `webSecurity: true`, allowlist of preload-bridged IPC channels.
- Auto-update setup is more code than Tauri's built-in updater. Deferred to a packaging plan.

### Neutral
- The frontend stack moves from "vanilla TS" (per the abandoned Tauri-era draft) to **React + TypeScript**. Slightly more upfront tooling (Vite, React-Query for fetch state); justified by the charting and form components we now need.

## Alternatives considered

### Alternative A — Stay on Tauri (per superseded ADR-0001)
Smaller binary, faster startup, Rust-side memory safety. Rejected because the savings do not translate into user-visible benefit for this workload, and the cost — Rust onboarding plus per-webview component verification — is concrete and recurring.

### Alternative B — Tauri 2.x + React, accept Rust learning
Some of the same component-ecosystem benefit by using React inside Tauri. Rejected because it still pays the per-webview verification cost (system webview, not Chromium) and the Rust shell-customization tax. Worth revisiting only if Electron's footprint becomes a real complaint.

## Notes

- The shell directory remains `desktop/` (TypeScript). The Python package directory remains `src/market_analyser/`. The previously-planned `src-tauri/` directory is not created.
- ADR-0002 (localhost HTTP) is unaffected by this supersession — the IPC decision survives the shell change. ADR-0003 (vendoring strategy) and ADR-0004 (strategy interface) are likewise unaffected.
- This ADR is the first material correction in the project. Future readers: the chain is ADR-0001 (proposed, never reached `accepted`) → ADR-0005 (this, accepted) — no production code was ever written against ADR-0001.
