# ADR-0011 — Bearer-secret transport: env-var, not argv

> **Status:** accepted
> **Date:** 2026-05-18
> **Related plan(s):** [0001-bootstrap](../plans/0001-bootstrap.md), [0004-bootstrap-review-followups](../plans/0004-bootstrap-review-followups.md)
> **Related ADRs:** [ADR-0002](0002-ipc-local-http.md) (extends — transport decision unchanged; only the secret-injection mechanism flipped)

## Context

[ADR-0002](0002-ipc-local-http.md) picked localhost HTTP with a per-launch bearer-token shared secret as the renderer↔sidecar transport. The original draft of that ADR specified the secret would be passed from the Electron main process to the Python sidecar via `argv` (`--secret=<hex>`), and explicitly called out the downside in its Consequences section:

> Shared-secret in argv is visible in process listings. A second local user on the same machine can read `argv` of the sidecar process and recover the bearer secret. The threat model accepts this for a single-user desktop app; if multi-user-host becomes a concern, switch to env-var injection at spawn (captured as a followup in Plan 0001).

Plan 0001 carried this as an open question. Plan 0004 phase 3 closed it: the implementer needed to decide on a concrete replacement transport. The forces:

- **Other local users can read argv.** On Windows: `Get-CimInstance Win32_Process | Select-Object CommandLine`. On Linux: `/proc/<pid>/cmdline`. On macOS: `ps -ww`. No special permissions required for processes owned by the same user account; cross-user reads are gated by `ps -A` permissions on POSIX but not on Windows. For a desktop app intended to ship to ordinary users, "another logged-in user on a shared machine can see your bearer secret in `ps`" is a defect, not a documented limitation.
- **The sidecar has no child processes.** The Python process serves HTTP, talks to SQLite, and reads Yahoo over outbound TLS. It does not fork or spawn anything that would inherit its environment. So the standard objection to env-vars (descendant processes inherit them) does not apply.
- **The Electron main process already isolates env. completely.** When `spawn()` is called with an explicit `env:` option, the child receives exactly that environment — there is no implicit leak from the parent unless the parent's env is spread in. The sidecar spawn deliberately does both (`env: { ...process.env, MARKET_ANALYSER_SECRET: secretToken }`) because the Python interpreter needs `PATH` and `PYTHONHOME` to resolve correctly; the secret rides alongside those without polluting any other surface.
- **Stdin handshake is the marginally-most-secure option** but adds complexity: the supervisor would need to keep stdio piped (giving up the existing `stdio: ['ignore', 'pipe', 'pipe']` simplicity for stdout-line capture), write the secret + newline before closing stdin, and the sidecar would need to consume stdin during boot before uvicorn binds. The win — env vars are visible to descendant processes and (transiently) in `/proc/<pid>/environ` on Linux — is small for a sidecar with no children, where the parent's own argv would already have been visible under the original scheme.

## Decision

The Electron main process passes the per-launch bearer secret to the Python sidecar via the `MARKET_ANALYSER_SECRET` environment variable injected at `spawn()` time, not via `argv`. The sidecar reads the value from `os.environ["MARKET_ANALYSER_SECRET"]` on startup and refuses to start (exits non-zero with a stderr message naming the variable) if it is unset or empty.

The secret is **never** written to disk, **never** logged, **never** appears in `argv` of any process, and lives in process memory of exactly two processes: the Electron main process and the Python sidecar. Rotation on sidecar restart is unchanged from [ADR-0002](0002-ipc-local-http.md): the supervisor generates a fresh 32-byte hex string per spawn, including each post-crash restart, and pushes the new value to the renderer via the existing `sidecar:status` event with `kind: 'restarted'`.

The localhost-HTTP transport, bearer-token enforcement, 127.0.0.1-only bind, and `/healthz` auth-exemption from [ADR-0002](0002-ipc-local-http.md) are all unchanged. This ADR only specifies how the shared secret is injected at sidecar boot.

## Consequences

### Positive
- Other local users can no longer read the bearer secret via `ps` / `Get-CimInstance` / `/proc/<pid>/cmdline`. The argv of the sidecar is now `python -m market_analyser.api --port=<n>` — no secret.
- The change is mechanical and well-localized: one `spawn()` call in `desktop/electron/sidecar.ts`, one read in `src/market_analyser/api/__main__.py`. No protocol-level change.
- The startup contract is sharper: missing-or-empty secret is a hard exit with a named error, not a quietly-permissive default. `tests/test_api_startup.py` covers the three paths (set / missing / empty).

### Negative
- **Env vars are visible to descendant processes.** Today the sidecar has none; if a future plan spawns child processes from the sidecar (e.g., a worker pool for backtests), the new code must strip `MARKET_ANALYSER_SECRET` from the inherited environment before exec. We accept this as a future-care item rather than pre-engineering for it; the alternative (stdin handshake) costs complexity now for a marginal future win.
- **Env vars are visible in `/proc/<pid>/environ` on Linux** to processes owned by the same user. The risk is symmetric to the argv risk we just eliminated, but with one important difference: `ps` / `Get-CimInstance` are user-facing tools casually run by admins and helpdesks; `/proc/<pid>/environ` requires deliberate inspection. The change moves the secret from "trivially leaked" to "leaked under deliberate inspection by a same-user process" — a meaningful improvement for the threat model ([ADR-0002](0002-ipc-local-http.md)'s "single-user desktop app").
- **On Windows, env vars are scoped to the spawned process** (not the user account or system); they vanish when the sidecar exits. On Linux/macOS, the value is in the kernel's per-process env block until the process exits. In both cases, lifetime matches the sidecar's lifetime — same as before.

### Neutral
- `MARKET_ANALYSER_SECRET` is the same name in dev (`pnpm dev`), production, and tests. No environment-specific aliasing.

## Alternatives considered

### Alternative A — keep `argv`
Rejected because the cross-process visibility is a real defect on every supported OS; the documented "single-user host" assumption is too aggressive for a desktop app whose threat model includes "another user on the same Windows machine".

### Alternative B — stdin handshake
The supervisor would write `<secret>\n` to `child.stdin` and close it; the sidecar would `readline()` before uvicorn binds. Marginally more secure (no `/proc/<pid>/environ` exposure), but more complex on both ends: keeping stdio piped during boot, handling the case where stdin closes early, and ordering the read against the existing stdout-line `PORT=` capture. Rejected because the sidecar has no child processes, so the env-var disadvantage we're avoiding is mostly theoretical for the foreseeable future.

### Alternative C — short-lived token file under `%APPDATA%` / `$XDG_RUNTIME_DIR`
The supervisor writes the secret to a 0600-permissioned file, passes the path via argv, sidecar reads + unlinks. Cleaner audit trail. Rejected because filesystem ACLs on Windows are weaker than POSIX (the user's own processes can read 0600 files in their own profile), the cleanup story on crash is fragile (orphaned token files), and persistence to disk — even momentary — increases the surface that has to be audited for log/backup leakage.

## Notes

- The implementation landed in commit `b669a3e` (feat(api): move bearer secret from argv to MARKET_ANALYSER_SECRET env var) as part of Plan 0004 phase 3.
- `src/market_analyser/api/__main__.py:34` defines `SECRET_ENV_VAR = "MARKET_ANALYSER_SECRET"`; the test contract is at `tests/test_api_startup.py`.
- This ADR closes the "argv-snooping" open question carried in Plan 0001's "Risks & open questions" section and the followup-link from [ADR-0002](0002-ipc-local-http.md) "Negative consequences".
