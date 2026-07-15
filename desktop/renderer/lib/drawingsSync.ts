/**
 * Renderer→sidecar drawing mirror sync (Plan 0104 phase 4, ADR-0099).
 *
 * The write half of the read-back loop. On every user-drawing mutation and on
 * chart load the renderer PUTs the symbol's FULL user set to the sidecar mirror
 * (a declarative replace) and POSTs one `ui.drawing_changed` nudge. Both are
 * best-effort: a failure is logged and dropped, never blocking the draw — the
 * renderer's `ma.userDrawings` stays the source of truth (ownership never moves).
 *
 * Kept out of the pure `userDrawings` store so that module carries no network
 * dependency; this sibling is the seam that reaches the typed fetch client (which
 * injects the bearer once) and the ui-event poster.
 */
import { api } from '../api/client'
import { postDrawingChanged } from '../api/uiEvents'
import type { DrawingChange } from '../types/ui-events'
import type { DrawingKind } from '../types/events'
import { loadUserDrawings } from './userDrawings'

/**
 * PUT the current full user drawing set for `symbol` to the sidecar mirror, with
 * one retry. Never throws — a persistent failure is logged at DEBUG and the local
 * drawing is untouched (the mirror is a shadow, not authority).
 */
export async function syncUserDrawings(symbol: string): Promise<void> {
  const drawings = loadUserDrawings(symbol)
  try {
    await api.putUserDrawings(symbol, drawings)
    return
  } catch {
    try {
      await api.putUserDrawings(symbol, drawings)
      return
    } catch (retry) {
      console.debug(
        '[drawingsSync] PUT /user_drawings failed after retry; mirror not updated',
        retry,
      )
    }
  }
}

/**
 * One user-drawing mutation: replace the sidecar mirror with the full current set
 * AND emit exactly one `ui.drawing_changed` nudge. Fire-and-forget — both halves
 * swallow their own errors, so a mutation is never blocked by the network.
 */
export function notifyDrawingMutation(
  symbol: string,
  change: DrawingChange,
  drawingId: string,
  kind: DrawingKind,
): void {
  void syncUserDrawings(symbol)
  void postDrawingChanged({ symbol, change, drawing_id: drawingId, kind })
}
