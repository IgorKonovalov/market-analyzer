/**
 * Plan 0006 phase 5 done-when: SettingsView behavior.
 *
 * Defends:
 * - The secret is NOT in the rendered DOM on initial mount — a screen reader
 *   walking the a11y tree before Reveal is clicked must not encounter it.
 * - Clicking Reveal puts the plaintext secret into the DOM.
 * - Clicking Copy invokes navigator.clipboard.writeText with the secret.
 * - Clicking Rotate calls the rotate fetch + updates the displayed secret.
 * - The claude_desktop_config.json snippet contains the placeholder before
 *   Reveal and the real secret after.
 */
import '@testing-library/jest-dom'

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'

import { SettingsView } from './SettingsView'
import { getCandleType, getChartStyleOverrides, resetChartStyle } from '../lib/chartStyle'

const INITIAL_SECRET = 'a'.repeat(64)
const ROTATED_SECRET = 'b'.repeat(64)
const PORT = 54321
const CREATED_AT = '2026-05-20T12:00:00+00:00'

interface MockedFetchCall {
  url: string
  init: RequestInit
}

function setupWindowApi(): void {
  Object.defineProperty(window, 'api', {
    configurable: true,
    writable: true,
    value: {
      sidecar: {
        getPort: jest.fn().mockResolvedValue({ port: PORT, secretToken: 'renderer-secret' }),
        onStatus: jest.fn(),
      },
    },
  })
}

function mockResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => (typeof body === 'string' ? body : JSON.stringify(body)),
    json: async () => body,
  } as unknown as Response
}

function setupFetch(): { calls: MockedFetchCall[] } {
  const calls: MockedFetchCall[] = []
  global.fetch = jest.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = typeof input === 'string' ? input : input.toString()
    calls.push({ url, init })
    if (url.endsWith('/settings/mcp-secret')) {
      return mockResponse({ secret: INITIAL_SECRET, created_at: CREATED_AT })
    }
    if (url.endsWith('/settings/mcp-secret/rotate')) {
      return mockResponse({ secret: ROTATED_SECRET, created_at: CREATED_AT })
    }
    return mockResponse('not mocked', 500)
  }) as unknown as typeof fetch
  return { calls }
}

function setupClipboard(): { writeText: jest.Mock } {
  const writeText = jest.fn().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', {
    configurable: true,
    value: { writeText },
  })
  return { writeText }
}

function installMatchMedia(initialDark: boolean): void {
  const state = { matches: initialDark }
  window.matchMedia = jest.fn().mockReturnValue({
    get matches() {
      return state.matches
    },
    media: '(prefers-color-scheme: dark)',
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => true,
  }) as unknown as typeof window.matchMedia
}

beforeEach(() => {
  setupWindowApi()
  window.localStorage.clear()
  resetChartStyle()
  delete document.documentElement.dataset.theme
  installMatchMedia(false)
})

afterEach(() => {
  jest.restoreAllMocks()
})

describe('SettingsView — secret hiding', () => {
  it('does not render the plaintext secret on initial mount', async () => {
    setupFetch()
    setupClipboard()

    render(<SettingsView />)

    // Wait for the fetch to settle so the record is loaded into component state.
    await screen.findByTestId('mcp-secret-hidden')

    // The secret is in component state — assert it does NOT escape into the DOM.
    expect(screen.queryByText(INITIAL_SECRET)).toBeNull()
    expect(screen.queryByTestId('mcp-secret-plaintext')).toBeNull()
    // The bullet placeholder is present and explicitly aria-hidden so screen
    // readers don't speak it either.
    const hidden = screen.getByTestId('mcp-secret-hidden')
    expect(hidden).toHaveAttribute('aria-hidden', 'true')
  })

  it('puts the plaintext secret in the DOM after Reveal is clicked', async () => {
    setupFetch()
    setupClipboard()

    render(<SettingsView />)
    await screen.findByTestId('mcp-secret-reveal')

    fireEvent.click(screen.getByTestId('mcp-secret-reveal'))

    const plaintext = await screen.findByTestId('mcp-secret-plaintext')
    expect(plaintext).toHaveTextContent(INITIAL_SECRET)
    expect(screen.queryByTestId('mcp-secret-hidden')).toBeNull()
  })

  it('hides the secret again when Hide is clicked', async () => {
    setupFetch()
    setupClipboard()

    render(<SettingsView />)
    await screen.findByTestId('mcp-secret-reveal')
    fireEvent.click(screen.getByTestId('mcp-secret-reveal'))
    await screen.findByTestId('mcp-secret-plaintext')

    fireEvent.click(screen.getByTestId('mcp-secret-hide'))

    await screen.findByTestId('mcp-secret-hidden')
    expect(screen.queryByText(INITIAL_SECRET)).toBeNull()
  })
})

describe('SettingsView — copy', () => {
  it('writes the secret to the clipboard when Copy is clicked', async () => {
    setupFetch()
    const clipboard = setupClipboard()

    render(<SettingsView />)
    await screen.findByTestId('mcp-secret-reveal')
    fireEvent.click(screen.getByTestId('mcp-secret-reveal'))
    await screen.findByTestId('mcp-secret-plaintext')

    fireEvent.click(screen.getByTestId('mcp-secret-copy'))

    await waitFor(() => {
      expect(clipboard.writeText).toHaveBeenCalledWith(INITIAL_SECRET)
    })
    expect(screen.getByTestId('mcp-secret-copy')).toHaveTextContent('Copied!')
  })

  it('disables Copy until the secret is revealed', async () => {
    setupFetch()
    setupClipboard()

    render(<SettingsView />)
    await screen.findByTestId('mcp-secret-reveal')

    expect(screen.getByTestId('mcp-secret-copy')).toBeDisabled()
  })
})

describe('SettingsView — rotate', () => {
  it('calls the rotate endpoint and updates the displayed secret', async () => {
    const { calls } = setupFetch()
    setupClipboard()

    render(<SettingsView />)
    await screen.findByTestId('mcp-secret-rotate')

    await act(async () => {
      fireEvent.click(screen.getByTestId('mcp-secret-rotate'))
    })

    const plaintext = await screen.findByTestId('mcp-secret-plaintext')
    expect(plaintext).toHaveTextContent(ROTATED_SECRET)
    expect(plaintext).not.toHaveTextContent(INITIAL_SECRET)

    const rotateCall = calls.find((c) => c.url.endsWith('/settings/mcp-secret/rotate'))
    expect(rotateCall).toBeDefined()
    expect(rotateCall?.init.method).toBe('POST')
  })
})

describe('SettingsView — Appearance theme control', () => {
  const radio = (name: string): HTMLInputElement =>
    screen.getByRole('radio', { name }) as HTMLInputElement

  it('renders all three options with System selected by default', async () => {
    setupFetch()
    setupClipboard()

    render(<SettingsView />)
    await screen.findByTestId('theme-option-system')

    expect(radio('System').checked).toBe(true)
    expect(radio('Light').checked).toBe(false)
    expect(radio('Dark').checked).toBe(false)
  })

  it('selecting Dark sets the theme (data-theme + localStorage) and checks Dark', async () => {
    setupFetch()
    setupClipboard()

    render(<SettingsView />)
    await screen.findByTestId('theme-option-dark')
    fireEvent.click(radio('Dark'))

    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(window.localStorage.getItem('ma.theme')).toBe('dark')
    expect(radio('Dark').checked).toBe(true)
  })

  it('selecting Light pins light regardless of OS', async () => {
    setupFetch()
    setupClipboard()

    render(<SettingsView />)
    await screen.findByTestId('theme-option-light')
    fireEvent.click(radio('Light'))

    expect(document.documentElement.dataset.theme).toBe('light')
    expect(window.localStorage.getItem('ma.theme')).toBe('light')
  })

  it('selecting System removes the attribute and clears storage', async () => {
    setupFetch()
    setupClipboard()

    render(<SettingsView />)
    await screen.findByTestId('theme-option-dark')
    // Pin dark first, then back to system.
    fireEvent.click(radio('Dark'))
    fireEvent.click(radio('System'))

    expect(document.documentElement.dataset.theme).toBeUndefined()
    expect(window.localStorage.getItem('ma.theme')).toBeNull()
    expect(radio('System').checked).toBe(true)
  })
})

describe('SettingsView — Chart style controls (Plan 0068 phase 3)', () => {
  const radio = (name: string): HTMLInputElement =>
    screen.getByRole('radio', { name }) as HTMLInputElement

  it('writes a colour override for the active theme and reflects the new value', async () => {
    setupFetch()
    setupClipboard()

    render(<SettingsView />)
    const input = (await screen.findByTestId('chart-style-color-candleUp')) as HTMLInputElement

    fireEvent.change(input, { target: { value: '#123456' } })

    // Active effective theme is light (system + OS light); the override lands there.
    expect(getChartStyleOverrides().light.candleUp?.color).toBe('#123456')
    expect(getChartStyleOverrides().dark.candleUp).toBeUndefined()
    // The control reflects the new value.
    expect((screen.getByTestId('chart-style-color-candleUp') as HTMLInputElement).value).toBe(
      '#123456',
    )
  })

  it('writes an in-range line-width override', async () => {
    setupFetch()
    setupClipboard()

    render(<SettingsView />)
    const width = (await screen.findByTestId('chart-style-width-ema')) as HTMLSelectElement

    fireEvent.change(width, { target: { value: '3' } })

    expect(getChartStyleOverrides().light.ema?.lineWidth).toBe(3)
    expect((screen.getByTestId('chart-style-width-ema') as HTMLSelectElement).value).toBe('3')
  })

  it('edits the OTHER theme after switching theme in Appearance (per-theme model)', async () => {
    setupFetch()
    setupClipboard()

    render(<SettingsView />)
    await screen.findByTestId('chart-style-editing-theme')
    expect(screen.getByTestId('chart-style-editing-theme')).toHaveTextContent('Editing Light')

    fireEvent.click(radio('Dark'))

    expect(screen.getByTestId('chart-style-editing-theme')).toHaveTextContent('Editing Dark')
    fireEvent.change(screen.getByTestId('chart-style-color-candleUp'), {
      target: { value: '#0b0b0b' },
    })
    // The override targets dark now; light is untouched.
    expect(getChartStyleOverrides().dark.candleUp?.color).toBe('#0b0b0b')
    expect(getChartStyleOverrides().light.candleUp).toBeUndefined()
  })

  it('Reset chart style clears every override back to defaults', async () => {
    setupFetch()
    setupClipboard()

    render(<SettingsView />)
    fireEvent.change(await screen.findByTestId('chart-style-color-vwap'), {
      target: { value: '#abcdef' },
    })
    fireEvent.change(screen.getByTestId('chart-style-width-vwap'), { target: { value: '4' } })
    expect(getChartStyleOverrides().light.vwap).toBeDefined()

    fireEvent.click(screen.getByTestId('chart-style-reset'))

    expect(getChartStyleOverrides()).toEqual({ light: {}, dark: {} })
  })

  it('labels each control and announces the theme indicator', async () => {
    setupFetch()
    setupClipboard()

    render(<SettingsView />)
    // Each colour input has an associated <label> (accessible name).
    expect(await screen.findByTestId('chart-style-color-vwap')).toHaveAccessibleName('VWAP')
    // The width control is labelled too.
    expect(screen.getByTestId('chart-style-width-vwap')).toHaveAccessibleName('Width')
    // The "Editing <theme>" indicator is a live region.
    expect(screen.getByTestId('chart-style-editing-theme')).toHaveAttribute('aria-live', 'polite')
  })
})

describe('SettingsView — Candle type control (Plan 0068 phase 4)', () => {
  const candleRadio = (name: string): HTMLInputElement =>
    screen.getByRole('radio', { name }) as HTMLInputElement

  it('selecting a candle type writes it to the store', async () => {
    setupFetch()
    setupClipboard()

    render(<SettingsView />)
    await screen.findByTestId('candle-type-control')

    fireEvent.click(candleRadio('Line'))

    expect(getCandleType()).toBe('line')
    expect(candleRadio('Line').checked).toBe(true)
  })

  it('disables the candle up/down colour controls and shows a note in Line mode', async () => {
    setupFetch()
    setupClipboard()

    render(<SettingsView />)
    await screen.findByTestId('candle-type-control')

    // Candle mode: up/down enabled, no note.
    expect(screen.getByTestId('chart-style-color-candleUp')).not.toBeDisabled()
    expect(screen.queryByTestId('candle-type-note')).toBeNull()

    fireEvent.click(candleRadio('Line'))

    // Line mode: up + down colour controls inert, note explains the single colour.
    expect(screen.getByTestId('chart-style-color-candleUp')).toBeDisabled()
    expect(screen.getByTestId('chart-style-color-candleDown')).toBeDisabled()
    expect(screen.getByTestId('candle-type-note')).toBeInTheDocument()
    // A non-candle element (VWAP) stays editable.
    expect(screen.getByTestId('chart-style-color-vwap')).not.toBeDisabled()
  })

  it('Reset chart style also clears the candle type back to candles', async () => {
    setupFetch()
    setupClipboard()

    render(<SettingsView />)
    await screen.findByTestId('candle-type-control')
    fireEvent.click(candleRadio('Area'))
    expect(getCandleType()).toBe('area')

    fireEvent.click(screen.getByTestId('chart-style-reset'))

    expect(getCandleType()).toBe('candles')
    expect(candleRadio('Candles').checked).toBe(true)
  })
})

describe('SettingsView — Claude Desktop snippet', () => {
  it('shows a placeholder for the secret before Reveal', async () => {
    setupFetch()
    setupClipboard()

    render(<SettingsView />)
    await screen.findByTestId('mcp-config-snippet')

    const snippet = screen.getByTestId('mcp-config-snippet')
    expect(snippet).toHaveTextContent('<click Reveal to see secret>')
    expect(snippet).not.toHaveTextContent(INITIAL_SECRET)
  })

  it('substitutes the real secret into the snippet after Reveal', async () => {
    setupFetch()
    setupClipboard()

    render(<SettingsView />)
    await screen.findByTestId('mcp-secret-reveal')
    fireEvent.click(screen.getByTestId('mcp-secret-reveal'))

    const snippet = await screen.findByTestId('mcp-config-snippet')
    await waitFor(() => {
      expect(snippet).toHaveTextContent(INITIAL_SECRET)
    })
    expect(snippet).toHaveTextContent(`http://127.0.0.1:${PORT}/mcp`)
    expect(snippet).not.toHaveTextContent('<click Reveal to see secret>')
  })
})
