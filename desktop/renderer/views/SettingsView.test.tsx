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

beforeEach(() => {
  setupWindowApi()
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
