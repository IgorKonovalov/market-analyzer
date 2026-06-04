# ADR-0038 — Third-party API-key storage: a `0600` secrets file, write-only to the renderer

> **Status:** accepted (Plan 0032 close, 2026-06-03 — phase 1 implemented this: `SecretsStore` over a `0600` `secrets.json`, env-override-first, write-only `GET/POST /settings/secret(s)`, value never logged/echoed; verified by `tests/persistence/test_secrets.py` + `tests/api/test_secrets_route.py`)
> **Date:** 2026-06-03
> **Related plan(s):** [0032](../plans/0032-defi-wallet-discovery.md) (phase 1)
> **Related ADRs:** [ADR-0011](0011-bearer-secret-transport.md) (the no-argv / no-log secret discipline this extends), [ADR-0006](0006-persistence-layout.md) (why not `config.json` or SQLite), [ADR-0020](0020-shared-data-dir-contract.md) (where the file resolves), [ADR-0002](0002-ipc-local-http.md) (the localhost+bearer channel a set-key call rides), [ADR-0034](0034-defi-portfolio-aggregator.md) (the first consumer)

## Context

Every TradFi data source shipped to date is **keyless** — Yahoo, TradingView, alternative.me, RSS feeds need no credential — so the app has never had to store a user-supplied API key. The "third-party data-source API keys" decision has sat in the open ADR backlog precisely because nothing forced it. The DeFi program forces it: [ADR-0034](0034-defi-portfolio-aggregator.md) (Zerion key), the deep on-chain adapters (RPC endpoint URLs, which embed keys for hosted providers), and The Graph (`GRAPH_API_KEY`) all need long-lived, user-provided credentials.

These are a different category from the two secrets the app already handles. The renderer↔sidecar **bearer** ([ADR-0011](0011-bearer-secret-transport.md)) is machine-generated, per-launch, rotated on every restart, never on disk. The **MCP secret** (`mcp-secret.json`) is long-lived but machine-generated, stored `0600` in the user-data dir. A third-party API key is **long-lived and user-supplied**: the user pastes it once and expects it to persist across launches. Forcing it through the per-launch env-var mechanism would mean re-entering it every start or stashing it in a shell profile — a worse leak surface, not a better one.

The constraints from prior decisions carry over hard. [ADR-0011](0011-bearer-secret-transport.md)'s discipline: a secret is **never logged, never in `argv`**. [ADR-0006](0006-persistence-layout.md) splits persistence into SQLite (application *data*) and `config.json` (hand-editable, non-secret *config*) — and `config.json` is exactly the file a user might paste into an issue or sync to a dotfiles repo, so a key in it is a leak waiting to happen. The renderer runs sandboxed and reaches the sidecar only over localhost+bearer ([ADR-0002](0002-ipc-local-http.md)); a key entered in a Settings field has to reach the sidecar without the renderer retaining or re-displaying it.

## Decision

We will store user-supplied third-party API keys in a **dedicated `secrets.json` file in the user-data dir** ([ADR-0020](0020-shared-data-dir-contract.md)), written with `0600` permissions — the same on-disk model as `mcp-secret.json`, **not** `config.json` and **not** SQLite. A `SecretsStore` in the sidecar owns read/write; adapters obtain their key from it at construction/call time.

> `secrets.json` is a flat, Pydantic-validated map of known secret keys to string values — e.g. `{"zerion_api_key": "...", "graph_api_key": "...", "eth_rpc_url": "...", "base_rpc_url": "..."}`. It lives outside the repo (in the user-data dir), is created `0600` on first write, and is never committed. An environment-variable override (`MARKET_ANALYSER_<KEY>`) takes precedence per key for dev/CI, so tests and headless runs inject keys without touching the file.

Three rules bound the handling:

1. **Never logged, never in argv** ([ADR-0011](0011-bearer-secret-transport.md) extended). Secret values are redacted from any log/error path; a key is never passed as a process argument.
2. **Write-only to the renderer.** The Settings API accepts a key value (over the existing localhost+bearer channel) and reports only **presence/absence** per key — it never returns a stored value back to the renderer. The UI shows "set / not set," never the secret. The renderer holds the value only transiently while the user is typing it.
3. **Server-side injection.** Adapters read keys from the `SecretsStore` inside the sidecar and inject them into outbound calls there — the key never crosses back to the renderer or into a response body, mirroring how the typed fetch client injects the bearer once.

## Consequences

### Positive
- One clear, auditable home for every authenticated source's credential, reusing the proven `mcp-secret.json` `0600` model rather than inventing a scheme.
- Keys survive restarts (the user pastes once) without the per-launch-re-entry cost the bearer mechanism would impose.
- The write-only Settings contract means a compromised or buggy renderer can *set* a key but never *exfiltrate* a stored one — the value lives only in the sidecar after entry.
- The env-var override keeps CI/dev keyless-by-default and secrets out of test fixtures.

### Negative
- **`0600` on Windows is weak** — the user's own processes can read files in their own profile regardless of ACL, the same limitation [ADR-0011](0011-bearer-secret-transport.md) Alt C and `mcp-secret.json` already accept. We accept it again for consistency; the threat model remains the single-user desktop ([ADR-0002](0002-ipc-local-http.md)), and these are third-party keys for read-only data APIs, not trade-permissioned credentials.
- **A persisted secret file is a backup/sync leak surface.** If the user syncs their user-data dir, `secrets.json` goes with it. Mitigation is documentation + the file living in the OS user-data dir (not the repo, not a dotfiles-typical path), but persistence inherently widens the surface versus the bearer's never-on-disk model. The tradeoff is deliberate: long-lived user keys have to persist somewhere.
- **A second on-disk secret file** (alongside `mcp-secret.json`) for a future reader to learn. Minor; both follow the identical `0600`/user-data-dir convention.

### Neutral
- The polished Settings UI for entering keys is a renderer concern owned by a later DeFi plan; Plan 0032 ships the `SecretsStore` + the set/status endpoint, and a key can be set by editing `secrets.json` directly or via that endpoint in the interim.
- Key *rotation* is overwrite-in-place (re-set via the endpoint or edit the file); no rotation ceremony is specified because these are user-owned upstream credentials, not app-generated.

## Alternatives considered

### Alternative A — Keys in `config.json`
Put them in the existing hand-editable config file. **Rejected:** `config.json` is explicitly the non-secret, user-editable, shareable config surface ([ADR-0006](0006-persistence-layout.md)) — exactly what gets pasted into a bug report or synced to a public dotfiles repo. A secret there is a leak waiting to happen. Secrets get their own `0600` file.

### Alternative B — Keys in SQLite
Store them in the application database. **Rejected:** the DB is copied/backed-up as data and inspected with ordinary tooling; mixing credentials into it widens the leak surface and breaks the clean data-vs-secret split. A discrete `0600` file is the established pattern (`mcp-secret.json`) and is trivially excludable from data dumps.

### Alternative C — Env-vars only, no persistent file (like the bearer)
Mirror [ADR-0011](0011-bearer-secret-transport.md) exactly. **Rejected:** the bearer is machine-generated per launch; a user API key is long-lived and user-supplied. Env-only forces the user to re-export the key every launch or persist it in a shell profile / system env — a *worse* leak surface than a `0600` file, and hostile UX. We keep the env-var as a per-key *override* for dev/CI, but the persistent store is the file.

## Notes
- The Windows-ACL and backup-leak caveats are inherited, knowingly, from the `mcp-secret.json` precedent; this ADR does not solve them, it scopes them to read-only third-party data keys.
- First consumer is [ADR-0034](0034-defi-portfolio-aggregator.md)'s Zerion adapter; the RPC/Graph keys for the deep-adapter plan reuse the same store with no further decision.
