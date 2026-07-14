/**
 * Thin wrapper over the typed fetch client for `POST /ui_events` (Plan 0014,
 * ADR-0021). The renderer fires these on every chart gesture — forwarding is
 * unconditional per ADR-0101 — and the sidecar buffers them for the agent to
 * read via `get_pending_ui_events`.
 *
 * The renderer sends `{type, version, payload}` only — the sidecar stamps
 * `event_id` and `ts`. Any non-2xx (or a transport failure) is logged at DEBUG
 * and the gesture discarded — UI gestures are best-effort, never blocking.
 */
import { sidecarFetch } from './client'
import type { BarClickedPayloadV1, RangeSelectedPayloadV1 } from '../types/ui-events'

async function postUiEvent(type: string, version: number, payload: unknown): Promise<void> {
  let res: Response
  try {
    res = await sidecarFetch('/ui_events', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type, version, payload }),
    })
  } catch (err) {
    console.debug('[uiEvents] POST /ui_events failed; gesture discarded', err)
    return
  }
  if (!res.ok) {
    console.debug(`[uiEvents] /ui_events ${res.status}; gesture discarded`)
  }
}

export function postRangeSelected(payload: RangeSelectedPayloadV1): Promise<void> {
  return postUiEvent('ui.range_selected', 1, payload)
}

export function postBarClicked(payload: BarClickedPayloadV1): Promise<void> {
  return postUiEvent('ui.bar_clicked', 1, payload)
}
