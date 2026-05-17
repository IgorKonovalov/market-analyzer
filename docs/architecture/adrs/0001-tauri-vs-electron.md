# ADR-0001 — Use Tauri as the desktop shell

> **Status:** superseded by [ADR-0005](0005-desktop-shell-electron.md) on 2026-05-17
> **Date:** 2026-05-17
> **Related plan(s):** an abandoned Tauri-era bootstrap draft (deleted on renumber; replaced by [0001-bootstrap](../plans/0001-bootstrap.md))

## Context

`market-analyser` is a local desktop app. The two realistic shell choices are Tauri (Rust core, system webview) and Electron (Chromium + Node). The decision matters because reversing it later costs a week — the shell wraps everything else.

Forces at play:

- **Cold start matters.** This is a tool a trader opens many times a day to check screeners. A 3-second Electron splash is annoying daily; sub-second Tauri startup is not.
- **Binary size matters mildly.** We're not shipping to scale, but a 5 MB Tauri bundle vs. 150 MB Electron means easier rebuild + re-share cycles between the user's machines.
- **The frontend code is going to be ordinary HTML/TS.** We do not need Node APIs in the renderer; everything heavyweight lives in the Python sidecar. That neutralises Electron's biggest practical advantage.
- **Ecosystem depth is real.** Electron has a decade of plugins, recipes, and Stack Overflow answers. Tauri's docs are catching up but still thinner — especially around the external-sidecar (Python child process) pattern, which is exactly what we need. This is the main risk on the Tauri side.
- **The author works solo in Python + TS.** Rust is mostly invisible in Tauri until you customize the shell, but adding a small Rust spawn-sidecar block in `main.rs` is one of the few places we *will* touch Rust on day one. That's a one-time tax of a few hours.

## Decision

We will use **Tauri 2.x** as the desktop shell. The Rust `main.rs` is responsible only for: (1) spawning the Python sidecar as a child process, (2) capturing its chosen port from stdout, (3) opening a single window pointing at the bundled `ui/` build, (4) cleanly killing the sidecar on shutdown. All UI logic lives in the webview (vanilla TS for now, framework choice deferred); all data logic lives in the Python sidecar.

If Tauri's external-binary sidecar pattern proves too painful during bootstrap slice 4 (more than one day of friction), we revisit this ADR rather than work around it — the cost of switching is bounded at slice 4 since no UI code beyond `fetch('/health')` exists yet.

## Consequences

### Positive
- Sub-second cold start — meaningful for a tool used many times a day.
- ~5 MB bundle. Easier to share builds, faster CI later.
- System webview means we inherit OS font rendering and accessibility for free; the app feels native.
- Less RAM per running instance (one Chromium copy is ~150 MB just sitting there).
- Rust shell is small enough that a Python+TS developer can maintain it without learning Rust deeply.

### Negative
- **Thinner ecosystem for sidecar spawning.** Electron's `child_process.spawn` is one well-documented line; Tauri's external-binary pattern is documented but requires platform-specific binary naming and `tauri.conf.json` config that's easy to get wrong. We pay this cost once in slice 4.
- **System webview means cross-browser quirks.** WebKit on macOS, WebView2 on Windows, WebKitGTK on Linux. CSS and JS features that "just work" in Electron's bundled Chromium may not work uniformly. Mitigation: stay close to ES2022 / standard CSS; avoid cutting-edge features. For a table-heavy trading UI this is rarely a constraint.
- **Smaller hiring pool / smaller AI training data** for Tauri-specific issues. Stack Overflow and LLMs answer Electron questions better.
- **Rust toolchain becomes a dev prerequisite.** Anyone working on the shell needs `rustup` installed. For a solo project this is fine; if we ever onboard another dev, it's friction.

### Neutral
- Auto-updater story is different between the two. Tauri ships one; Electron has `electron-updater`. Neither is in scope for week one.

## Alternatives considered

### Alternative A — Electron
The mainstream choice, bigger ecosystem, easier sidecar spawn. Rejected primarily on **daily-use cold-start cost** and the ~30x binary-size factor. Neither is fatal, but Tauri wins the cost-benefit for an app whose UI is intentionally simple. If we needed a Chromium-only feature, a Node API in the renderer, or a mature plugin we couldn't replicate, Electron would have been right.

### Alternative B — A web app served by the Python sidecar in a normal browser
Considered briefly: open `localhost:<port>` in the user's default browser, no native shell at all. Rejected because it loses single-window app behavior (browser tabs, history, accidental closure), shares cookie/localStorage state with random websites, and feels wrong for a trading tool. Worth revisiting only if both Tauri and Electron prove too heavy to maintain — extremely unlikely.

### Alternative C — PyQt / PySide
All-Python stack, no sidecar needed (UI and data layer in one process). Rejected because (1) it commits us to Qt's widget model and forecloses on web tech for charts later, (2) the author prefers HTML/TS for the UI per the project setup, and (3) it loses the clean process boundary between UI and data layer that `best-practices.md` calls for. The boundary isn't theoretical — it's what lets the data layer evolve without breaking the UI.

## Notes

- Tauri 2.x is the assumed version (GA since late 2024). If 1.x is needed for some plugin, this ADR doesn't change but the slice 4 design in the (now abandoned) Tauri-era bootstrap draft does. Moot now that the shell is Electron.
- The "fall back to Electron if slice 4 gets ugly" escape hatch is a real option — it's only one slice's worth of work to redo, and no Python code changes either way. Make the decision explicitly via a new ADR superseding this one, not silently.
