/**
 * The bootstrap's single route. Composes SymbolPicker + CandlestickChart and
 * owns the four async states (loading / error / empty / populated). Default
 * range is the last 365 days; refresh re-pulls the same window.
 */
import { useMemo, useState } from "react";

import { CandlestickChart } from "../components/CandlestickChart";
import { SymbolPicker } from "../components/SymbolPicker";
import type { Timeframe } from "../components/SymbolPicker";
import { useOhlcv } from "../hooks/useOhlcv";
import styles from "./OhlcvView.module.css";

const DEFAULT_SYMBOL = "AAPL";
const DEFAULT_TIMEFRAME: Timeframe = "1d";
const DEFAULT_LOOKBACK_DAYS = 365;

export function OhlcvView(): JSX.Element {
  const [symbol, setSymbol] = useState(DEFAULT_SYMBOL);
  const [timeframe, setTimeframe] = useState<Timeframe>(DEFAULT_TIMEFRAME);

  const { start, end } = useMemo(() => {
    const now = new Date();
    const past = new Date(now.getTime() - DEFAULT_LOOKBACK_DAYS * 24 * 60 * 60 * 1000);
    return { start: past, end: now };
  }, []);

  const { bars, isLoading, error, refetch } = useOhlcv({ symbol, timeframe, start, end });

  return (
    <section className={styles.root} aria-label={`OHLCV view for ${symbol} ${timeframe}`}>
      <header className={styles.toolbar}>
        <SymbolPicker
          symbol={symbol}
          timeframe={timeframe}
          onSymbolChange={setSymbol}
          onTimeframeChange={setTimeframe}
          disabled={isLoading}
        />
        <button
          type="button"
          className={styles.refresh}
          onClick={refetch}
          disabled={isLoading}
        >
          Refresh
        </button>
      </header>

      <div className={styles.body}>
        {isLoading && (
          <div className={styles.skeleton} role="status" aria-label="Loading chart">
            Loading {symbol} {timeframe}…
          </div>
        )}
        {!isLoading && error && (
          <div className={styles.error} role="alert">
            <p>
              Failed to load <strong>{symbol}</strong> {timeframe}: {error.message}
            </p>
            <button type="button" onClick={refetch}>
              Retry
            </button>
          </div>
        )}
        {!isLoading && !error && bars && bars.length === 0 && (
          <div className={styles.empty}>
            No bars for {symbol} {timeframe} in the last {DEFAULT_LOOKBACK_DAYS} days.
          </div>
        )}
        {!isLoading && !error && bars && bars.length > 0 && (
          <CandlestickChart
            bars={bars}
            ariaLabel={`Candlestick chart for ${symbol} ${timeframe}, ${bars.length} bars`}
          />
        )}
      </div>
    </section>
  );
}
