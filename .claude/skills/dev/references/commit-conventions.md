# Commit conventions — dev

This repo enforces Conventional Commits via `commitizen` in the commit-msg pre-commit hook (per Plan 0001 phase 1). Commits that don't match the convention will be rejected.

## Format

```
<type>(<scope>): <subject>

[optional body — wrap at 72 chars]

[optional footer — references, BREAKING CHANGE, etc.]
```

- **type**: one of the allowed types (see table below).
- **scope**: the package or area touched. Optional but encouraged — it scans the log far better.
- **subject**: imperative, lowercase, no trailing period, ≤ 72 chars.

## Types used in this repo

| Type       | When to use                                                              |
|------------|--------------------------------------------------------------------------|
| `feat`     | A new user-visible capability — new endpoint, new UI component, new module exporting something a consumer will call. |
| `fix`      | A bug fix in existing code.                                              |
| `refactor` | Internal restructuring with no behavior change.                          |
| `test`     | Adding or fixing tests. (If a test is for new code in the same phase, fold it into the `feat` commit instead.) |
| `docs`     | Markdown, comments, docstrings, ADRs, plans, READMEs.                    |
| `chore`    | Misc maintenance that doesn't fit elsewhere — dependency bumps, file moves, etc. |
| `build`    | Build-system or external-dependency changes (`pyproject.toml`, `package.json`, lockfiles). |
| `ci`       | CI config (`.github/workflows/`, `.pre-commit-config.yaml`).             |

## Scopes used in this repo

Pick the smallest scope that's still meaningful. If a commit truly spans many scopes (rare — usually a sign it should be split), omit the scope.

| Scope          | Area                                                          |
|----------------|---------------------------------------------------------------|
| `api`          | `src/market_analyser/api/`                                    |
| `data`         | `src/market_analyser/data/`  (incl. adapters)                 |
| `persistence`  | `src/market_analyser/persistence/`                            |
| `contracts`    | `src/market_analyser/contracts/`                              |
| `strategies`   | `src/market_analyser/strategies/`                             |
| `backtest`     | `src/market_analyser/backtest/`                               |
| `config`       | `src/market_analyser/config.py`, `config.json`                |
| `desktop`      | `desktop/`  (Electron shell + renderer)                       |
| `tooling`      | `pyproject.toml`, `uv.lock`, `.pre-commit-config.yaml`, `mypy` config |
| `ci`           | `.github/workflows/`                                          |
| `docs`         | Anything under `docs/`                                        |

## Examples

```
feat(api): add /healthz endpoint with auth bypass
```

```
feat(data): add MarketDataProvider Protocol with NotImplementedError stubs
```

```
feat(data): implement YahooAdapter.fetch_ohlcv with in-house fetch

Validates response shape with pydantic, rejects bars with NaN close
or negative volume per best-practices.md. Network test marked
@pytest.mark.network so CI doesn't depend on Yahoo uptime.
```

```
build(tooling): pin mypy=1.x and enable strict mode

Per Plan 0001 phase 1. All existing code passes; future phases must
maintain --strict cleanliness.
```

```
ci: add release.yml stub triggered on v*.*.* tags

Stub builds and checksums but does not publish — packaging plan
will wire publish later.
```

```
test(persistence): cover BarRepository upsert deduplication on (symbol, timeframe, event_ts)
```

## When to split into multiple commits

Default to splitting when a phase has logically independent pieces. The architect's review is easier when each commit tells one story.

**Good split for Plan 0001 phase 2:**

1. `feat(data): declare Bar/Quote pydantic models in data/types.py`
2. `feat(data): add MarketDataProvider Protocol with NotImplementedError stubs`
3. `feat(data): add _fetch_yahoo_ohlcv (urllib + JSON parsing)`
4. `feat(data): implement YahooAdapter with response validation`
5. `feat(api): add /ohlcv route delegating to DefaultMarketDataProvider`
6. `test(data): cover provider protocol introspection and yahoo adapter validation`

**Bad: a single `feat: implement phase 2` commit** — opaque, hard to bisect, hard to review.

## When NOT to split

- Tests for the same phase ship together with the production code, in a single `feat` commit, when they're tightly coupled to that code.
- Cross-file mechanical renames go in one commit, not per-file.
- A bugfix that needs a test goes in one `fix` commit.

## What every commit must NOT contain

- Secrets, tokens, API keys, `.env` contents, bearer secrets — not in the diff, not in the message, not in the body.
- `--no-verify` shortcuts. If the pre-commit hook fails, fix the cause.
- Co-author trailers, unless the user explicitly asks for them.
- File-staging via `git add -A` or `git add .`. Name files explicitly; you're responsible for what enters the index.
