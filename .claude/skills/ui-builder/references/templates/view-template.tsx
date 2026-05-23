// Template: desktop/renderer/views/<ViewName>.tsx
//
// A view is a route-level composition. It owns the fetch, the four async states
// (loading / error / empty / populated), and composes presentational components.
//
// Views import from `components/` and `hooks/`. They never own design decisions
// (those live in components) and never own data shapes (those come from the
// sidecar via the typed client).

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/client'
import { CandlestickChart } from '@/components/CandlestickChart'
import { SymbolPicker } from '@/components/SymbolPicker'
import styles from './ViewName.module.css'

export function ViewName() {
  const [symbol, setSymbol] = useState('AAPL')
  const [timeframe, setTimeframe] = useState('1d')

  const {
    data: bars,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: ['ohlcv', symbol, timeframe],
    queryFn: () => api.getOhlcv({ symbol, timeframe }),
    staleTime: 60_000,
  })

  return (
    <div className={styles.root}>
      <header className={styles.toolbar}>
        <SymbolPicker
          symbol={symbol}
          timeframe={timeframe}
          onSymbolChange={setSymbol}
          onTimeframeChange={setTimeframe}
        />
        <button type="button" onClick={() => refetch()} className={styles.refresh}>
          Refresh
        </button>
      </header>

      <main className={styles.body}>
        {isLoading && <div className={styles.skeleton} aria-label="Loading chart" />}
        {error && (
          <div className={styles.error} role="alert">
            <p>Failed to load bars: {error.message}</p>
            <button type="button" onClick={() => refetch()}>
              Retry
            </button>
          </div>
        )}
        {!isLoading && !error && bars && bars.length === 0 && (
          <div className={styles.empty}>No bars in the requested range.</div>
        )}
        {!isLoading && !error && bars && bars.length > 0 && <CandlestickChart bars={bars} />}
      </main>
    </div>
  )
}

// Notes:
// - All four states are visible in the JSX, not commented out or deferred.
//   Spinners that never resolve are the most common UX failure; an explicit
//   error state with a Retry button beats a frozen page every time.
// - `queryKey` is a tuple of identifying params. Don't include things like
//   `Date.now()` — that defeats caching.
// - The view doesn't compute anything market-related. Sharpe, drawdown, deltas
//   come from the sidecar; the renderer renders.
