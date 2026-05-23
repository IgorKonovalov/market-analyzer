# Architectural best practices — market-analyser

The longer-form checklist the architect skill grounds reviews against. Each item explains *why* it matters in this specific project — generic advice is too easy to ignore.

## Layering

The project has three logical layers. Cross-layer imports should only go downward, never upward.

```
UI layer        (frontend code — Electron renderer)
  ↓ talks to (IPC only)
Service layer   (Python sidecar — request handlers, orchestration)
  ↓ calls
Data layer      (in-house — providers, indicators, backtest)
```

**What goes wrong without this:**
- UI code calling data layer directly bypasses validation/caching and breaks when the data layer evolves.
- Data layer code importing service-layer concerns (request objects, auth) makes the data layer untestable in isolation.

**Watch for:**
- UI code that imports anything under `src/market_analyser/data/`.
- Data layer code that knows about HTTP, JSON-RPC, or the UI's component names.

## Determinism in backtests

Backtests must produce **byte-identical** results when re-run with the same inputs. This is non-negotiable — non-deterministic backtests turn statistically into "luck of the seed" and are worse than useless because they look credible.

**Sources of non-determinism to watch:**
- `random` / `np.random` without an explicit seed.
- `dict` iteration order in Python <3.7 (we're 3.10+, so this is fine — but `set` iteration order still isn't guaranteed).
- `datetime.now()` or `time.time()` in strategy logic.
- Reading current bar from `t+1` data (lookahead bias — both a correctness bug and a determinism risk).
- Floating-point reduction order across threads.

**Enforcement:** any backtest entrypoint must accept a `seed: int` parameter and use it to initialize all random sources.

## Lookahead bias

The cardinal sin of backtesting. Strategy code at time `t` must not see data from `t+1` or later.

**Common ways it sneaks in:**
- Closing-price-based signals that get the *current* bar's close before the bar has closed.
- Indicator implementations that center their window around `t` instead of trailing it.
- Train/test splits done by random sampling instead of by time.

When reviewing strategy code, draw the time arrow explicitly: at decision time `t`, what data is available? Anything `>t` is a bug.

## Input validation at boundaries

The data layer touches external services that can and do return garbage. Validate at the boundary — once data is inside the system, code downstream should be able to trust it.

**Concrete:**
- Every external API call (TradingView, Yahoo, Reddit, RSS) should validate response shape before returning. Use `pydantic` models or explicit checks.
- Reject bars where `high < low`, `close < 0`, `volume < 0`, `ts` is in the future, or any field is `None`/`NaN`.
- Log validation failures, don't silently filter — silent filters hide upstream bugs.

## Secret handling

- All secrets via `.env`, loaded once at sidecar startup. Never read mid-request.
- Secrets must never end up in: logs, error messages shown to the UI, plan/ADR documents, mermaid diagrams, commit messages, or test fixtures.
- The data layer gets secrets passed in via dependency injection, not by reading `os.environ` directly. This keeps the data layer testable and the secret-handling surface small.

## Coupling between sibling-skill domains

The three sibling skills (`strategy-author`, `backtester`, `ui-builder`) should be **independently buildable**. A change in the strategy author's output should not require coordinated changes in the backtester or the UI.

**Watch for:**
- `from market_analyser.strategies import ...` inside backtest code that doesn't go through a clear strategy-protocol abstraction.
- UI code that knows the internal structure of a backtest result object beyond what's in a documented schema.
- Strategy code that imports anything from the UI or the backtest engine directly.

The right pattern: each sibling produces / consumes via a small, named contract (a `pydantic` model, a Python `Protocol`, a JSON schema). Contracts live in a shared `src/market_analyser/contracts/` module so all three skills can reach them without reaching into each other.

## God modules

Files over ~400 lines doing five jobs. Common in projects that vendor from another codebase, because the source repo's module might do more than we need.

When you spot one in review, the question to ask is: *what's the natural seam?* Usually it's a service that's both fetching data and computing on it. Split fetching (cheap, retryable, mockable) from computation (pure, fast, easy to test).

## Hidden state

Module-level mutables — especially in the data layer — are how reproducibility dies. Look for:

- `_cache = {}` at module top level.
- `_last_fetch_ts = None` updated by the first call.
- Singletons that hold connections or auth state.

If state is truly needed, make it explicit: a `class Cache` you instantiate, or a function that takes/returns state. Tests must be able to construct fresh instances.

## Async / threading discipline

- The Python sidecar will likely become async (FastAPI + uvicorn). Mixing sync data-layer code into an async handler is fine if the data-layer call is fast and CPU-bound; it's a problem if it blocks on network.
- For external HTTP calls in the data layer, prefer `httpx` (sync + async) over `requests` (sync only). This lets us migrate the data layer to async without rewriting call sites.
- Never call `asyncio.run()` from inside a request handler — it'll deadlock.

## Documentation as a first-class deliverable

A plan isn't done until its `Status:` line is flipped to `done` and any diagram the change invalidates has been refreshed. This is part of "definition of done", not a nice-to-have. Doc rot kills the architect skill's usefulness over time.
