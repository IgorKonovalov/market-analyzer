# 0033 — UI theme control + visual polish

> **Status:** done — closed 2026-06-04. Four `ui-builder` commits on `main`: `e87dea3` (theme mechanism + no-flash bootstrap admitted by CSP SHA-256 hash) → `143f334` (visual-polish tokens + chrome, both themes) → `c0a2d9c` (Appearance control + header toggle) → `b8d48c6` (theme-aware candlestick chart). Clean Mode 4: no blockers; one commit-hygiene nit (phase-1 commit carried an R100 rename of Plan 0032 into `done/` — cross-plan bleed, left as-is per no-history-rewrite) and the phase-2 AA-contrast / reduced-motion done-whens are visual-only (no automated test, accepted on the implementer's verification). Verified by opening every named spec and reading the assertions: `theme.test.ts` (defaults/persist/apply/resolve/subscribe incl. no-fire-on-OS-change-while-explicit), `window.csp.test.ts` (recomputes the bootstrap hash from `index.html`, asserts no `unsafe-inline`), `SettingsView.test.tsx` (all three states), `theme.spec.ts` e2e (attribute flip + real computed-color change + reload persistence), `CandlestickChart.theme.test.tsx` (createChart once across the flip → no remount + dark token applied). 31 renderer + 3 main specs green at close. ADR-0039 accepted. No branch — committed directly on `main`.
> **Created:** 2026-06-03
> **Owner skill(s):** ui-builder
> **Related ADRs:** [0039-renderer-theming-localstorage](../adrs/0039-renderer-theming-localstorage.md) (paired — accepts at this plan's close), [0008-electron-shell-conventions](../adrs/0008-electron-shell-conventions.md) (CSP), [0006-persistence-layout](../adrs/0006-persistence-layout.md) (why theme does *not* go in config.json)

## TL;DR

The renderer already has a CSS-token foundation and an OS-driven dark palette, but there is **no in-app way to choose a theme** and the overall look is wireframe-plain. This plan adds an explicit **Light / Dark / System** control (a Settings *Appearance* section plus a compact header toggle), persisted in `localStorage` and applied before first paint so there is no flash; gives every view a **visual polish pass** (refined palette, elevation, type scale, restyled tabs/cards/tables/badges/states) that looks good in both themes; and makes the **candlestick chart theme-aware** so candle/overlay/marker colors follow the chosen theme instead of being hardcoded. First user-visible behavior: flip a switch and the whole app — chart included — turns dark and stays dark across restarts.

## Context & problem

A live walkthrough of all four views (Chart, Backtests, News, Settings) in the browser surfaced the following:

- **Dark mode is OS-driven only.** It lives entirely in `@media (prefers-color-scheme: dark)` in `renderer/styles.css:21`. A user on a light OS cannot get dark, and vice-versa — there is no toggle anywhere.
- **The visual language is utilitarian to the point of looking unfinished.** `system-ui` font, flat tabs, uniform 4px radii, hairline borders, **no elevation/shadows**, sparse layout with large dead space, and a single accent blue (`#2563eb`) as the only color.
- **Button styling is copy-pasted per view** (`App.module.css` `.tab`, `OhlcvView.module.css` `.refresh`, `BacktestView.module.css` `.backButton`, `SettingsView`), so polish is inconsistent and any change is N edits.
- **Chart colors are hardcoded.** `CandlestickChart.tsx` already reads `--color-fg`/`--color-border` from computed styles (`:175-176`) but pins marker/overlay colors (`CLICKED_MARKER_COLOR='#2563eb'`, `VOLUME_MA_COLOR='#64748b'`, `VWAP_COLOR='#9333ea'`, `OBV_COLOR='#0891b2'` at `:48,62-64`) and leaves candle up/down to lightweight-charts defaults — so the chart will not follow a theme.

The user asked for "a dark theme, better visuals". The decision (below) was taken via the Mode 1 interview.

## Decision

We add an **explicit theme preference** (`light` / `dark` / `system`) applied by toggling a `data-theme` attribute on `<html>`, persisted in `localStorage` (key `ma.theme`) and read by a tiny pre-paint inline bootstrap so there is no flash-of-wrong-theme; expose it through a Settings *Appearance* control and a header quick toggle; run a **token-and-chrome polish pass** across all five view surfaces that holds up in both themes; and make the **candlestick chart read its colors from CSS tokens** and re-apply them on theme change without remounting. Theme persistence intentionally lives in the renderer, not in the sidecar's `config.json` — see [ADR-0039](../adrs/0039-renderer-theming-localstorage.md).

We rejected persisting the preference in `config.json` (it cannot meet the pre-paint, no-flash latency constraint without a sidecar round-trip, and couples a presentation toggle to sidecar availability), a dark-only default (the user wanted explicit choice), and a shared component-library refactor this round (valuable but larger — deferred to a followup so the per-view CSS only gets aligned to tokens, not restructured).

## Architecture diagram

```mermaid
flowchart TD
  subgraph boot[Pre-paint bootstrap]
    LS[(localStorage<br/>ma.theme)] --> INLINE[inline script in index.html<br/>sets html[data-theme]]
  end
  subgraph renderer[Renderer]
    THEME[lib/theme.ts<br/>get / set / resolve / subscribe]
    THEME -->|set/remove attr| HTML[html data-theme]
    THEME -->|persist| LS
    OS[(matchMedia<br/>prefers-color-scheme)] -->|system mode| THEME
    SETTINGS[SettingsView · Appearance] --> THEME
    HEADER[Header ThemeToggle] --> THEME
    HTML --> CSS[styles.css<br/>token blocks]
    CSS --> VIEWS[all views + chrome]
    THEME -->|subscribeEffective| CHART[CandlestickChart<br/>reads chart tokens, re-applies]
    CSS --> CHART
  end
  INLINE -.first paint.-> HTML
```

## Implementation phases

All four phases are `ui-builder`. They ship as four commits in one session.

### Phase 1 — Theme mechanism, token plumbing, and no-flash persistence
- **Owner skill:** ui-builder
- **What:** Add the `data-theme` override layer to `styles.css`, a `lib/theme.ts` module owning the preference, and a pre-paint inline bootstrap — defaulting to `system` so current behavior is preserved.
- **Files touched:** `desktop/renderer/styles.css` (restructure palette into base/override blocks), `desktop/renderer/lib/theme.ts` (+ `theme.test.ts`), `desktop/renderer/index.html` (inline bootstrap in `<head>`), `desktop/renderer/main.tsx` (apply stored pref on boot for the SPA path).
- **Done when:**
  - `theme.test.ts` proves: stored preference defaults to `system` when unset; `setTheme('dark')` writes `localStorage['ma.theme'] === 'dark'` **and** sets `documentElement.dataset.theme === 'dark'`; `setTheme('system')` **removes** the attribute (so the media query governs); `resolveEffective('system')` returns `'dark'`/`'light'` per a mocked `matchMedia`; and `subscribeEffective` fires when the mocked OS preference changes **while in `system` mode** and does **not** fire on OS change while an explicit theme is set.
  - With `data-theme='dark'` on the root, `getComputedStyle(documentElement).getPropertyValue('--color-bg')` resolves to the dark value and with `data-theme='light'` (or attribute absent under a light OS) resolves to the light value — i.e. the explicit attribute overrides `prefers-color-scheme`.
  - The inline bootstrap sets the attribute **before** the module bundle loads (no flash on reload); confirm it satisfies **both** the `index.html` CSP meta (already `script-src 'self' 'unsafe-inline'`) and the main-process CSP header (ADR-0008 double-CSP) — if the header is stricter, use a build-time hash/nonce rather than weakening the policy.
  - Existing renderer unit + e2e specs stay green (no behavior change yet).

### Phase 2 — Visual polish pass (tokens + chrome, both themes)
- **Owner skill:** ui-builder
- **What:** Expand the token set (elevation/shadow, type scale, radii, semantic success/danger for trading numbers, refined neutrals for both palettes) and restyle the shared chrome and every view's surfaces so the app looks finished in both themes.
- **Files touched:** `desktop/renderer/styles.css`, `desktop/renderer/App.module.css`, `desktop/renderer/views/{OhlcvView,BacktestView,RecentBacktestsView,NewsView,SettingsView}.module.css`, `desktop/renderer/components/{Toast,SymbolPicker,AgentModeToggle}.module.css`. (No `.tsx` structural changes; class names/testids/aria unchanged.)
- **Done when:**
  - All five surfaces (Chart, Backtests, News, Settings, and the Backtest result view) render with the refreshed look in **both** light and dark — verified visually via the running app in each theme.
  - Restyled: header identity, nav tabs, the (still per-view) buttons aligned to one token-driven rule set, metric cards (elevation + `tabular-nums`), the trade table (header/hover/zebra), badges, the OHLCV toolbar, and the empty/error/loading states.
  - Body/foreground text meets **WCAG AA contrast** on its background in both themes; `:focus-visible` remains on every interactive control; any new transition is wrapped in `@media (prefers-reduced-motion: reduce)`.
  - Existing renderer unit + Playwright e2e specs stay green (they key off roles/testids, which don't change).

### Phase 3 — Appearance control (Settings) + header quick toggle
- **Owner skill:** ui-builder
- **What:** A Settings *Appearance* section with a Light/Dark/System segmented control wired to `theme.ts`, plus a compact toggle in the app header for one-click switching.
- **Files touched:** `desktop/renderer/views/SettingsView.tsx` (+ `.module.css`, + `SettingsView.test.tsx`), `desktop/renderer/components/ThemeToggle.tsx` (+ `.module.css`, + test), `desktop/renderer/App.tsx` + `App.module.css` (mount the toggle in the header).
- **Done when:**
  - Choosing **Dark** sets `documentElement.dataset.theme === 'dark'` and writes `localStorage['ma.theme'] === 'dark'`; choosing **System** removes the attribute and writes `'system'`; the header toggle and the Settings control reflect each other (single source of truth in `theme.ts`).
  - A Playwright e2e asserts: clicking Dark flips `data-theme` to `dark` **and** a representative computed color (e.g. body background) changes accordingly, and that after a reload the choice is restored from `localStorage`.
  - `SettingsView.test.tsx` covers all three control states and that selecting one calls the theme setter.
  - The control is keyboard-operable (radio/segmented semantics, `aria-pressed`/`role="radiogroup"` as appropriate) and existing Settings specs stay green.

### Phase 4 — Theme-aware candlestick chart
- **Owner skill:** ui-builder
- **What:** Move candle/overlay/marker colors to CSS tokens and have `CandlestickChart` read them and re-apply on theme change **in place** (no remount), reusing the existing computed-style read seam.
- **Files touched:** `desktop/renderer/styles.css` (chart tokens), `desktop/renderer/components/CandlestickChart.tsx`, `desktop/renderer/lib/{overlays,markers}.ts` (color source), `desktop/renderer/components/CandlestickChart.*.test.tsx`.
- **Done when:**
  - Candle up/down, overlay (volume MA / VWAP / OBV), and marker colors derive from CSS tokens (`--chart-up`, `--chart-down`, `--overlay-*`, `--marker-*`) rather than the hardcoded hex constants at `CandlestickChart.tsx:48,62-64`.
  - Switching theme recolors the **existing** chart instance (subscribe to `theme.ts` effective-theme changes → re-read tokens → `series.applyOptions(...)` + redraw); a renderer test flips `data-theme` and asserts `applyOptions` is called with the dark token values (or the existing `__test_chart_render__` gate is extended to assert the recolor), proving no remount.
  - Existing chart specs and the `__test_chart_render__` render gate stay green.

## Data shapes

```ts
// desktop/renderer/lib/theme.ts — illustrative, not final
export type ThemePref = 'light' | 'dark' | 'system'
export type EffectiveTheme = 'light' | 'dark'

const STORAGE_KEY = 'ma.theme'

export function getStoredTheme(): ThemePref          // 'system' if unset/blocked
export function setTheme(pref: ThemePref): void       // persist + apply (set/remove data-theme)
export function applyTheme(pref: ThemePref): void     // DOM-only; 'system' removes the attribute
export function resolveEffective(pref: ThemePref): EffectiveTheme   // 'system' → matchMedia
export function subscribeEffective(cb: (t: EffectiveTheme) => void): () => void
```

```css
/* styles.css — base = light; explicit attribute overrides the OS media query */
:root            { /* light tokens (base) */ }
:root[data-theme='dark']  { /* dark tokens */ }
@media (prefers-color-scheme: dark) {
  :root:not([data-theme]) { /* system-follow dark — only when no explicit choice */ }
}
```

```html
<!-- index.html <head> — pre-paint, no-flash; relies on existing 'unsafe-inline' script-src -->
<script>
  try { var t = localStorage.getItem('ma.theme');
        if (t === 'light' || t === 'dark') document.documentElement.setAttribute('data-theme', t);
  } catch (e) { /* localStorage blocked → fall back to system */ }
</script>
```

## Risks & open questions

- **CSP vs the inline bootstrap.** The `index.html` meta already allows `'unsafe-inline'` for `script-src`, but ADR-0008's main-process CSP **header** may be stricter. Mitigation: phase 1 confirms both; if the header forbids inline, switch to a build-time hashed script rather than loosening CSP.
- **Flash-of-wrong-theme.** The whole no-flash property hinges on the bootstrap running before paint. Mitigation: it lives in `<head>` ahead of the module script; the e2e reload check in phase 3 is the regression guard.
- **Dark-mode contrast regressions.** Hand-rolled dark palettes routinely fail AA on muted text. Mitigation: AA contrast is an explicit phase-2 done-when, checked on both palettes.
- **Chart recolor without remount.** If `series.applyOptions` is used wrong the chart may flicker or rebuild, losing zoom/scroll state. Mitigation: phase 4 reuses the existing computed-style read seam and asserts in-place recolor in a test.
- **localStorage unavailable** (sandboxed/privacy modes). Mitigation: every access is `try/catch`; failure degrades to `system` (session-only), never throws.
- **File collisions with in-flight work.** This plan edits `styles.css`, `App.tsx`, and `SettingsView.tsx` — the same surfaces as **Plan 0023** (News view, currently in flight on branch `plan-0023-news-view`) and the `ui-builder` phase of **Plan 0026** (chart panel on `CandlestickChart`/`OhlcvView`). Mitigation: **serialize** — land 0033 after 0023 closes, and don't run 0033 phase 4 in parallel with 0026 phase 3 (both touch the chart). See the execution-order note in the plans index.

## What this plan does NOT do

- **No shared component library.** Buttons/cards stay per-view CSS, only aligned to common tokens. Extracting `<Button>`/`<Card>` is a worthwhile followup, not this plan.
- **No `config.json`/sidecar persistence** for theme and **no new route/IPC** — ADR-0039 picks `localStorage`.
- **No web fonts.** Stay on the `system-ui` stack; only the type *scale* changes.
- **No information-architecture or layout restructuring**, no new views, no density/compact mode, no new chart types or overlays.
- **No changes to the existing `ui-builder` open follow-ups** (e.g. 0020 metrics UI, SSE Zod validation). The polish pass will touch `BacktestView` but does not pull those in.

## Followups (after this lands)

- Extract a shared component layer (`Button`, `Card`, `Field`) so per-view CSS stops duplicating button rules.
- Consider a small set of accent/brand choices once the token system is in place.
- Re-evaluate whether any presentation preference ever needs to be portable across machines (would reopen the ADR-0039 `localStorage` choice).
