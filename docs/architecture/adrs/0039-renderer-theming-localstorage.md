# ADR-0039 — Renderer theming: `data-theme` override + localStorage persistence

> **Status:** proposed — accepts at Plan 0033 close
> **Date:** 2026-06-03
> **Related plan(s):** [0033-ui-theme-and-visual-polish](../plans/0033-ui-theme-and-visual-polish.md)

## Context

The renderer ships a CSS-token palette with a dark variant gated entirely on `@media (prefers-color-scheme: dark)` (`renderer/styles.css`). There is no way for a user to choose a theme independent of the OS. Plan 0033 adds an explicit **Light / Dark / System** control, which forces two decisions that could reasonably go either way:

1. **How does an explicit choice override the OS media query?** The theme must be applied *before first paint* — a flash-of-wrong-theme on every launch is the failure mode any toggle has to avoid. That is a hard, synchronous, pre-bundle constraint.
2. **Where does the preference live?** ADR-0006 establishes `config.json` (sidecar-owned, in the user data dir) as the home for user configuration. Theme is "user configuration" in the loose sense — but it is a pure presentation concern, it must be readable before the React bundle (let alone the sidecar) is reachable, and the sidecar is a *separate process* that Electron attaches to asynchronously and which may be mid-restart (ADR-0016). A round-trip to the sidecar to learn "are we light or dark?" cannot meet the pre-paint constraint without a flash, and would make theming break when the sidecar is down.

These two constraints — pre-paint application and no sidecar dependency — are what make this a decision rather than a default.

## Decision

We will apply the theme by toggling a **`data-theme` attribute on the root `<html>` element** (`light` / `dark`; **absent** means "follow the OS via `prefers-color-scheme`"), and persist the user's preference in **`localStorage` under the key `ma.theme`** with values `light` / `dark` / `system`. A tiny **inline `<script>` in `index.html`'s `<head>`** reads `localStorage` and sets the attribute *before* the module bundle loads, so there is no flash. The sidecar's `config.json` is **not** used for theme, and **no new sidecar route or IPC channel** is added for it. CSS resolves the palette purely from the attribute: explicit `data-theme` wins; when absent, `@media (prefers-color-scheme: dark) :root:not([data-theme])` provides the system-follow path. Canvas-based surfaces that do not inherit CSS (the lightweight-charts candlestick chart) read the relevant tokens from computed styles and re-apply them on theme change.

## Consequences

### Positive
- **No flash-of-wrong-theme.** The `localStorage` read is synchronous and runs pre-paint; nothing waits on the bundle or the sidecar.
- **Theming is independent of sidecar state.** It works during attach, during a sidecar restart, and offline.
- **Minimal surface.** No route, no IPC, no Zod schema, no migration — a presentation toggle stays in the presentation layer.
- **Sets a reusable convention** for future renderer-only presentation preferences (e.g. density, accent).

### Negative
- **User configuration is now split across two homes:** functional config in `config.json` (sidecar), presentation prefs in `localStorage` (renderer). This is a mild inconsistency against ADR-0006's "config.json is where user config lives" framing, and a maintainer must know the split exists.
- **Not portable.** `localStorage` is per-OS-user-profile, per-app-install. The preference does not follow the user to another machine or survive a profile wipe. Acceptable for a single-user desktop app; called out so a future multi-profile need reopens this.
- **Canvas surfaces pay a per-surface cost.** Because the chart canvas does not inherit CSS, each such surface must read tokens and re-apply on theme change (handled in Plan 0033 phase 4) rather than getting recolor "for free."
- **Inline-script/CSP coupling.** The pre-paint bootstrap depends on `script-src` permitting the inline script (the `index.html` meta already does); if the main-process CSP header (ADR-0008 double-CSP) is stricter, the script must be hashed/nonced rather than the policy weakened.

### Neutral
- The default (`system`, attribute absent) reproduces today's exact behavior, so the change is purely additive for users who never touch the control.

## Alternatives considered

### Alternative A — Persist in `config.json` via the sidecar
Store `theme` as a field in the sidecar's `config.json` and read/write it over a new `GET/PUT` route through the typed fetch client. Rejected because it cannot satisfy the pre-paint, no-flash constraint (the renderer would paint a default, then repaint after the async round-trip), it couples a presentation toggle to sidecar availability (no theme while attaching/offline), and it adds a route + IPC + validation surface for what is purely a CSS concern. The ADR-0006 consistency it would buy is not worth those costs.

### Alternative B — In-memory only, no persistence
Keep the chosen theme in React state with no storage. Rejected because users expect a theme choice to survive a restart; resetting to default every launch is a worse experience than the conceptual tidiness it preserves.

### Alternative C — CSS-only (checkbox hack), no JavaScript
Drive the theme from a `:checked` toggle and sibling selectors with no script. Rejected because it cannot implement "System" (that needs `matchMedia`) and cannot reliably pre-set the theme before paint, reintroducing the flash this decision exists to prevent.

## Notes

Paired with Plan 0033; flips to `accepted` at that plan's close ceremony if the `localStorage` approach holds. Relates to ADR-0006 (persistence layout — this carves presentation prefs out of `config.json`) and ADR-0008 (Electron shell conventions / CSP — constrains the inline bootstrap).
