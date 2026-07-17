# ADR-0094 — OS-native desktop notifications via a renderer→main IPC channel

> **Status:** accepted (2026-07-17, at Plan 0099 close — the `notification:show` channel shipped `b403c3c`: main-process `Notification` + typed preload bridge, Zod-validated + length-capped, focused-window suppression asserted)
> **Date:** 2026-07-13
> **Related plan(s):** [0099-defi-position-out-of-range-monitor](../plans/0099-defi-position-out-of-range-monitor.md) (first consumer)
> **Related ADRs:** [ADR-0017](0017-live-ui-updates-via-sse.md) (the SSE delivery this extends; it scoped system notifications as future), [ADR-0016](0016-standalone-sidecar-mode.md) (why "notify while fully closed" stays deferred), [ADR-0008](0008-electron-shell-conventions.md) (the IPC discipline this channel obeys), [ADR-0055](0055-in-sidecar-watch-scheduler.md) / [ADR-0093](0093-defi-position-monitor-dwell-triggered.md) (the alert sources that feed it)

## Context

The app's live-event delivery (ADR-0017) pushes typed envelopes over SSE to the renderer, where an in-app toast (`AlertToaster`) and the Alerts view surface them. That path has one structural limit the user just ran into: **the toast only exists while the Electron window is open and rendering.** An out-of-range LP alert that fires (ADR-0093) at 03:00 while the viewer is minimized or closed is durably recorded in the alerts table and shows up when the window is next opened — but nothing actively surfaces it. ADR-0017's own negative section flagged exactly this: it is "not acceptable as a notification mechanism for 'the agent just finished … while you weren't looking'; that's a future feature (system notifications, persistent inbox)." The open ADR backlog carries "OS-native notification transport" as an explicit unwritten decision.

The renderer cannot raise an OS notification itself: per ADR-0008 security defaults it runs with `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`, and reaches nothing outside the typed IPC bridge. The Electron **main** process can (`new Notification(...)`), and the registered IPC domains today are app / dialog / shell / sidecar only — there is no notification handler, no `Notification` import, no tray. So this is a genuine new-IPC-channel decision, which by project rule routes through architect.

There are three distinct reach levels, and they cost very differently: (1) window open — in-app toast, already shipped; (2) app running but window minimized/unfocused — needs an OS notification raised from main; (3) app fully closed — needs a separate always-on tray/supervisor process, which ADR-0016 deliberately deferred (its Alternative B). The user asked for level 2.

## Decision

We will add a **single new renderer→main IPC channel** — a `notification:show` handler in Electron main with a typed preload bridge (`window.<bridge>.notification.show(payload)`), following the existing app/dialog/shell/sidecar bridge pattern and ADR-0008 discipline. The renderer's existing SSE subscriber, on receiving an alert envelope it should surface passively (initially `defi.position_alert`, and the channel is written to accept any condition-only alert so `alert.triggered` can opt in later), calls the bridge; main raises a native `Notification` with the alert's condition-only title/body. **Main raises the OS notification only when the app window is not focused**, so a user staring at the chart gets the in-app toast and not a redundant OS toast for the same event. Clicking the notification focuses/restores the window. The payload is Zod/type-validated at the preload boundary, carries condition text only (no directive, no secrets, honouring the ADR-0029/0055 boundary), and is capped in length.

This delivers reach level 2 (notify while the app is running but the window is minimized/closed). It **explicitly does not** deliver level 3 (notify while Electron is fully quit) — that remains ADR-0016's deferred tray/supervisor decision. When level 3 is wanted, a background process can reuse this same channel and payload shape; nothing here forecloses it.

## Consequences

### Positive
- Closes the exact gap the user hit and that ADR-0017 named: passive surfacing survives a minimized/unfocused window, so a 03:00 out-of-range alert actually reaches the user.
- One small, well-scoped IPC channel that obeys the existing bridge conventions; the renderer stays sandboxed and Node-free.
- Alert-source-agnostic: built for `defi.position_alert` first, but the market-alert path (`alert.triggered`) can adopt it later with no new channel — a general OS-notification transport, not a one-off.
- The focused-window suppression avoids double-signalling (OS toast + in-app toast) for the same event.

### Negative
- **First OS-integration surface** in the app — platform behaviour now matters. Electron's `Notification` maps to the Windows action-center toast (the user's platform); macOS/Linux behave differently and are untested here. Windows-first is stated, not hidden.
- Does **not** notify while the app is fully closed — a real, documented gap. The user must leave the sidecar *and* the Electron shell running to get OS notifications (the sidecar alone, per ADR-0016, keeps detecting and persists the alert, but only the shell can raise the OS toast). Level-3 delivery is a future ADR.
- A new attack-surface line item, however small: a renderer compromise could spam OS notifications. Mitigated by payload validation + length cap + condition-only content; no secret or action ever crosses this channel.

### Neutral
- No new dependency: Electron's built-in `Notification` is used, not `node-notifier` or similar (ADR-0012/0013 dependency discipline favours this).
- No CSP change — this is an IPC channel, not a network or resource-load change.

## Alternatives considered

### Alternative A — In-app toast only (ship nothing new)
Rely on the existing `AlertToaster` + Alerts view; the alert is always durably in the table on next open. Rejected: it misses the minimized/closed-window case, which is precisely what the user asked for — an alert they'll see without already staring at the app.

### Alternative B — Tray / background supervisor for notify-while-fully-closed
A persistent tray or supervisor process that outlives the Electron window and can raise notifications with no window at all. Deferred, not chosen: ADR-0016 already deferred exactly this (its Alternative B — automated restart/supervision), it is materially heavier (a second always-on process, lifecycle, single-instance coordination), and it is not needed for the stated requirement. This ADR is deliberately layered so that supervisor can be added later and reuse this channel.

### Alternative C — `node-notifier` (or another notification dependency)
Rejected: Electron's built-in `Notification` covers the requirement with zero new dependency, and every direct dependency here pays the ADR-0012 cooldown + ADR-0013 exact-pin cost. No reason to add one for a capability the platform already gives us.
