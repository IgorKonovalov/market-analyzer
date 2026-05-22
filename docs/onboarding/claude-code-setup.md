# Claude Code setup for `market-analyser`

Claude Code is the primary control surface for this project ([ADR-0015](../architecture/adrs/0015-claude-code-primary-control-surface.md)). You drive the app by talking to the agent, which calls MCP tools on the sidecar; the Electron viewer renders the agent-issued chart commands ([ADR-0016](../architecture/adrs/0016-standalone-sidecar-mode.md), [ADR-0014](../architecture/adrs/0014-mcp-as-second-sidecar-protocol.md)). This page is the one-page setup for that workflow.

## TL;DR

Run a single command from the repo root:

```
pnpm dev:all
```

That boots the Python sidecar on an OS-picked port, writes `<repo>/.mcp.json` from the live port + the long-lived MCP bearer, starts the Electron viewer (which attaches to the running sidecar), and on Ctrl+C tears the sidecar down. Switch to Claude Code in another window and the MCP server is already reachable as `market-analyser` — no manual port juggling, no copy-pasting the bearer.

If you want the sidecar to outlive the viewer (overnight agent runs, scheduled scans), add the opt-out:

```
pnpm dev:all -- --keep-sidecar
```

(The `--` is pnpm's pass-through delimiter — the flag reaches the sidecar wrapper, not pnpm.)

## What `pnpm dev:all` actually does

Three children run under `concurrently`, all logging with `[name]` prefixes:

1. **`[sidecar]`** — `scripts/dev/spawn-sidecar.mjs` wraps `uv run python -m market_analyser.api --port=0 --dev-origin=http://localhost:5173`. The sidecar writes `<data-dir>/sidecar.lock` once `uvicorn` is bound; that lockfile carries the live port and the per-launch renderer bearer ([ADR-0016](../architecture/adrs/0016-standalone-sidecar-mode.md)). The wrapper handles Ctrl+C teardown and the `--keep-sidecar` opt-out.
2. **`[mcp-config]`** — `scripts/dev/write-mcp-config.mjs --watch` polls `<data-dir>/sidecar.lock`, reads the live port, reads the long-lived MCP bearer from `<data-dir>/mcp-secret.json` ([ADR-0014](../architecture/adrs/0014-mcp-as-second-sidecar-protocol.md)), and atomically rewrites `<repo>/.mcp.json` on every lockfile change.
3. **`[desktop]`** — `wait-on file:<data-dir>/sidecar.lock` then `pnpm --filter @market-analyser/desktop dev`. Electron only starts after the sidecar's lockfile is in place, so it always takes the idempotent-attach branch (no cold-spawn race).

The `<data-dir>` is the canonical shared data directory ([ADR-0020](../architecture/adrs/0020-shared-data-dir-contract.md)): `%APPDATA%\market-analyser` on Windows, `~/Library/Application Support/market-analyser` on macOS, `$XDG_DATA_HOME/market-analyser` on Linux. `MARKET_ANALYSER_DATA_DIR` overrides it verbatim.

## Where the files live

| File                          | Where                                | Lifecycle                                         |
|-------------------------------|---------------------------------------|---------------------------------------------------|
| `sidecar.lock`                | `<data-dir>/sidecar.lock`            | Written at sidecar boot; removed on clean exit.  |
| `mcp-secret.json`             | `<data-dir>/mcp-secret.json`         | Long-lived (rotates only on user request).       |
| `.mcp.json`                   | `<repo>/.mcp.json`                   | Auto-written by `pnpm dev:all`. Gitignored.       |

`.mcp.json` carries the MCP bearer inline. It is always gitignored (`.gitignore` blocks the path) and Claude Code reads it from the repo root at startup.

## Troubleshooting

### Claude Code shows the MCP server as `disconnected` or returns 401

The MCP bearer rotated (the sidecar restarted with a fresh secret, or you ran the `Settings → Rotate MCP secret` flow). `pnpm dev:all` has already rewritten `.mcp.json` with the new bearer; Claude Code just needs to re-read it. Either bounce the MCP server in Claude Code's UI (`/mcp` → `market-analyser` → reconnect) or restart `claude` to reload `.mcp.json`.

### `wait-on` times out before the lockfile appears

Look at the `[sidecar]` log lines — the Python sidecar logs `PORT=<n>` once `uvicorn` is bound, and writes the lockfile just before that. If the sidecar didn't reach that line, something is wrong with `uv run python -m market_analyser.api` itself. Try running it directly to see the full traceback.

### "sidecar already running at PID `<N>`, port `<M>`; stop it first"

A previous `pnpm dev:all --keep-sidecar` left the sidecar running. Stop it with:

```
uv run python -m market_analyser.api stop
```

Then `pnpm dev:all` again.

(A phase-3 follow-up lets the wrapper detect this case and reuse the already-running sidecar instead of erroring out. Until then, the explicit stop is the workflow.)
