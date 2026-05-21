/**
 * Plan 0007 phase 4 done-when for `useEventStream`. Six assertions, one per
 * behavioural claim in the plan:
 *   1. On mount, hook constructs `EventSource` with URL of the form
 *      `http://127.0.0.1:<port>/events?token=<bearer>`.
 *   2. A `chart.show v1` message dispatches to the `chart.show` handler with
 *      the parsed payload.
 *   3. A `chart.update v1` envelope dispatches to the `chart.update` handler.
 *   4. A `chart.show v2` envelope (forward-compat) still dispatches to the v1
 *      handler AND `console.warn` is called with a version-mismatch notice.
 *   5. On `EventSource.onerror`, `state` becomes `"reconnecting"`.
 *   6. On unmount, `EventSource.close()` is called.
 *
 * Path: spec is co-located with the hook (`renderer/hooks/`), not under
 * `desktop/tests/` as the plan literally listed — the existing jest.config.ts
 * `roots` only covers `renderer/` and `shared/`. The phase commit message
 * notes this minor plan-vs-reality reconciliation.
 */
import { act, render, waitFor } from '@testing-library/react'
import { useEffect } from 'react'

import {
  type EventStreamHandlers,
  type UseEventStreamResult,
  useEventStream,
} from './useEventStream'

// ---------- EventSource mock ---------------------------------------------- //

interface MockEventSourceInstance {
  url: string
  onopen: ((ev: Event) => void) | null
  onerror: ((ev: Event) => void) | null
  onmessage: ((ev: MessageEvent<string>) => void) | null
  close: jest.Mock
  // Test helpers — not on the spec'd EventSource interface.
  _fireOpen: () => void
  _fireError: () => void
  _fireMessage: (data: unknown) => void
}

let lastEventSource: MockEventSourceInstance | null = null
const eventSourceCtor = jest.fn((url: string) => {
  const instance: MockEventSourceInstance = {
    url,
    onopen: null,
    onerror: null,
    onmessage: null,
    close: jest.fn(),
    _fireOpen() {
      this.onopen?.(new Event('open'))
    },
    _fireError() {
      this.onerror?.(new Event('error'))
    },
    _fireMessage(data: unknown) {
      const payload = typeof data === 'string' ? data : JSON.stringify(data)
      this.onmessage?.({ data: payload } as MessageEvent<string>)
    },
  }
  lastEventSource = instance
  return instance
})

// Re-installed before every test so a stale mock can't leak across cases.
beforeEach(() => {
  eventSourceCtor.mockClear()
  lastEventSource = null
  // jsdom does not ship EventSource; we own the global outright.
  ;(globalThis as unknown as { EventSource: unknown }).EventSource = eventSourceCtor
})

// ---------- window.api mock ----------------------------------------------- //

const FAKE_PORT = 53221
const FAKE_TOKEN = 'renderer-bearer-test-token-with-special-chars/&='

beforeEach(() => {
  ;(globalThis as unknown as { window: { api: unknown } }).window.api = {
    sidecar: {
      getPort: jest.fn().mockResolvedValue({ port: FAKE_PORT, secretToken: FAKE_TOKEN }),
      onStatus: jest.fn(),
    },
  }
  // `api/client.ts` caches `{port, secretToken}` at module level on first
  // resolve. We deliberately keep FAKE_PORT/FAKE_TOKEN identical across tests
  // so the cache (populated by whichever test runs first) holds values that
  // match what every subsequent test expects. No isolateModules ceremony.
})

// ---------- the harness component ---------------------------------------- //

interface HarnessProps {
  handlers: EventStreamHandlers
  onResult?: (r: UseEventStreamResult) => void
}

function Harness({ handlers, onResult }: HarnessProps): JSX.Element {
  const result = useEventStream(handlers)
  useEffect(() => {
    onResult?.(result)
  }, [result, onResult])
  return <div data-testid="harness">{result.state}</div>
}

// Wait until `eventSourceCtor` has been called once (the URL fetch is async),
// then return the latest mock instance.
async function waitForStream(): Promise<MockEventSourceInstance> {
  await waitFor(() => expect(eventSourceCtor).toHaveBeenCalled())
  if (!lastEventSource) throw new Error('EventSource constructor returned no instance')
  return lastEventSource
}

// ---------- specs --------------------------------------------------------- //

describe('useEventStream', () => {
  it('constructs an EventSource with /events?token=<bearer> on mount', async () => {
    render(<Harness handlers={{}} />)
    await waitForStream()

    expect(eventSourceCtor).toHaveBeenCalledTimes(1)
    expect(eventSourceCtor).toHaveBeenCalledWith(
      `http://127.0.0.1:${FAKE_PORT}/events?token=${encodeURIComponent(FAKE_TOKEN)}`,
    )
  })

  it('dispatches chart.show v1 to the chart.show handler with the parsed payload', async () => {
    const onChartShow = jest.fn()
    render(<Harness handlers={{ onChartShow }} />)
    const es = await waitForStream()

    const payload = {
      symbol: 'AAPL',
      timeframe: '1d',
      range_start: '2026-04-20T00:00:00+00:00',
      range_end: '2026-05-20T00:00:00+00:00',
      overlays: [{ kind: 'ema', period: 20 }],
    }
    await act(async () => {
      es._fireMessage({ type: 'chart.show', version: 1, ts: '2026-05-20T14:00:00Z', payload })
    })

    expect(onChartShow).toHaveBeenCalledTimes(1)
    expect(onChartShow).toHaveBeenCalledWith(payload)
  })

  it('dispatches chart.update v1 to the chart.update handler', async () => {
    const onChartUpdate = jest.fn()
    render(<Harness handlers={{ onChartUpdate }} />)
    const es = await waitForStream()

    const payload = { symbol: 'AAPL', timeframe: '1d', overlays: [{ kind: 'ema', period: 50 }] }
    await act(async () => {
      es._fireMessage({
        type: 'chart.update',
        version: 1,
        ts: '2026-05-20T14:00:01Z',
        payload,
      })
    })

    expect(onChartUpdate).toHaveBeenCalledTimes(1)
    expect(onChartUpdate).toHaveBeenCalledWith(payload)
  })

  it('forward-compat: dispatches chart.show v2 to the v1 handler with a warning', async () => {
    const onChartShow = jest.fn()
    const warnSpy = jest.spyOn(console, 'warn').mockImplementation(() => undefined)

    render(<Harness handlers={{ onChartShow }} />)
    const es = await waitForStream()

    const payload = {
      symbol: 'AAPL',
      timeframe: '1d',
      range_start: '2026-04-20T00:00:00+00:00',
      range_end: '2026-05-20T00:00:00+00:00',
      // A v2-only future field — present, but the v1 handler accepts unknown keys.
      brand_new_field: 'tbd',
    }
    await act(async () => {
      es._fireMessage({ type: 'chart.show', version: 2, ts: '2026-05-20T14:00:00Z', payload })
    })

    expect(onChartShow).toHaveBeenCalledTimes(1)
    expect(onChartShow).toHaveBeenCalledWith(payload)
    expect(warnSpy).toHaveBeenCalledWith(expect.stringContaining('chart.show'))
    expect(warnSpy.mock.calls.some((call) => String(call[0]).includes('v2'))).toBe(true)

    warnSpy.mockRestore()
  })

  it('surfaces "reconnecting" state when EventSource emits onerror', async () => {
    const results: UseEventStreamResult[] = []
    render(<Harness handlers={{}} onResult={(r) => results.push(r)} />)
    const es = await waitForStream()

    await act(async () => {
      es._fireError()
    })

    expect(results.at(-1)?.state).toBe('reconnecting')
  })

  it('closes the EventSource on unmount', async () => {
    const { unmount } = render(<Harness handlers={{}} />)
    const es = await waitForStream()
    expect(es.close).not.toHaveBeenCalled()

    unmount()
    expect(es.close).toHaveBeenCalledTimes(1)
  })
})
