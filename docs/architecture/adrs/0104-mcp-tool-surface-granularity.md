# ADR-0104 — MCP tool-surface granularity: one tool per verb, modes as parameters

> **Status:** accepted
> **Date:** 2026-07-15
> **Related plan(s):** 0109-mcp-tool-consolidation

## Context

The sidecar's FastMCP server (ADR-0014) mounts **62 agent-callable tools** at `/mcp`, and the number only goes up. Each capability plan reflexively mints a new top-level tool, adds a `register_*` call in `mcp_app.py`, bumps the `EXPECTED_FULL_TOOLSET` set in `tests/api/test_mcp_tools.py`, and regenerates `docs/reference/`. Four approved-but-unshipped plans continue the pattern — 0102 (`sector_rotation`), 0103 (`reddit_sentiment`), 0108 (`social_sentiment`), 0107 (`defi_fundamentals`), plus 0042's risk tools and 0046's order tools — putting the surface on a path to ~70.

The user's concern is that a large tool count makes the agent "lose context." That intuition needs one correction and one confirmation. **Correction:** in Claude Code — our primary control surface (ADR-0015) — MCP tools are *deferred*, not eager-loaded. Their schemas enter the context window only when `ToolSearch` pulls them in on demand; the marginal context cost of tool N+1 is near zero until it is searched. So the raw "62 schemas fill the window" cost is largely already mitigated for the client we actually drive the app with. **Confirmation:** the real residual cost is *discovery and routing quality*. When six watchlist scanners, three forecast tools, and two-soon-to-be-four sentiment tools carry near-identical one-line descriptions, the agent is likelier to select the wrong one, or to miss that a capability exists at all. That is a coherence problem, and coherence — not byte count — is what a smaller, better-factored surface actually buys.

Inspecting the 62, the redundancy is concentrated in tools that are **the same verb differing only by a mode**: the watchlist scanners (`squeeze_scan`, `gainers_losers`, `momentum_scan`, `quality_rank`, `volume_breakout`, `smart_volume`) all take a symbol list and rank/filter on a condition through the *same* `analysis/scanners.py::_scan_symbols` harness; the forecast trio (`forecast`, `forecast_volatility`, `forecast_regime`) are three kinds of one operation; the sentiment tools (`sentiment_for_news`, `stocktwits_sentiment`) differ only by source; the single-symbol price reads (`fibonacci_levels`, `pivot_points`, `anchored_vwap`, `market_structure`) are one "read a price-structure overlay" verb with a kind. None of these is a distinct capability boundary — they are enum values wearing tool costumes.

No existing ADR governs tool *granularity*. ADR-0014 and ADR-0015 govern the surface as a control plane; ADR-0046 governs large-result delivery; ADR-0064 governs its generated documentation. Nothing tells a plan author when a new capability earns a new top-level tool versus a parameter on an existing one — so the default is always "new tool," and the count ratchets.

## Decision

We adopt a **granularity rule for the MCP tool surface, and consolidate the existing same-verb clusters to match it.**

**The rule:** one tool per *verb*. A capability that is a new **mode of an existing verb** — the same operation over the same inputs, differing by a discriminator — extends that tool through a `kind` / `rank_by` / `source`-style parameter and a discriminated result; it does **not** add a top-level tool. Only a **genuinely new verb** (a new operation, new inputs, or a new capability boundary) earns a new top-level tool. Distinct verbs stay distinct even when it would shrink the count — the per-tool description is the agent's primary routing signal, and collapsing unrelated verbs to save a number trades a real boundary for a cosmetic win.

**The consolidations** (executed by Plan 0109), each folding a same-verb cluster behind one discriminated tool:

| New tool | Discriminator | Absorbs | Δ |
|---|---|---|---|
| `scan_watchlist` | `rank_by` | `squeeze_scan`, `gainers_losers`, `momentum_scan`, `quality_rank`, `volume_breakout`, `smart_volume` | −5 |
| `forecast` | `kind` | `forecast` (direction), `forecast_volatility`, `forecast_regime` | −2 |
| `sentiment` | `source` | `sentiment_for_news`, `stocktwits_sentiment` | −1 |
| `price_structure` | `kind` | `fibonacci_levels`, `pivot_points`, `anchored_vwap`, `market_structure` | −3 |
| `volume_read` | `kind` | `volume_confirmation`, `counter_trend_volume` | −1 |

Net **62 → 50**, with the sentiment merge also converting 0103/0108's two *new* tools into two *modes* of `sentiment` (a further −2 against the projected surface).

**What stays separate** (distinct verbs, explicitly not merged): chart control (`show_chart`/`update_chart`/`annotate_chart`/`highlight_pattern`/`get_chart_drawings`), data access (`get_ohlcv`/`backfill_ohlcv`/`quote_for`/`search_symbols`/`market_snapshot`), the pattern/level compute-and-draw tools (`detect_levels`/`detect_chart_patterns`/`scan_patterns` — they *emit chart events*, unlike the pure reads), the backtest verbs, the prediction-market verbs, the DeFi verbs (`scan_wallet` vs `compute_wallet_pnl` vs `portfolio_summary` — different operations), the advisory tools (`recommend`, `technical_read`, `get_track_record`), the watch CRUD, and above all **execution** (`prepare_order`/`confirm_order` are deliberately split for the human-confirm gate, ADR-0025).

**The governance artifact:** `EXPECTED_FULL_TOOLSET` is designated the tool budget ledger. Adding a name to it is the reviewable moment where a plan author must state, in the plan's tool phase, *which verb is new*. Mode 4 review checks that claim against this ADR's rule; a name that is really a mode of an existing verb is a blocker, not a nit.

## Consequences

### Positive
- **Better routing.** One `scan_watchlist` with a documented `rank_by` enum reads more clearly than six overlapping scanners; the agent picks a mode of a known verb instead of disambiguating six near-duplicate tools.
- **Capped growth.** The rule turns "new capability → new tool" from a reflex into a justified decision, and the `EXPECTED_FULL_TOOLSET` ledger enforces it at review time. 0103/0108 stop adding tools.
- **Fewer top-level entries to search.** Even under deferred loading, a smaller, non-redundant surface makes `ToolSearch` hits sharper and reduces mis-selection.
- **One place per verb to evolve.** A new scanner criterion or forecast kind is a new enum value, not a new module + registration + test-set edit + doc regen.

### Negative
- **Fatter per-tool schemas.** A discriminated-union tool carries every mode's parameters and result variants in one schema; the individual tool is bigger even though there are fewer of them. Mitigation: keep each mode's params in a nested per-mode object and lean on clear enum-value descriptions so the discriminator itself routes.
- **Breaking rename of agent-facing tools.** The retired names disappear from the wire. Consumers that must be updated in lockstep: `EXPECTED_FULL_TOOLSET`, the apiref generator output (`docs/reference/`), and every **skill reference doc** that names a retired tool (e.g. `market-analyst` naming `squeeze_scan`). This is real churn and Plan 0109 owns it as an explicit phase. There is no external/public consumer to break — only the agent, the tests, and the generated docs.
- **Internal-consumer rewiring.** `recommend` consumes the quality-rank computation internally; the advisor consumes the forecast legs. These must call the underlying `analysis/`/`forecast` functions directly (they already can), not the retired tools — verified per phase, or the merge is deferred for that cluster.
- **Lost per-tool one-liners.** Six scanner descriptions collapse to one; a capability that used to announce itself in the top-level list now lives inside an enum value's description. Net-positive for routing, but discoverability of a specific mode depends on the enum docs being good.
- **Discriminated-result envelope for the disjoint-shape cluster (added 2026-07-15, from Plan 0109 phase 2; refined at acceptance to match what shipped).** FastMCP wraps any tool whose *return annotation* is a `Union`/generic type in a `{"result": …}` object (`mcp/server/fastmcp/utilities/func_metadata.py` → `wrap_output=True`); only a single `BaseModel` return stays unwrapped. So a cluster whose modes return **different, *bare* top-level models with no shared wrapper** cannot keep each mode's exact top-level shape. As implemented, exactly one cluster is in this position: **`forecast`** (`MultiHorizonForecastResult` / `VolatilityForecast` / `RegimeForecast` — the three retired tools returned these bare). Plan 0109 gives it a purpose-built envelope `ForecastResponse{kind, result: <mode payload>}` — itself a single object, so **not** generically wrapped — nesting each mode's existing model one level under `result`. The `result` payload is byte-identical to the retired tool's output and the SSE events are unchanged (they publish the raw domain models, not the tool return); only the agent-facing tool-return envelope gains a discriminator + one level of nesting. **The other four clusters are all immune** because each retired member already returned a single object: `scan_watchlist` (six tools shared `{matches, skipped, scanned_at}` — mode-union in the `matches` **field**, shipped phase 1); `price_structure` and `volume_read`, whose retired reads already shared `{result, partial_reason, scanned_at}`, so they fold the *same* shared-wrapper way — a flattened `{kind, result, partial_reason, scanned_at}` with the mode-union in the `result` field and `kind` added alongside (no envelope, no double-nest — `result`/`partial_reason`/`scanned_at` stay byte-identical); and `sentiment`, whose two tools already return `dict` (already `result`-wrapped today), a zero-shape-change. Net: one tool gained the envelope, four folded shape-preserving.

### Neutral
- The consolidation is a surface refactor, not a computation change: the underlying `analysis/`, `forecast/`, and sentiment computations are untouched; only their MCP entrypoints are re-shaped. Determinism, anti-lookahead, and the conditions-vs-advice boundary (ADR-0029) are unaffected — `quality_rank` stays conditions-side (ADR-0096) as a `rank_by` mode. For the three disjoint-shape clusters the entrypoint reshaping additionally nests each mode's payload under a `{discriminator, result}` envelope (see the Negative note); the payload content and all computation are unchanged — a tool-return *shape* change, not a *behavior* change.

## Alternatives considered

### Alternative A — Do nothing; rely on deferred loading
Claude Code already defers the tools, so the context cost is mostly paid down. Rejected: this fixes neither the routing/coherence problem (near-duplicate descriptions still confuse selection) nor the growth (the surface keeps ratcheting toward 70+), and it only helps clients that defer — the surface should be coherent independent of one client's loading strategy.

### Alternative B — Per-skill tool subsetting (multiple MCP mounts or a capability filter)
Mount `/mcp/analysis`, `/mcp/defi`, `/mcp/execution` and have each skill connect only to its slice, so an agent never carries tools outside its domain. Rejected: it is materially heavier infrastructure (multiple session managers, per-skill transport wiring, a routing story for cross-domain calls) to solve a context-cost problem that deferred loading already solves for our primary client. The coherence win is better bought by fixing the surface itself.

### Alternative C — Aggressive fold into `analyze_symbol` + full re-taxonomy
Absorb the price reads, volume reads, and even the scanners into `analyze_symbol` and re-taxonomize the whole surface. Rejected: it merges distinct verbs (a single-symbol snapshot is not a watchlist scan), fattens one tool into a god-tool, and changes many contracts at once for marginal additional reduction over the same-verb merges. The chosen scope stops at same-verb clusters, which is where the merge is both safe and routing-positive.

## Notes

- Live count verified 2026-07-15 from `docs/reference/mcp-tools.md` (62, including the in-flight `quality_rank` from Plan 0101 phase 1) and the `EXPECTED_FULL_TOOLSET` set.
- **Discriminated-result shape (decided 2026-07-15, mid-implementation; reconciled at acceptance to the shipped surface).** Phase 1 (`scan_watchlist`) shipped as `{rank_by, matches, skipped, scanned_at}` — the shared-wrapper case, no envelope. Phase 2 surfaced the FastMCP union-wrap constraint above; the mid-implementation resolution proposed the `{discriminator, result}` envelope for all three "disjoint" clusters (full consolidation retained, 62 → 50, **not** dropping any merge). As shipped, only `forecast` actually needed it: `price_structure` and `volume_read` turned out to be shared-wrapper clusters like phase 1 (their retired reads already shared `{result, partial_reason, scanned_at}`), so they folded flat to `{kind, result, partial_reason, scanned_at}` with no envelope and no double-nest — a strictly better outcome that keeps `result`/`partial_reason`/`scanned_at` byte-identical. Full consolidation (62 → 50) retained regardless. Concrete per-cluster contract lives in Plan 0109 (Data shapes + close reconciliation).
- Queued-plan disposition (applied as amendments at this ADR's authoring):
  - **0103** (`reddit_sentiment`) and **0108** (`social_sentiment`) → **reroute**: each becomes a `source` mode of the unified `sentiment` tool (Plan 0109 creates it; these extend it). No new top-level tool.
  - **0102** (`sector_rotation`) → **keep**: a distinct verb (config-defined sector taxonomy + momentum ranking over baskets), not a mode of `scan_watchlist` (which ranks a flat symbol list). New tool justified.
  - **0107** (`defi_fundamentals`) → **keep**: a distinct verb (token TVL/holders/fundamentals), not a mode of an existing DeFi tool. New tool justified.
  - **0042** (DeFi risk) → **conform**: its phase-3 "risk tools" (plural — scenario sensitivity + conditional risk) collapse to **one** `defi_risk` tool with a `kind` discriminator, not two tools.
  - **0046** (`prepare_order`/`confirm_order`) → **keep both, deliberately**: the split is the ADR-0025 human-confirm gate, an explicit capability boundary the rule protects.
