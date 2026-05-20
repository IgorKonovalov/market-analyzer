# ADR-0015 — Claude Code (MCP) as the primary control surface; Electron as the live viewer

> **Status:** accepted
> **Date:** 2026-05-20
> **Related plan(s):** [0007-live-agent-driven-viewer](../plans/0007-live-agent-driven-viewer.md)
> **Related ADRs:** [ADR-0002](0002-ipc-local-http.md) (renderer ↔ sidecar transport), [ADR-0005](0005-desktop-shell-electron.md) (Electron pick), [ADR-0011](0011-bearer-secret-transport.md) (per-launch renderer bearer), [ADR-0014](0014-mcp-as-second-sidecar-protocol.md) (MCP as a second sidecar protocol — **refined here**)

## Context

[ADR-0014](0014-mcp-as-second-sidecar-protocol.md) (accepted 2026-05-19, implementation closed 2026-05-20 via Plan 0006) framed MCP as *"a second sidecar protocol alongside renderer HTTP"*. The renderer was the primary client; MCP was the additive surface for an external agent (Claude Desktop) that wanted to query data and write annotations the user would see on the chart. The interaction model the project was designed around: the user clicks in the renderer; the agent helps from the side.

The user's stated direction (this session, 2026-05-20) inverts that model. The product they want to use is:

- The user types prompts to an agent (concretely **Claude Code**, the CLI, but the architecture is transport-portable to any conforming MCP client).
- The agent drives analysis, backtests, screens, and chart visualisations by calling MCP tools.
- The Electron app exists to **show** what the agent renders — candlestick charts with overlays, equity curves, strategy state — and to provide the small set of human-only privileged operations (rotate the MCP secret, reveal the bearer for re-paste).
- The user does not type symbols into a form, does not click "run backtest" buttons. The renderer's "control surface" role shrinks to near-zero; its "viewer" role expands.

This is the headline architectural shift. It has two structural consequences that warrant their own ADRs (ADR-0016 and ADR-0017); this ADR captures the framing decision that demands them.

Three forces shape the framing:

1. **An agent-primary product needs the agent to be reachable independent of the viewer.** ADR-0014's "MCP availability is coupled to app lifecycle" caveat was acceptable when MCP was secondary; it is unacceptable when MCP is primary. Closing the viewer must not break the agent. See [ADR-0016](0016-standalone-sidecar-mode.md).
2. **An agent-driven viewer must react to changes the agent makes, in something close to real time, even when the change is ephemeral.** Plan 0006's 1 Hz polling for annotations works for "the agent dropped a marker on yesterday's candle"; it does not work for "the agent just told the viewer to render this chart, with these overlays, focused on this date range — and now change it." Conversational tweaks need push, not pull. See [ADR-0017](0017-live-ui-updates-via-sse.md).
3. **The renderer is still the right surface for chart rendering.** ADR-0005's pick (Electron + React + TypeScript + `lightweight-charts`) is unchanged: the fidelity the user wants for candlestick analysis exists in a browser-class chart library, not in a terminal renderer. The role inversion is "who drives", not "where do charts live".

The decision is non-obvious because it reframes a recently-accepted ADR (0014 is one day old) and because it cuts the perceived value of a non-trivial amount of recently-shipped UI work. We are deciding to *keep* that work and *change what it does for the user*, not to throw it away.

## Decision

We will treat **Claude Code (and any conforming MCP client) as the primary control surface for the market-analyser desktop app**, and **Electron as the live viewer for charts and agent-rendered artifacts**. The architectural roles are:

- **Claude Code (or another MCP client)** — the user's primary input device. The user types prompts; the agent calls MCP tools on the sidecar. Symbols, timeframes, indicators, backtest parameters, render commands all originate here.
- **Python sidecar** — the single in-process owner of the data layer, persistence, strategies, and (eventually) the backtest engine. Serves two transports on one port: MCP at `/mcp` (Streamable HTTP, agent-facing, long-lived `mcp-secret.json` bearer) and the renderer HTTP routes (viewer-facing, per-sidecar-launch bearer; see ADR-0016). Publishes a live event stream that the viewer consumes; see ADR-0017.
- **Electron viewer** — the live visualisation surface. Renders charts, overlays, equity curves, strategy state, agent annotations. Subscribes to the sidecar's event stream so the agent's render commands and completed-run notifications appear in real time. Hosts the small set of human-only privileged operations (the Settings page from Plan 0006: reveal/copy/rotate the MCP secret).

This refines — does not supersede — [ADR-0014](0014-mcp-as-second-sidecar-protocol.md). MCP is no longer "a second sidecar protocol"; it is the **primary** sidecar protocol, with renderer HTTP routes serving the viewer's read traffic and hosting the event stream. ADR-0014's two-bearer model, single-process layout, and Streamable-HTTP-via-`/mcp` decision are unchanged; only the framing of which client matters most is updated.

## Consequences

### Positive

- **Headless workflows become natural.** Overnight scans, scheduled backtests, multi-step analyses can run with no Electron window open. The viewer becomes a thing the user opens when they want to *see* something, not a thing the sidecar depends on to *exist*.
- **The viewer's UX surface shrinks dramatically.** Most of the "form-driven control panel" work we would have had to build (symbol picker, strategy picker, parameter form, run button, etc.) doesn't need to exist. The agent is the input.
- **Sibling-skill outputs flow to both clients for free.** A backtest run produces an artifact under `runs/`; the agent reads it via an MCP tool, the viewer reads it via the renderer HTTP route and re-renders on the corresponding event. One source of truth, two consumers.
- **The product matches how the user actually wants to work.** The explicit "use only with Claude CLI" decision lands without any throwaway code — every component in the existing architecture has a role in the new framing.
- **Sibling skills (`strategy-author`, `backtester`, `market-analyst`, `defi-analyst`) compose with Claude Code naturally.** They already produce files-and-Python, which is what the agent consumes. The MCP tool surface becomes the seam.

### Negative

- **The viewer must work in two modes: standalone-read and reactive-render.** Standalone-read = user opens the viewer cold and sees the current state from SQLite + `runs/`. Reactive-render = the agent is driving and events flow in live. The reactive mode is the dominant one; this changes how `ui-builder` designs interactions (less form-driven, more "subscribe to state and reflect it"). Some existing renderer code patterns (`useState` for symbol selection, modal forms) become legacy on contact.
- **The renderer's per-launch bearer property from [ADR-0011](0011-bearer-secret-transport.md) cannot survive unchanged.** The sidecar now outlives any single Electron launch, so the renderer must be able to attach to an already-running sidecar — and that requires the bearer to be discoverable somehow. [ADR-0016](0016-standalone-sidecar-mode.md) addresses this by rotating the bearer per **sidecar** launch (not per Electron launch) and persisting it in a `0600` lockfile. The "never persisted to disk" property of ADR-0011 is downgraded, deliberately, with the same threat-model accept ADR-0014 made for `mcp-secret.json`.
- **The Settings page from Plan 0006 is now load-bearing for onboarding, not optional.** Pasting the MCP secret into Claude Code's config (`.mcp.json` or `~/.claude.json`) is the *only* way to make the agent reach the sidecar. The renderer's role in revealing/rotating that secret graduates from "convenient" to "required". Onboarding docs must reflect this.
- **We lose UI-driven discoverability.** New users have to learn to drive via Claude — there is no "click around and find features" path. Documentation burden shifts from "click these buttons" to "ask Claude this kind of question". The skill ecosystem (`market-analyst`, `backtester`, `strategy-author`, `defi-analyst`) is partially responsible for filling this gap by being descriptive enough that Claude routes correctly without the user knowing the skill names.
- **The viewer is now coupled to an event-schema contract** (ADR-0017). Adding a new render kind requires touching both the agent-side MCP tool and the renderer-side handler, plus a version bump on the event envelope if the shape changes. This is a real tax compared to "the agent just writes a row and the renderer polls."

### Neutral

- **ADR-0005's Electron pick is unchanged.** The role-inversion does not weaken the case for Electron — chart rendering still wants browser-class libraries; the Settings page still wants accessible HTML; the viewer still wants window chrome and OS integration. A future "no UI at all" pivot is not blocked by this ADR, but it is not what we are deciding here.
- **ADR-0014's dual-bearer, single-process, Streamable-HTTP-at-`/mcp` topology is unchanged.** Refining the *framing* leaves the *mechanism* alone.
- **The skills ecosystem (CLAUDE.md's skill split — `market-analyst` vs `backtester` vs `defi-analyst` etc.) stays exactly as it is.** Skills still own their domains; they still produce artifacts under `runs/`. The agent invoking MCP tools is the seam between Claude-the-conversation and skills-doing-work. (This is a place where future drift will likely happen — eventually the skill descriptions will want to reference "via the MCP tool surface" instead of "via direct Python invocation". Deferred to a future SKILL.md sweep.)

## Alternatives considered

### Alternative A — Remove Electron entirely; render in the terminal

The maximally consistent "Claude CLI only" reading. Replace `lightweight-charts` candlesticks with `plotext` or similar terminal-rendering libraries; replace the Settings page with a CLI subcommand for MCP-secret operations.

Rejected because the user explicitly wants visualization fidelity that terminal charts cannot deliver — candlesticks with overlays, equity curves with zoomable axes, marker hover-text. The terminal-renderer alternative reads "consistent" but trades the actual product value for ideological purity. The Electron viewer is the one piece of UI the user wants to *keep*; it just stops being the *control* surface.

### Alternative B — Status quo (ADR-0014 framing unchanged)

Keep Electron as the primary surface; treat Claude as the secondary "automation companion" via the existing three MCP tools from Plan 0006. Build out the renderer's form-driven control panel for symbol/timeframe/strategy/backtest selection.

Rejected because it contradicts the user's stated direction. The form-driven control panel is exactly the work the user does not want us to spend cycles on; the agent-driven path is the one they want to prioritise.

### Alternative C — Build a typer-style CLI binary as the primary surface; Electron is optional viewer

Add a `market-analyser` CLI binary (Python typer or click) that exposes the same operations as the agent does. The agent and the CLI both call the same internal layer; Electron is a viewer for either.

Rejected because the CLI binary duplicates Claude's role without adding value. Claude already gives us a typed-tool conversational interface (via MCP). Adding a parallel CLI surface would mean maintaining two control surfaces with overlapping vocabularies, two onboarding docs, and two test surfaces — for a product whose stated entry point is Claude. If a user genuinely wants a shell-only path (e.g. for shell scripts), they can call MCP tools via `curl` or via a thin script — but that's a workflow, not an architectural surface we maintain.

### Alternative D — Keep ADR-0014's framing; let standalone mode + SSE accrete without renaming

Don't write this ADR. Let the deferred items from ADR-0014 (standalone mode) and Plan 0006 (SSE push) accumulate as follow-up ADRs without explicitly recording the role-inversion at the framing level.

Rejected because the role inversion is the load-bearing decision; the technical follow-ups are downstream consequences. Future maintainers reading just ADR-0016 and ADR-0017 would not understand *why* those decisions were forced. The framing ADR is the load-bearing one even though it does not itself prescribe a mechanism.

## Notes

- The "Claude Desktop" framing in [ADR-0014](0014-mcp-as-second-sidecar-protocol.md) was correct at the time but is no longer the primary use case. ADR-0014's text is unchanged (ADRs are append-only); the cross-reference here is the marker.
- This ADR does not change the [skill ecosystem](../../../CLAUDE.md) — `architect`, `dev`, `ui-builder`, `strategy-author`, `backtester`, `market-analyst`, `defi-analyst`, `skill-creator` keep their domains. Future drift may move some skill descriptions to call out "via the MCP tool surface" but that is a separate concern from this ADR.
- The user's flow that motivates this ADR: open Claude Code, ask "visualize an AAPL setup with EMA20 and a 30-day window", see the chart appear in Electron; continue the dialog "now add EMA50 and zoom to last 10 days", see live re-draw. This is the test we are designing for.
