/**
 * Collapsible chart side dock (Plan 0096 phase 4).
 *
 * Replaces the old LAYERS checklist `<aside>` (layer control now lives in the
 * inline ChartLegend) with a collapsible, contextual symbol-details panel:
 * last price + change plus the latest bar's OHLC and volume, read from data the
 * chart already holds (the polled quote + the bars) — no new fetch, no sidecar
 * call. Defaults collapsed so the chart opens full-width; the collapsed state
 * persists in `localStorage['ma.rightPanelCollapsed']` (the ADR-0039 `ma.*`
 * convention). Purely presentational + renderer-owned.
 */
import { useState } from 'react'

import { formatInt, formatPct } from '../lib/format'
import { t } from '../lib/i18n'
import type { Bar } from '../types/sidecar/bar'
import type { QuoteResponse } from '../types/sidecar/quote-response'
import styles from './ChartSidePanel.module.css'

const STORAGE_KEY = 'ma.rightPanelCollapsed'

/** Default collapsed: only an explicit `'false'` opens the dock. */
function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) !== 'false'
  } catch {
    return true
  }
}

function persistCollapsed(value: boolean): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, String(value))
  } catch {
    /* storage blocked → collapse is session-only */
  }
}

/** Two-decimal, thousands-separated price/number (en-US per ADR-0063). */
const NUM = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
function num(value: number | undefined): string {
  return value === undefined || !Number.isFinite(value) ? '—' : NUM.format(value)
}

export interface ChartSidePanelProps {
  symbol?: string
  bars: Bar[]
  quote?: QuoteResponse | null
}

export function ChartSidePanel({ symbol, bars, quote }: ChartSidePanelProps): JSX.Element {
  const [collapsed, setCollapsed] = useState(readCollapsed)

  const toggle = (): void => {
    setCollapsed((prev) => {
      const next = !prev
      persistCollapsed(next)
      return next
    })
  }

  if (collapsed) {
    return (
      <div className={styles.collapsed} data-collapsed="true" data-testid="chart-side-panel">
        <button
          type="button"
          className={styles.expandButton}
          onClick={toggle}
          aria-expanded={false}
          aria-label={t('sidePanel.expandAria')}
          data-testid="side-panel-toggle"
        >
          ‹
        </button>
      </div>
    )
  }

  const last = bars[bars.length - 1]
  const prev = bars[bars.length - 2]
  const price = quote?.price ?? last?.close
  const changeFrac =
    last !== undefined && prev !== undefined && prev.close !== 0
      ? (last.close - prev.close) / prev.close
      : undefined
  const asOf = quote?.as_of ?? last?.event_ts

  const rows: Array<{ label: string; value: string; testid: string }> = [
    { label: t('sidePanel.last'), value: num(price), testid: 'last' },
    {
      label: t('sidePanel.change'),
      value: changeFrac === undefined ? '—' : formatPct(changeFrac),
      testid: 'change',
    },
    { label: t('sidePanel.open'), value: num(last?.open), testid: 'open' },
    { label: t('sidePanel.high'), value: num(last?.high), testid: 'high' },
    { label: t('sidePanel.low'), value: num(last?.low), testid: 'low' },
    { label: t('sidePanel.close'), value: num(last?.close), testid: 'close' },
    {
      label: t('sidePanel.volume'),
      value: last?.volume === undefined ? '—' : formatInt(last.volume),
      testid: 'volume',
    },
  ]

  return (
    <aside className={styles.panel} data-collapsed="false" data-testid="chart-side-panel">
      <div className={styles.header}>
        <span className={styles.title}>{symbol ?? t('sidePanel.title')}</span>
        <button
          type="button"
          className={styles.collapseButton}
          onClick={toggle}
          aria-expanded={true}
          aria-label={t('sidePanel.collapseAria')}
          data-testid="side-panel-toggle"
        >
          ›
        </button>
      </div>
      {last === undefined ? (
        <p className={styles.empty}>{t('sidePanel.noData')}</p>
      ) : (
        <dl className={styles.stats}>
          {rows.map((row) => (
            <div key={row.testid} className={styles.statRow}>
              <dt className={styles.statLabel}>{row.label}</dt>
              <dd
                className={`${styles.statValue} ${
                  row.testid === 'change' && changeFrac !== undefined
                    ? changeFrac >= 0
                      ? styles.up
                      : styles.down
                    : ''
                }`}
                data-testid={`side-panel-${row.testid}`}
              >
                {row.value}
              </dd>
            </div>
          ))}
          {asOf !== undefined && (
            <div className={styles.statRow}>
              <dt className={styles.statLabel}>{t('sidePanel.asOf')}</dt>
              <dd className={styles.statValue} data-testid="side-panel-asof">
                {asOf}
              </dd>
            </div>
          )}
        </dl>
      )}
    </aside>
  )
}
