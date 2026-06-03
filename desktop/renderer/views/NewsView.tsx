/**
 * News view (Plan 0023 phase 2) — recent headlines + aggregate tone for a
 * symbol, backed by `GET /news` through the typed fetch client.
 *
 * Feed content is untrusted external input (ADR-0008 §security): titles and
 * summaries render as text (React escapes by default — never
 * `dangerouslySetInnerHTML`), and a headline URL becomes a clickable link only
 * when it sanitizes to an `http(s)` href, opened in the OS browser via
 * `shell.openExternal` (never an in-app window).
 *
 * The per-headline badge reflects the *sign* of `compound_sentiment`. The
 * aggregate tone header shows the server-computed `score` + breakdown counts
 * verbatim — the ±0.05 bucketing thresholds live server-side and are not
 * re-implemented here (Plan 0023 Decision).
 */
import { useEffect, useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'

import { api } from '../api/client'
import type { NewsWindow } from '../api/client'
import { formatDateTime, formatRatio } from '../lib/format'
import type { NewsItem } from '../types/sidecar/news-item'
import type { NewsResponse } from '../types/sidecar/news-response'
import type { SentimentSample } from '../types/sidecar/sentiment-sample'
import styles from './NewsView.module.css'

const NEWS_WINDOWS: readonly NewsWindow[] = ['1h', '4h', '24h', '7d']

interface FetchState {
  status: 'loading' | 'ready' | 'error'
  data: NewsResponse | null
  error: string | null
}

interface Query {
  symbol: string
  window: NewsWindow
}

type Tone = 'bullish' | 'bearish' | 'neutral'

/**
 * Reduce a feed-supplied URL to an `http(s)` href, or `null` when it isn't safe
 * to render as a link. `javascript:`/`file:`/`data:` schemes must never become
 * clickable; the same constraint is re-checked at the IPC boundary by
 * `ShellOpenExternalSchema`, but the renderer never even offers the link.
 */
function safeHttpHref(url: string): string | null {
  try {
    const { protocol, href } = new URL(url)
    return protocol === 'http:' || protocol === 'https:' ? href : null
  } catch {
    return null
  }
}

/** Per-headline tone from the sign of the VADER compound score (no thresholds). */
function toneOfSign(score: number | null | undefined): Tone {
  if (score === null || score === undefined || score === 0) return 'neutral'
  return score > 0 ? 'bullish' : 'bearish'
}

const TONE_CLASS: Record<Tone, string> = {
  bullish: styles.badgeBullish,
  bearish: styles.badgeBearish,
  neutral: styles.badgeNeutral,
}

const TONE_LABEL: Record<Tone, string> = {
  bullish: 'Bullish',
  bearish: 'Bearish',
  neutral: 'Neutral',
}

export function NewsView(): JSX.Element {
  const [symbolInput, setSymbolInput] = useState('')
  const [windowSel, setWindowSel] = useState<NewsWindow>('24h')
  // The query that actually drives a fetch — distinct from the input fields so
  // typing doesn't fire a request per keystroke. A new object on each submit /
  // window change re-triggers the effect even when the values repeat.
  const [query, setQuery] = useState<Query>({ symbol: '', window: '24h' })
  const [state, setState] = useState<FetchState>({ status: 'loading', data: null, error: null })

  useEffect(() => {
    let cancelled = false
    setState((s) => ({ ...s, status: 'loading', error: null }))
    api
      .getNews({ symbol: query.symbol, window: query.window })
      .then((data) => {
        if (cancelled) return
        setState({ status: 'ready', data, error: null })
      })
      .catch((err: unknown) => {
        if (cancelled) return
        const message = err instanceof Error ? err.message : 'failed to load news'
        setState({ status: 'error', data: null, error: message })
      })
    return () => {
      cancelled = true
    }
  }, [query])

  const onSubmit = (e: FormEvent): void => {
    e.preventDefault()
    setQuery({ symbol: symbolInput.trim(), window: windowSel })
  }

  const onWindowChange = (e: ChangeEvent<HTMLSelectElement>): void => {
    const next = e.target.value as NewsWindow
    setWindowSel(next)
    setQuery({ symbol: symbolInput.trim(), window: next })
  }

  const ready = state.status === 'ready' ? state.data : null

  return (
    <section className={styles.root} aria-label="News">
      <header className={styles.header}>
        <h2 className={styles.title}>News</h2>
        <p className={styles.lede}>
          Recent headlines and aggregate tone. Leave the symbol blank to browse all feeds.
        </p>
      </header>

      <form className={styles.controls} onSubmit={onSubmit}>
        <div className={styles.field}>
          <label htmlFor="news-symbol">Symbol</label>
          <input
            id="news-symbol"
            type="text"
            value={symbolInput}
            placeholder="e.g. BTC (blank = all feeds)"
            onChange={(e) => setSymbolInput(e.target.value)}
          />
        </div>
        <div className={styles.field}>
          <label htmlFor="news-window">Window</label>
          <select id="news-window" value={windowSel} onChange={onWindowChange}>
            {NEWS_WINDOWS.map((w) => (
              <option key={w} value={w}>
                {w}
              </option>
            ))}
          </select>
        </div>
        <button type="submit">Load</button>
      </form>

      {ready?.sentiment && <ToneHeader sentiment={ready.sentiment} />}

      {state.status === 'loading' && (
        <div className={styles.statusBlock} role="status">
          Loading news…
        </div>
      )}
      {state.status === 'error' && (
        <div className={styles.error} role="alert">
          {state.error ?? 'failed to load news'}
        </div>
      )}
      {ready && ready.items.length === 0 && (
        <div className={styles.statusBlock} role="status" data-testid="news-empty">
          No headlines in this window.
        </div>
      )}
      {ready && ready.items.length > 0 && (
        <ul className={styles.list} aria-label="Headlines">
          {ready.items.map((item, i) => (
            <HeadlineRow key={`${item.url}-${i}`} item={item} />
          ))}
        </ul>
      )}
    </section>
  )
}

interface ToneHeaderProps {
  sentiment: SentimentSample
}

function ToneHeader({ sentiment }: ToneHeaderProps): JSX.Element {
  const breakdown = sentiment.breakdown ?? {}
  const pos = breakdown.positive ?? 0
  const neg = breakdown.negative ?? 0
  const neu = breakdown.neutral ?? 0
  const tone = toneOfSign(sentiment.score)
  return (
    <div className={styles.tone} data-testid="news-tone">
      <span className={`${styles.badge} ${TONE_CLASS[tone]}`} data-tone={tone}>
        {TONE_LABEL[tone]}
      </span>
      <span className={styles.toneScore}>tone {formatRatio(sentiment.score)}</span>
      <span className={styles.toneCounts}>
        {pos} pos / {neg} neg / {neu} neu
      </span>
    </div>
  )
}

interface HeadlineRowProps {
  item: NewsItem
}

function HeadlineRow({ item }: HeadlineRowProps): JSX.Element {
  const href = safeHttpHref(item.url)
  const tone = toneOfSign(item.compound_sentiment)
  return (
    <li className={styles.row} data-testid="news-row">
      <div className={styles.rowMain}>
        {href ? (
          <a
            className={styles.headlineLink}
            href={href}
            onClick={(e) => {
              e.preventDefault()
              // ADR-0008: external URLs open in the OS browser, never in-app.
              void window.api?.shell?.openExternal({ url: href })
            }}
          >
            {item.title}
          </a>
        ) : (
          <span className={styles.headlinePlain}>{item.title}</span>
        )}
        <span
          className={`${styles.badge} ${TONE_CLASS[tone]}`}
          data-testid="news-badge"
          data-tone={tone}
        >
          {TONE_LABEL[tone]}
        </span>
      </div>
      <div className={styles.rowMeta}>
        <span className={styles.source}>{item.source}</span>
        <span className={styles.time}>{formatDateTime(item.published_at)} UTC</span>
      </div>
      {item.summary && <p className={styles.summary}>{item.summary}</p>}
    </li>
  )
}
