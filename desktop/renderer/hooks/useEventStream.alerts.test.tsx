/**
 * Plan 0060 phase 4 done-when (c): the `alert.triggered v1` payload is
 * Zod-validated at the SSE boundary — a malformed payload is dropped with a
 * logged warning and never reaches the handler; a valid payload dispatches.
 * Uses the exported `dispatchEnvelope` directly (the same seam the older
 * per-type dispatch tests use), so no live EventSource is needed.
 */
import { dispatchEnvelope } from './useEventStream'

const VALID_PAYLOAD = {
  watch_id: 7,
  symbol: 'BTC-USD',
  timeframe: '1d',
  kind: 'indicator_threshold',
  fired_at: '2026-07-02T00:00:00Z',
  condition: 'rsi 28.44 < 30',
  values: { rsi: 28.44, level: 30 },
}

function envelope(payload: unknown): {
  type: string
  version: number
  ts: string
  payload: unknown
} {
  return { type: 'alert.triggered', version: 1, ts: '2026-07-02T00:00:01Z', payload }
}

afterEach(() => {
  jest.restoreAllMocks()
})

it('dispatches a valid alert payload to the handler', () => {
  const onAlertTriggered = jest.fn()
  dispatchEnvelope(envelope(VALID_PAYLOAD), { onAlertTriggered })
  expect(onAlertTriggered).toHaveBeenCalledTimes(1)
  expect(onAlertTriggered).toHaveBeenCalledWith(VALID_PAYLOAD)
})

it.each([
  ['missing condition', { ...VALID_PAYLOAD, condition: undefined }],
  ['wrong-typed watch_id', { ...VALID_PAYLOAD, watch_id: 'seven' }],
  ['unknown kind', { ...VALID_PAYLOAD, kind: 'forecast_probability' }],
  ['non-numeric values entry', { ...VALID_PAYLOAD, values: { rsi: 'low' } }],
  // strict(): a recommendation-shaped extra field must not ride along (ADR-0029).
  ['smuggled action field', { ...VALID_PAYLOAD, action: 'buy' }],
  ['not an object at all', 'rsi crossed'],
])('drops a malformed payload (%s) with a logged warning', (_name, payload) => {
  const warn = jest.spyOn(console, 'warn').mockImplementation(() => undefined)
  const onAlertTriggered = jest.fn()
  dispatchEnvelope(envelope(payload), { onAlertTriggered })
  expect(onAlertTriggered).not.toHaveBeenCalled()
  expect(warn).toHaveBeenCalledWith(
    expect.stringContaining('dropping malformed alert.triggered payload'),
    expect.anything(),
  )
})
