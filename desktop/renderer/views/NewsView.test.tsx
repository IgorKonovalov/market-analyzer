/**
 * Plan 0023 phase 2 done-when: the News view renders headlines + tone, handles
 * empty/error states, drives fetches from its controls, renders untrusted feed
 * content safely, and is reachable from the nav.
 */
import '@testing-library/jest-dom'

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'

// `api.getNews` is the seam every test drives. Mocking the client module also
// keeps the App nav test from making real sidecar calls.
jest.mock('../api/client', () => ({
  api: { getNews: jest.fn() },
}))
// App mounts the SSE stream + the chart view on render; neither is relevant to
// the nav assertion, so stub them out to keep the test fast and deterministic.
jest.mock('../hooks/useEventStream', () => ({ useEventStream: () => undefined }))
jest.mock('./OhlcvView', () => ({ OhlcvView: () => <div data-testid="ohlcv-stub" /> }))

import { api } from '../api/client'
import { App } from '../App'
import { NewsView } from './NewsView'

const getNews = api.getNews as jest.Mock

function response(overrides: Record<string, unknown> = {}): unknown {
  return {
    items: [],
    sentiment: null,
    queried_at: '2026-05-20T12:00:00Z',
    ...overrides,
  }
}

const ITEMS = [
  {
    symbol: 'BTC',
    title: 'Bitcoin surges to a new all-time high',
    url: 'https://www.coindesk.com/a',
    published_at: '2026-05-20T11:30:00Z',
    source: 'coindesk',
    summary: 'A strong rally.',
    compound_sentiment: 0.9274,
  },
  {
    symbol: 'BTC',
    title: 'Regulators weigh new crypto rules',
    url: 'https://www.reuters.com/b',
    published_at: '2026-05-20T09:15:00Z',
    source: 'reuters',
    summary: 'Mixed signals.',
    compound_sentiment: -0.4019,
  },
  {
    symbol: 'BTC',
    title: 'Markets flat ahead of data',
    url: 'https://apnews.com/c',
    published_at: '2026-05-20T08:00:00Z',
    source: 'apnews',
    summary: '',
    compound_sentiment: 0,
  },
]

const SENTIMENT = {
  symbol: 'BTC',
  score: 0.2628,
  window: '24h',
  as_of: '2026-05-20T12:00:00Z',
  source: 'rss-vader',
  breakdown: { positive: 1, negative: 1, neutral: 1 },
}

beforeEach(() => {
  getNews.mockReset()
})

it('renders three headline rows with sign-matched badges and a tone header', async () => {
  getNews.mockResolvedValue(response({ items: ITEMS, sentiment: SENTIMENT }))
  render(<NewsView />)

  const rows = await screen.findAllByTestId('news-row')
  expect(rows).toHaveLength(3)

  expect(within(rows[0]).getByText('Bitcoin surges to a new all-time high')).toBeInTheDocument()
  expect(within(rows[0]).getByText('coindesk')).toBeInTheDocument()
  expect(within(rows[0]).getByText(/2026-05-20 11:30 UTC/)).toBeInTheDocument()

  // Badge variant tracks the sign of compound_sentiment: +, −, 0.
  expect(within(rows[0]).getByTestId('news-badge')).toHaveAttribute('data-tone', 'bullish')
  expect(within(rows[1]).getByTestId('news-badge')).toHaveAttribute('data-tone', 'bearish')
  expect(within(rows[2]).getByTestId('news-badge')).toHaveAttribute('data-tone', 'neutral')

  // Tone header: server-computed score (verbatim) + the three breakdown counts.
  const tone = screen.getByTestId('news-tone')
  expect(tone).toHaveTextContent('+0.26')
  expect(tone).toHaveTextContent('1 pos / 1 neg / 1 neu')
})

it('shows an explicit empty affordance when there are no headlines', async () => {
  getNews.mockResolvedValue(response({ items: [], sentiment: null }))
  render(<NewsView />)

  expect(await screen.findByTestId('news-empty')).toHaveTextContent(/no headlines in this window/i)
})

it('shows an error message when the fetch rejects, without blanking', async () => {
  getNews.mockRejectedValue(new Error('sidecar 502: upstream unavailable'))
  render(<NewsView />)

  const alert = await screen.findByRole('alert')
  expect(alert).toHaveTextContent('sidecar 502: upstream unavailable')
})

it('issues getNews with the matching symbol and window as the controls change', async () => {
  getNews.mockResolvedValue(response({ items: [], sentiment: null }))
  render(<NewsView />)

  // Mount fetch (browse mode: blank symbol).
  await waitFor(() => expect(getNews).toHaveBeenCalledWith(expect.objectContaining({ symbol: '' })))
  getNews.mockClear()

  // Symbol input + submit.
  fireEvent.change(screen.getByLabelText('Symbol'), { target: { value: 'BTC' } })
  fireEvent.click(screen.getByRole('button', { name: 'Load' }))
  await waitFor(() =>
    expect(getNews).toHaveBeenCalledWith(expect.objectContaining({ symbol: 'BTC', window: '24h' })),
  )
  getNews.mockClear()

  // Window select change refetches immediately with the new window.
  fireEvent.change(screen.getByLabelText('Window'), { target: { value: '7d' } })
  await waitFor(() =>
    expect(getNews).toHaveBeenCalledWith(expect.objectContaining({ symbol: 'BTC', window: '7d' })),
  )
})

it('renders an http(s) headline as a link but a javascript: URL as non-clickable text', async () => {
  const items = [
    { ...ITEMS[0], title: 'Safe headline', url: 'https://example.com/ok' },
    { ...ITEMS[1], title: 'Malicious headline', url: 'javascript:alert(1)' },
  ]
  getNews.mockResolvedValue(response({ items, sentiment: null }))
  render(<NewsView />)

  const link = await screen.findByRole('link', { name: 'Safe headline' })
  expect(link).toHaveAttribute('href', 'https://example.com/ok')

  // The malicious headline still shows (as text) but is not a link.
  expect(screen.getByText('Malicious headline')).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Malicious headline' })).not.toBeInTheDocument()
  expect(screen.getAllByRole('link')).toHaveLength(1)
})

it('opens a clicked headline in the OS browser via shell.openExternal, not in-app', async () => {
  const openExternal = jest.fn().mockResolvedValue(undefined)
  // @ts-expect-error — minimal window.api stub for this test only.
  window.api = { shell: { openExternal } }
  getNews.mockResolvedValue(response({ items: [ITEMS[0]], sentiment: null }))
  render(<NewsView />)

  const link = await screen.findByRole('link', { name: ITEMS[0].title })
  fireEvent.click(link)

  expect(openExternal).toHaveBeenCalledWith({ url: 'https://www.coindesk.com/a' })
  // @ts-expect-error — tear down the stub.
  delete window.api
})

it('exposes a News menu item that mounts the News view when selected', async () => {
  getNews.mockResolvedValue(response({ items: [], sentiment: null }))
  render(<App />)

  // News folded into the collapsed nav menu (Plan 0096 phase 5) — open it first.
  fireEvent.click(screen.getByTestId('nav-menu-trigger'))
  const newsItem = screen.getByRole('menuitem', { name: 'News' })
  expect(newsItem).toBeInTheDocument()

  fireEvent.click(newsItem)
  expect(await screen.findByRole('region', { name: 'News' })).toBeInTheDocument()
})
