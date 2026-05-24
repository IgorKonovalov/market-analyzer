# ADR-0022 — Sidecar shutdown cleanup runs in the app lifespan, not a post-serve `finally`

> **Status:** accepted
> **Date:** 2026-05-24
> **Related plan(s):** none (bug fix; refines an existing contract rather than building a feature)
> **Related ADRs:** [ADR-0016](0016-standalone-sidecar-mode.md) (**refined here** — its lockfile-cleanup-on-shutdown contract), [ADR-0014](0014-mcp-as-second-sidecar-protocol.md) (the MCP session manager already composed into the lifespan), [ADR-0011](0011-bearer-secret-transport.md) (the bearer the lockfile carries)

## Context

[ADR-0016](0016-standalone-sidecar-mode.md) established the standalone sidecar's `sidecar.lock` and a contract documented in `__main__.py`: the lockfile is "written atomically at boot and removed on clean shutdown (SIGTERM / SIGINT / normal exit)." The original mechanism is a `try/finally` wrapping the serve call (`__main__.py`):

```python
try:
    asyncio.run(_serve(sock, ...))     # _serve() awaits uvicorn's server.serve(...)
finally:
    sock.close()
    remove_lockfile(lockfile_path)
```

This mechanism is **broken for SIGTERM**, and the break is not hypothetical — it fails two CI tests on the Ubuntu leg (`test_sigterm_removes_lockfile_before_exit`, `test_renderer_secret_rotates_per_sidecar_boot`) and it affects the real `POST /settings/stop` / `stop` subcommand on POSIX (`settings_stop.py` raises SIGTERM on POSIX, SIGINT only on Windows).

Root cause, verified against the pinned uvicorn (0.46.0): `uvicorn/server.py`'s `serve()` wraps the run loop in a `capture_signals()` context manager that, after a signal-driven graceful shutdown, **restores the original signal handler and re-raises the captured signal** (`signal.raise_signal`). For SIGTERM the restored default disposition terminates the process immediately — and the re-raise happens *inside* `await server.serve(...)`, so `_serve` never returns and the `finally` above never runs. SIGINT survives only by luck: its default handler raises `KeyboardInterrupt`, which unwinds the stack (running the `finally`) instead of terminating immediately. This almost certainly regressed silently at a uvicorn upgrade — older uvicorn did not re-raise.

The decisive constraint: **any cleanup placed after `server.serve()` is unreachable on a re-raised SIGTERM.** The only place that reliably runs before the re-raise is uvicorn's own graceful-shutdown sequence — which includes the ASGI **lifespan shutdown** (`app.py` already defines a `lifespan`).

## Decision

We remove the lockfile during the **app lifespan shutdown**, not in a post-`serve()` `finally`. `create_app` gains a generic `on_shutdown: Sequence[Callable[[], None]] | None` seam; its lifespan invokes those callbacks after `yield` (in a `finally`, so they run on any graceful shutdown). `__main__` supplies a callback that calls `remove_lockfile(lockfile_path)`, keeping lockfile knowledge in `__main__` (it is a process-level concern) while `create_app` stays lockfile-agnostic. uvicorn runs lifespan shutdown before its signal re-raise, so the lockfile is cleaned on every path: SIGTERM, SIGINT, `/settings/stop`, and normal exit. The existing `__main__` `finally` stays as an idempotent backstop for paths that never enter `serve()`.

We **accept `exit -15` (terminated by SIGTERM) as a valid shutdown outcome.** uvicorn still re-raises SIGTERM after the lifespan cleanup, so a SIGTERM'd sidecar reports signal-termination, which is the correct Unix convention. Nothing depends on exit code 0: the `stop` path acks over HTTP, and Electron's attach uses the lockfile + PID-liveness, not the sidecar's exit status. Tests that asserted `rc == 0` after SIGTERM relax to accept `-signal.SIGTERM`.

## Consequences

### Positive
- The documented ADR-0016 shutdown contract is actually honored, on every shutdown path and both platforms — not just where `KeyboardInterrupt` happens to unwind.
- `create_app` gains a reusable, lockfile-agnostic shutdown seam; future process-level cleanup (temp files, sockets) can hang off the same hook.
- The fix lives in the sanctioned ASGI shutdown lifecycle, so it is robust to uvicorn's signal-handling internals rather than fighting them.

### Negative
- A SIGTERM'd sidecar exits `-15`, not `0`. Anyone reading exit codes (future supervisor work, scripts) must treat `-SIGTERM`/143 as a normal stop. Recorded here so it is not mistaken for a crash.
- Lockfile lifecycle is now slightly split: written in `__main__` (it needs the bound port), removed via the lifespan hook (+ the `__main__` `finally` backstop). The asymmetry is deliberate and documented; do not "simplify" the removal back into the `finally` — that reintroduces this bug.
- Couples the lockfile-cleanup timing to uvicorn's lifespan-shutdown ordering. If uvicorn ever stops running lifespan shutdown before re-raising, this regresses — pinned by the two CI tests.

### Neutral
- The `_wait_until_serving` readiness gate added during diagnosis (commit `a3d472e`) is retained as a harmless pre-signal robustness measure; it was not the fix.

## Alternatives considered

### Alternative A — Own SIGTERM handler in `__main__` that removes the lockfile and `os._exit(0)`
Install our own handler before `serve()`; uvicorn captures and restores it, then re-raises into it. It would make both tests pass unchanged (lockfile gone, exit 0). Rejected as the primary mechanism: it relies on the precise interleaving of uvicorn's capture/restore/re-raise and on raising through a signal handler during `asyncio.run`, which is fragile across uvicorn/Python versions. The lifespan hook is the version-robust, idiomatic seam.

### Alternative B — Relax the contract: accept a stale lockfile on SIGTERM
Do not change production; let SIGTERM leave the lockfile and rely on the next boot's stale-lockfile takeover (`test_stale_lockfile_is_taken_over`) to recover, updating the `__main__` docstring + tests to match. Rejected: it weakens a documented safety property (a lingering lockfile is a single-instance false-positive until the next boot) to dodge a cleanly fixable bug.

## Notes

- The SIGTERM lockfile tests skip on Windows (the dev machine), so the fix is verifiable only on CI's Ubuntu leg. The done-when for the implementing change must require both tests green on Ubuntu.
- Implementation handed to `dev` as a bug fix (no numbered plan): the `on_shutdown` seam in `create_app` + the `__main__` wiring + relaxing `test_renderer_secret_rotates_per_sidecar_boot`'s exit-code assertion.
