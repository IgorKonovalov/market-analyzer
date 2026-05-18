# ADR-0006 — Persistence: SQLite for data, JSON for user config

> **Status:** accepted
> **Date:** 2026-05-17
> **Related plan(s):** [0001-bootstrap](../plans/0001-bootstrap.md)
> **Related ADRs:** [ADR-0003](0003-vendoring-strategy.md), [ADR-0004](0004-strategy-interface.md)
> **Amendment:** see [ADR-0009](0009-rewrite-data-layer-in-house.md) — references in this ADR's body to "the vendored data layer" / "vendored data services" / "non-vendored adapter code" now refer to the in-house equivalents.

## Context

An earlier abandoned Tauri-era bootstrap draft deliberately deferred persistence — the BTC-screener walking skeleton was in-memory only. The current bootstrap ([Plan 0001](../plans/0001-bootstrap.md)) makes an OHLCV chart the first end-to-end feature, which means we are caching historical bars from day one. We need a persistence story before phase 2 of the bootstrap lands.

We persist three categories with different shapes:

1. **Cached market data** — OHLCV bars, screener snapshots, sentiment samples. Append-mostly, time-series-shaped, queried by `(symbol, timeframe, ts-range)`. Will reach millions of rows.
2. **Backtest runs and strategies** — strategy source/metadata (per [ADR-0004](0004-strategy-interface.md)), backtest run parameters, equity curves, trade logs. Relational: trades reference runs, runs reference strategies. Read-heavy after write.
3. **User configuration** — preferences, watchlists, layout state. Small (kilobytes). Power users expect to edit this in a text editor, not through the UI.

Forces:

- A single-file data backup is operationally simple — copy `app.db`, done.
- Power users want to hand-edit config. SQLite rows are not hand-editable; a JSON or TOML file is.
- OHLCV scan performance matters for backtests, but is not yet the bottleneck. SQLite with a `(symbol, timeframe, event_ts)` index handles tens of millions of rows comfortably; we revisit if profiling shows otherwise.
- Backtest determinism (per `best-practices.md`) requires that anything we cache distinguishes the *event timestamp* from the *ingestion timestamp*. A bar from "yesterday's close" fetched today must replay identically if fetched again next week.
- The vendored data layer (per [ADR-0003](0003-vendoring-strategy.md)) currently caches in-memory only; persistent caching is our concern, not upstream's, and lives in non-vendored adapter code.

## Decision

We will persist all application data in a single SQLite database at the OS-appropriate app-data directory (`%APPDATA%/market-analyser/app.db` on Windows; XDG equivalents on other platforms). User configuration lives in a separate hand-editable `config.json` at the same directory. Secrets (API keys, the per-launch IPC shared secret) live in `secrets.json` with restrictive file permissions and are never written to SQLite or surfaced in logs.

The SQLite schema is owned by Alembic migrations under `src/market_analyser/persistence/migrations/`. All writes go through a thin repository layer (`src/market_analyser/persistence/`); callers never write SQL inline.

## Consequences

### Positive
- One-file backup story for the data: `app.db` is self-contained and transactional.
- Atomic writes — a backtest run plus its trades land in one transaction or not at all.
- `config.json` is editable in any text editor when the app is closed, and validated against a pydantic model on load.
- Standard indexing handles point queries (`get_bars(symbol, timeframe, start, end)`) without further work.
- Test fixtures are trivial: point the repository at `sqlite:///:memory:`.
- Aligns with [ADR-0004](0004-strategy-interface.md)'s strategy-as-module discovery: strategy files live in `src/market_analyser/strategies/`, but their persisted metadata (id, version, last-modified) lives in SQLite, keyed by the same `id` as the module's `META` constant.

### Negative
- Bulk OHLCV scans (e.g. walk-forward backtesting across many symbols) are slower than Parquet would be. We accept this until profiling shows it dominates a backtest run; a Parquet-for-OHLCV migration is captured as a followup in Plan 0001.
- Two stores (SQLite + `config.json`) means two backup steps, and the repository layer must guard against cross-store references going stale (e.g. `config.json` names a strategy `id` that has since been deleted from SQLite). Validated on read.
- SQLite's writer model is single-writer; not a problem for a single-user desktop app, would be if we ever ran multiple writer processes.
- Migrations must be tested forward and backward — a broken migration locks a user out of their own data. Mitigation: every migration has a paired downgrade test, and a known-good snapshot is included in `tests/fixtures/`.
- Adds `sqlalchemy` and `alembic` as runtime dependencies. Both are mature.

### Neutral
- The repository layer is a chokepoint for instrumentation (slow-query log, cache-hit metrics) — useful, but not yet exploited.

## Alternatives considered

### Alternative A — All SQLite (config too)
Single store, single backup file. Rejected because power users explicitly want a text-editable config and rows in a SQLite table do not satisfy that. The cost of a second file is small versus the friction of pushing every config change through the UI.

### Alternative B — SQLite + Parquet for OHLCV
Faster bulk scans for backtests. Rejected for now because we have no measurements showing SQLite is the bottleneck, and a directory-of-parquet layout fragments the backup story. Captured as a followup; revisit when profiling earns it.

### Alternative C — JSON/TOML for everything
Hand-editable end to end. Rejected because backtest result sets (thousands of trades) and bar caches (millions of rows) are not appropriate for flat-file JSON: write atomicity, indexed reads, and concurrent-reader safety would all need to be rebuilt.

## Notes

- The `bars` table uses `(symbol, timeframe, event_ts)` as a composite primary key, with `event_ts` distinct from `ingested_at` so any historical state can be replayed for backtest determinism.
- `config.json` schema is enforced by a pydantic model on load; a malformed config refuses to start the sidecar rather than silently dropping fields.
- Secrets schema and rotation are out of scope for this ADR. We will revisit when the first authenticated external API beyond Yahoo Finance/TradingView is wired up.
- The persistence layer is non-vendored code per [ADR-0003](0003-vendoring-strategy.md) — it imports vendored data services through the [ADR-0007](0007-market-data-provider.md) provider abstraction, never edits them.
