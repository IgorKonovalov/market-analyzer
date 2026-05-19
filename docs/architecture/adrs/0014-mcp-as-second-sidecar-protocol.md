# ADR-0014 — MCP as a second sidecar protocol alongside renderer HTTP

> **Status:** accepted
> **Date:** 2026-05-19
> **Related plan(s):** [0006-annotations-via-mcp](../plans/0006-annotations-via-mcp.md)
> **Related ADRs:** [ADR-0002](0002-ipc-local-http.md) (renderer ↔ sidecar transport), [ADR-0006](0006-persistence-layout.md) (SQLite for app data), [ADR-0011](0011-bearer-secret-transport.md) (renderer per-launch bearer)

## Context

The market-analyser sidecar today serves exactly one client: the Electron renderer in the same process tree. The transport is plain HTTP/JSON on `127.0.0.1` with a per-launch bearer secret rotated on every spawn ([ADR-0002](0002-ipc-local-http.md), [ADR-0011](0011-bearer-secret-transport.md)). The model is "the sidecar exists for the renderer; nothing else can reach it."

We now want a second class of caller: an external **MCP client** — concretely Claude Desktop, but generally any conforming Model Context Protocol client — that can query the sidecar's data and write back analyst-produced artifacts (initially annotations; later, per the C → B → A ordering in the Plan 0006 interview, strategy result rows and possibly strategy code). The motivating workflow is: a user runs an MCP client alongside the desktop app, the agent queries OHLCV and writes annotations, the app polls and renders those annotations as chart markers in the user's view.

Three constraints shape the decision:

1. **The MCP server must coexist with the renderer's HTTP transport in the same sidecar process.** The Electron main process is the supervisor; it spawns one sidecar per app launch ([ADR-0002](0002-ipc-local-http.md)). Spawning a second process would duplicate the supervision logic, the cache file, and the port-management code. The MCP client and the renderer both want access to the same SQLite cache, so a single process owning the file is also simpler for concurrency.
2. **Authentication models for the two clients differ on lifetime.** The renderer's bearer rotates per Electron launch — that is the entire point of [ADR-0011](0011-bearer-secret-transport.md), since the renderer is spawned by the same supervisor in the same instant. An MCP client (Claude Desktop) lives independently of the app's launch cycle; its config is pasted once and persisted across Electron restarts. A per-launch rotating secret is incompatible with that lifecycle.
3. **The current MCP specification (revision 2025-03-26) offers two networked transports: HTTP+SSE (two endpoints, deprecated) and Streamable HTTP (single endpoint, current).** Stdio is also defined but is only viable when the MCP client spawns the server as a subprocess and owns its stdio pipes — the renderer/Electron lifecycle precludes that here, since the sidecar is spawned by Electron, not by Claude Desktop.

The decision is non-obvious because we're adding a long-lived auth surface to a process whose other auth surface is explicitly short-lived, and because the transport choice has migration implications (the older HTTP+SSE shape is on a deprecation path).

## Decision

We will mount the MCP server as a second route prefix on the existing FastAPI sidecar process, served over **Streamable HTTP** (per MCP spec rev 2025-03-26) on the **same `127.0.0.1` port** the renderer already binds. Two independent authentication paths share that port:

- **Renderer routes** (`/healthz`, `/ohlcv`, `/annotations`, …) — bearer auth via the per-launch `MARKET_ANALYSER_SECRET` env var ([ADR-0011](0011-bearer-secret-transport.md)). Unchanged from today.
- **MCP route** (`/mcp` — exact path follows the Streamable HTTP spec) — bearer auth via a **separate, long-lived MCP secret** persisted in the user data directory as `mcp-secret.json` (mode `0600` on POSIX) and surfaced in the app's Settings page so the user can copy it into the Claude Desktop config. The secret is rotatable on demand from Settings; rotation invalidates existing MCP sessions.

The sidecar's lifecycle is unchanged: Electron spawns it, supervises it, and kills it on app quit. MCP clients see "server unreachable" when the app is closed. Standalone-sidecar mode is explicitly **out of scope** of this ADR and is a future decision if the workflow demands it.

We rejected stdio (incompatible with Electron-spawned sidecar lifecycle), a separate sidecar process for MCP (duplicate supervision + cache contention), a separate port for MCP (more surface for the user to configure, no real isolation benefit on the same loopback interface), and HTTP+SSE transport (deprecated in favour of Streamable HTTP in the current spec — adopting it now would mean planning a migration in 6–12 months).

## Consequences

### Positive

- **Single sidecar process, single SQLite file, single supervision path.** The Electron main process supervises one child; the renderer and MCP clients are co-tenants of the same FastAPI app and the same data layer ([ADR-0007](0007-market-data-provider.md)'s `MarketDataProvider` is the chokepoint for both).
- **Auth boundaries stay clean.** The renderer's per-launch secret keeps its security properties (ADR-0011) untouched — MCP traffic can never authenticate as the renderer and vice versa, because each middleware accepts only its own secret. A leak of the MCP secret cannot escalate to renderer privileges or vice versa.
- **No transport churn in the foreseeable future.** Streamable HTTP is the current MCP spec recommendation; adopting it directly skips the HTTP+SSE migration step that early MCP servers will eventually face.
- **The Settings UI surface that this work adds is reusable** for other long-lived secrets the project might accrue (e.g. third-party data-source API keys, if those ever land).

### Negative

- **A new long-lived secret on disk.** Today the sidecar persists no secrets — every bearer is in process memory, rotated per launch ([ADR-0011](0011-bearer-secret-transport.md)). `mcp-secret.json` is the first persisted secret in the app's data directory; it needs file-mode discipline (`0600`), a documented rotation procedure, and a Settings UI that doesn't display the full secret in plaintext after creation (offer a "reveal" button + "copy to clipboard"). A misconfigured `mcp-secret.json` (world-readable, leaked into logs, accidentally committed via `pnpm package`) would give a local attacker write access to the annotations DB and read access to all cached market data.
- **MCP-client unavailability is coupled to the app's lifecycle.** If the user closes the desktop app, every MCP session breaks. Long-running agent workflows (overnight analysis, scheduled scans) are impossible until and unless a future ADR introduces standalone mode. We accept this constraint as part of the chosen lifecycle option in the Plan 0006 interview.
- **Two auth middlewares to keep correct.** Bug surface for "this route accidentally accepts the wrong bearer" or "this route accidentally accepts no bearer at all" doubles. Mitigation: a single bearer-check middleware that dispatches on the route prefix and uses constant-time comparison for both secrets ([ADR-0011](0011-bearer-secret-transport.md) carries the constant-time requirement); tests assert each route requires exactly the bearer kind it expects.
- **A wider FastAPI app means a wider attack surface on the localhost listener.** Today the listener is renderer-only; tomorrow it is renderer + MCP. Even on `127.0.0.1` this matters: any process on the user's machine that can read `mcp-secret.json` can authenticate. We accept this as the cost of MCP coexistence; defence-in-depth is the file-mode discipline above plus the Settings UI explicitly warning the user before showing the secret.

### Neutral

- The renderer's bearer transport (env var, ADR-0011) stays exactly as it is. The MCP secret transport differs (file on disk) precisely because the two clients have different lifecycle requirements — this is a deliberate asymmetry, not an inconsistency to resolve.
- The MCP server's tool surface is decided by Plan 0006 (and successor plans), not here. This ADR captures only the protocol-and-auth shell.

## Alternatives considered

### Alternative A — Stdio transport, Claude Desktop spawns the sidecar

Under MCP's stdio transport, the client launches the server as a subprocess and owns its `stdin`/`stdout`/`stderr`. Rejected because the sidecar is already spawned by the Electron main process — it cannot simultaneously be the child of two different supervisors. Stdio would force one of: (a) two separate sidecar processes (the Electron-spawned one for the renderer and a Claude-Desktop-spawned one for the agent), which fragments the cache and the data layer chokepoint and effectively prevents the agent's writes from being visible to the renderer in real time; or (b) running only the Claude-Desktop-spawned sidecar and abandoning the renderer's existing transport, which is a non-starter — the renderer doesn't speak MCP and Electron-without-sidecar is not the product. The Electron-supervised lifecycle is the binding constraint, and stdio is structurally wrong for it.

### Alternative B — Separate process for MCP (sibling sidecar)

Run two sidecar processes: one for the renderer (today's), one for MCP. They share the SQLite cache file. Rejected because shared SQLite write access from two processes needs explicit care (write-ahead logging, the "database is locked" failure mode), the Electron supervisor needs to spawn and health-check two children, and the chokepoint design of [ADR-0007](0007-market-data-provider.md) (one `MarketDataProvider` instance is the only data-layer authority) becomes hard to defend when there are literally two instances. The "one process, two transports" alternative we picked has none of these drawbacks at the cost of a slightly fatter FastAPI app, which is a much smaller bill.

### Alternative C — Same process, separate port for MCP

Bind two ports: the renderer's existing port and a new MCP-only port. Rejected because there is no actual isolation benefit on the same loopback interface — both ports are equally reachable by any local process — and the user-facing cost is real: Settings now has to display two URLs, and the Electron supervisor has to manage two free-port lookups and two health checks. We pay complexity for no security gain.

### Alternative D — HTTP+SSE transport (two-endpoint MCP)

The older MCP transport spec used two endpoints: a POST endpoint for client→server messages and an SSE endpoint for server→client streams. Rejected because that shape is deprecated in MCP spec rev 2025-03-26 in favour of Streamable HTTP, and the Python MCP SDK's roadmap follows the spec. Adopting it now would commit us to a transport migration plan within ~12 months for no upside compared to Streamable HTTP today.

### Alternative E — Per-launch MCP secret like the renderer's

Use the same `MARKET_ANALYSER_SECRET` env-var rotation strategy for MCP clients. Rejected because Claude Desktop config persists across the app's launches; if the secret rotates every time the user opens the desktop app, the user has to re-paste it into Claude Desktop's config every time, which is unworkable. The asymmetry of secret lifetimes is forced by the asymmetry of client lifetimes; pretending the two clients are symmetric would just push the problem onto the user as repetitive config friction.

## Notes

- Reference: MCP specification revision **2025-03-26**, section "Streamable HTTP transport". The current Python SDK (`mcp` ≥ 1.6) implements this transport directly as an ASGI route, which is what makes mounting on the existing FastAPI app feasible.
- The exact `mcp-secret.json` shape (single-secret file vs. JSON envelope with metadata like `created_at`, `last_rotated_at`) is decided in Plan 0006 phase 1, not here — the ADR captures only the persisted-on-disk-with-mode-0600 invariant.
- Future ADR territory (not blocked by this one): standalone sidecar mode for off-app agent workflows; agent-written strategy *code* (the "A" tier of Plan 0006's C → B → A ordering) and its sandboxing requirements; per-tool authorization scopes within MCP (today the secret is all-or-nothing).
