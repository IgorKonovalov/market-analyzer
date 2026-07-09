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

## Execution modes

`spawn-sidecar.mjs` (the `[sidecar]` child) has three modes. The wrapper picks one at boot based on whether a sidecar is already running and whether `--keep-sidecar` was passed.

| Mode             | Trigger                                       | Ctrl+C behaviour                                  |
|------------------|-----------------------------------------------|---------------------------------------------------|
| **default**      | No `--keep-sidecar`, no live sidecar          | Kills the whole sidecar subtree (POSIX: process group; Windows: `taskkill /T /F`). |
| **--keep-sidecar** | `--keep-sidecar` passed, no live sidecar    | Detaches the child (`unref` on POSIX, no-op on Windows where the child already has its own console) and exits. The sidecar survives. |
| **reuse**        | Live sidecar detected via `sidecar.lock`      | No kill attempted — the wrapper did not spawn the sidecar, so it must not kill it. (Kill-only-what-you-spawned.) |

Reuse mode fires whenever `<data-dir>/sidecar.lock` exists AND `is_owner_alive(record)` returns true (the same PID + `process_create_time` cross-check ADR-0016 uses). Typical sequence:

```
pnpm dev:all -- --keep-sidecar     # spawn detached, Ctrl+C → sidecar survives
pnpm dev:all                        # reuse the survivor, Ctrl+C → sidecar still survives
uv run python -m market_analyser.api stop   # explicit stop when you're done
```

Kill-only-what-you-spawned matters because the user might run `pnpm dev:all` (no flag) against a sidecar from a prior `--keep-sidecar` session, expecting "default mode kills on Ctrl+C". The wrapper does NOT do that — the previous session is the rightful owner. If you want to stop the survivor, use the explicit `stop` subcommand.

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

## The full command reference

Every MCP tool, REST route, and SSE event is documented under [`docs/reference/`](../reference/) — start at the [index](../reference/README.md). The per-tool entries in [`mcp-tools.md`](../reference/mcp-tools.md) carry each tool's description, parameters, return shape, and a source link; [`rest-api.md`](../reference/rest-api.md) and [`events.md`](../reference/events.md) do the same for the renderer routes and the SSE vocabulary. The tree is generated by introspecting the live, fully-wired sidecar (so it can't drift from behaviour) and CI-gated against staleness ([ADR-0064](../architecture/adrs/0064-generated-sidecar-api-reference.md)). Regenerate after any surface change with `pnpm gen:api-docs` (or `uv run python -m market_analyser.apiref`).

## Screening

The `screener_query` MCP tool ([Plan 0009](../architecture/plans/0009-resilience-and-tradingview-screener.md)) lets the agent find candidates across a whole market universe, not just symbols you name. Ask in natural language and the agent translates to a filter query:

- *"find oversold large-cap US stocks on the daily"* — `RSI < 30` plus a `market_cap_basic` floor, `market="america"`.
- *"show me crypto pairs with a bullish MACD cross"* — MACD filters, `market="crypto"`.
- *"what NASDAQ tickers have a Bollinger-band squeeze right now"* — BB-width filters, `exchange="NASDAQ"`.

Filters are a dict keyed by TradingView column with operator sub-dicts, e.g. `{"RSI": {"lt": 30}, "market_cap_basic": {"gte": 1e10}}`; operators are `lt`/`lte`/`gt`/`gte`/`eq`/`ne`. Unknown columns are rejected (strict by design — a typo fails fast rather than returning a wrong screen).

> **Screener results are wall-clock-sensitive.** "RSI < 30 right now" is not the same query five minutes from now, so the tool has **no historical replay** — there is no `as_of` parameter, and each result carries a `queried_at` timestamp. (Point-in-time OHLCV with `as_of` is a separate path; the screener is live-only.)

The live upstream is TradingView's public scanner (reverse-engineered; it may change without notice). The end-to-end smoke is network-marked and local-only — run it with:

```
uv run pytest -m network tests/integration/test_screener_end_to_end.py
```

## Smoke check

`pnpm smoke` ([Plan 0016](../architecture/plans/0016-golden-path-smoke.md)) is the one-command "is the shipped product still standing" check. It attaches to a running `pnpm dev:all` sidecar and drives a single end-to-end golden path through every shipped layer — `/healthz` identity → `get_ohlcv` (live Yahoo) → `show_chart` → `run_backtest` (with a determinism re-run) → `screener_query` (live TradingView) → annotation roundtrip + `highlight_pattern` → `/events` SSE liveness → the `strategies list` CLI — then deletes the annotations it wrote so a re-run starts clean.

It is **hybrid**: the script asserts the wire-level responses, and because the `show_*` / `run_backtest` tools publish to the SSE bus, the live viewer updates while it runs so you can eyeball the visual half.

Run it in two terminals from the repo root:

```
pnpm dev:all     # terminal 1 — boots sidecar + viewer (leave running)
pnpm smoke       # terminal 2 — drives the golden path against the live stack
```

With no sidecar running, `pnpm smoke` exits non-zero and prints a *"run `pnpm dev:all` first"* message — the visual half needs the viewer up.

It hits **live** Yahoo and TradingView, so it is **local-only and never a CI gate** — an upstream outage must not fail a push or tag. Each step prints one result line:

| Line            | Meaning                                                              | Exit impact            |
|-----------------|---------------------------------------------------------------------|------------------------|
| `PASS`          | The step's wire assertion held.                                     | —                      |
| `FAIL`          | *Our* integration broke (an assertion mismatch).                    | Process exits `1`.     |
| `UPSTREAM-DOWN` | Yahoo/TradingView was unavailable (typed `ResilientHttpError`/5xx). | Non-fatal; exit stays `0`, re-run later. |

So a clean run is "all `PASS`, exit 0"; an `UPSTREAM-DOWN` line means "their problem, try again", and any `FAIL` means "our problem, look here".

After the automated report, the driver prints a manual visual checklist — confirm each against the live viewer:

1. **AAPL daily candles** render in the viewer after the `show_chart` step.
2. A **bullish marker** lands on the AAPL chart after the `highlight_pattern` step.
3. **BacktestView** shows a non-empty equity curve + metrics after the `run_backtest` step.
4. The **screener reply** surfaces an "as of HH:MM" wall-clock (the `queried_at` timestamp).

## Agent mode and UI gestures

By default the viewer is a one-way surface: the agent pushes chart commands to it, but your clicks and drags stay local ([ADR-0021](../architecture/adrs/0021-renderer-to-agent-feedback.md)). **Agent mode** opens the reverse channel — when it's ON, three gestures send typed events to the sidecar that the agent can read. It is OFF by default: you decide when your gestures are visible to the agent.

The **Agent mode** toggle lives in the chart header, top-right (beside the Refresh button and the backfill spinner). Flipping it persists to `<data-dir>/agent_mode.json` and survives a sidecar restart. With it ON:

| Gesture            | How                                                                 | Event the agent sees      |
|--------------------|---------------------------------------------------------------------|---------------------------|
| **Range select**   | Click **Select range** (appears only in agent mode), then drag across the chart. The selection stays highlighted with its date range; `Esc` cancels/exits. | `ui.range_selected v1`    |
| **Bar click**      | Click a single candle. The clicked bar gets a marker.               | `ui.bar_clicked v1`       |
| **Toggle**         | Flipping agent mode itself.                                         | `ui.agent_mode_toggled v1`|

The agent reads these by calling the **`get_pending_ui_events`** MCP tool (the reliable path — it drains the buffer), or by reading the **`ui-events://recent`** resource (non-draining). Events are buffered only while agent mode is ON; flip it OFF and the agent sees nothing (the `POST /ui_events` route 403s server-side). The buffer is in-memory and clears on sidecar restart.

To test the loop: flip agent mode ON, drag-select ~2 weeks on the AAPL daily, then paste to Claude Code:

> *what happened in the range I just selected?*

Claude calls `get_pending_ui_events`, sees the `ui.range_selected` envelope, and summarises the cached bars for that window — you never type the dates. Same loop for a bar click (*"anything notable about this bar?"*).

> **Note on push latency.** The sidecar fires `notifications/resources/updated` on every gesture, but whether your MCP client surfaces that to the model is client-defined. The reliable contract is the **polling tool** — if the agent should react to gestures, it calls `get_pending_ui_events` when it's ready. Don't rely on an automatic push.

> **One viewer per machine.** Agent mode is a single sidecar-resident state, so the viewer is now single-instance — launching a second `pnpm dev:all` focuses the existing window instead of opening a new one.

## Troubleshooting

### Claude Code shows the MCP server as `disconnected` or returns 401

The MCP bearer rotated (the sidecar restarted with a fresh secret, or you ran the `Settings → Rotate MCP secret` flow). `pnpm dev:all` has already rewritten `.mcp.json` with the new bearer; Claude Code just needs to re-read it. Either bounce the MCP server in Claude Code's UI (`/mcp` → `market-analyser` → reconnect) or restart `claude` to reload `.mcp.json`.

### `wait-on` times out before the lockfile appears

Look at the `[sidecar]` log lines — the Python sidecar logs `PORT=<n>` once `uvicorn` is bound, and writes the lockfile just before that. If the sidecar didn't reach that line, something is wrong with `uv run python -m market_analyser.api` itself. Try running it directly to see the full traceback.

### "sidecar already running at PID `<N>`, port `<M>`; stop it first"

This comes from the Python sidecar itself, not from `pnpm dev:all`'s wrapper — it means the wrapper tried to spawn a fresh sidecar even though a live one already owns the lockfile. The wrapper's reuse path should have caught this; if you see this error, the most likely cause is a `MARKET_ANALYSER_DATA_DIR` override mismatch (the wrapper checked one path; the Python sidecar wrote to another). Confirm `echo $env:MARKET_ANALYSER_DATA_DIR` (PowerShell) or `echo $MARKET_ANALYSER_DATA_DIR` (POSIX) matches between the two contexts.

If the lockfile is genuinely stale (sidecar crashed without cleaning up), it will be taken over automatically on the next start. If you want to force-stop a live sidecar, use:

```
uv run python -m market_analyser.api stop
```

### I see two `python -m market_analyser.api` processes

This shouldn't happen — ADR-0016's lockfile + `process_create_time` check enforces single-instance. If you do see two, one of them is likely orphaned from a previous crashed dev:all run. Find both with `Get-Process python` (Windows) or `pgrep -f market_analyser.api` (POSIX), then stop the one whose PID matches `<data-dir>/sidecar.lock`'s `pid` field via `uv run python -m market_analyser.api stop`. Kill the other directly.
