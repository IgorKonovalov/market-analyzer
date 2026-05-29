/**
 * Hand-written TS mirror of the renderer→agent UI-event payloads from
 * `src/market_analyser/api/ui_events/__init__.py` (Plan 0014, ADR-0021).
 *
 * Why hand-written (like `types/events.ts`, not `types/sidecar/`): the
 * per-type payload models are validated by pydantic behind `POST /ui_events`'s
 * `payload: dict` body, so they don't surface as named `components.schemas` in
 * the OpenAPI dump — the gen-types pipeline can't emit them. Keep them here so
 * the generated `types/sidecar/` set stays a pure gen-types output.
 *
 * The renderer supplies `{type, version, payload}` only; the sidecar stamps
 * `event_id` + `ts` and derives the authoritative `version` from its registry.
 */

/** `ui.range_selected v1` — the user drag-selected a [start, end] window. */
export interface RangeSelectedPayloadV1 {
  symbol: string
  timeframe: string
  /** ISO 8601 UTC. */
  range_start: string
  /** ISO 8601 UTC. */
  range_end: string
}

/** `ui.bar_clicked v1` — the user clicked a single candle. */
export interface BarClickedPayloadV1 {
  symbol: string
  timeframe: string
  /** ISO 8601 UTC. */
  event_ts: string
  open: number
  high: number
  low: number
  close: number
}

/** The agent-mode toggle wire shape (`GET`/`PUT /agent_mode`). */
export interface AgentModeState {
  enabled: boolean
}
