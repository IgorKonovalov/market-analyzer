/**
 * The chart's scan toolbar (Plan 0049 phase 8 / Plan 0064 phase 5 / Plan 0071
 * phase 1 — lifted verbatim out of `CandlestickChart` in the Plan 0072 phase 8
 * decomposition, no behaviour change).
 *
 * Presentational: the optional agent-mode range-select toggle, the "Candlesticks"
 * (candlestick-marker sweep) and "Chart patterns" (trendline sweep) buttons, and
 * each button's status/error read-out. All state + triggers are owned upstream
 * (`useChartScans`, `useChartGestures`) and passed in. Reuses the parent chart
 * stylesheet so the controls render byte-identically to before the extraction —
 * the `data-testid`s are unchanged behavioural anchors.
 */
import type { ScanStatus } from '../hooks/useChartScans'
import { t } from '../lib/i18n'
import styles from './CandlestickChart.module.css'

interface ChartToolbarProps {
  /** When true, the agent-mode range-select toggle is shown. */
  agentModeEnabled: boolean
  selectRangeMode: boolean
  toggleSelectRange: () => void
  scanStatus: ScanStatus
  chartScanStatus: ScanStatus
  onScanPatterns: () => void
  onScanChartPatterns: () => void
  /** Both scan buttons are disabled until symbol + timeframe are known. */
  symbol: string | undefined
  timeframe: string | undefined
}

export function ChartToolbar({
  agentModeEnabled,
  selectRangeMode,
  toggleSelectRange,
  scanStatus,
  chartScanStatus,
  onScanPatterns,
  onScanChartPatterns,
  symbol,
  timeframe,
}: ChartToolbarProps): JSX.Element {
  return (
    <div className={styles.controls}>
      {agentModeEnabled && (
        <button
          type="button"
          data-testid="select-range-toggle"
          aria-pressed={selectRangeMode}
          className={styles.selectRangeButton}
          onClick={toggleSelectRange}
        >
          {selectRangeMode ? t('chart.selectingRange') : t('chart.selectRange')}
        </button>
      )}
      <button
        type="button"
        data-testid="scan-patterns-button"
        className={styles.scanButton}
        onClick={onScanPatterns}
        disabled={scanStatus.kind === 'scanning' || !symbol || !timeframe}
      >
        {scanStatus.kind === 'scanning' ? t('chart.scanning') : t('chart.candlesticks')}
      </button>
      {scanStatus.kind === 'done' && (
        <span data-testid="scan-patterns-status" className={styles.scanStatus}>
          {t('chart.patternCount', { count: scanStatus.count })}
        </span>
      )}
      {scanStatus.kind === 'empty' && (
        <span data-testid="scan-patterns-status" className={styles.scanStatus}>
          {t('chart.noPatternsInView')}
        </span>
      )}
      {scanStatus.kind === 'error' && (
        <span data-testid="scan-patterns-error" role="alert" className={styles.scanError}>
          {scanStatus.message}
        </span>
      )}
      <button
        type="button"
        data-testid="scan-chart-patterns-button"
        className={styles.scanButton}
        onClick={onScanChartPatterns}
        disabled={chartScanStatus.kind === 'scanning' || !symbol || !timeframe}
      >
        {chartScanStatus.kind === 'scanning' ? t('chart.scanning') : t('chart.chartPatterns')}
      </button>
      {chartScanStatus.kind === 'done' && (
        <span data-testid="scan-chart-patterns-status" className={styles.scanStatus}>
          {t('chart.patternCount', { count: chartScanStatus.count })}
        </span>
      )}
      {chartScanStatus.kind === 'empty' && (
        <span data-testid="scan-chart-patterns-status" className={styles.scanStatus}>
          {t('chart.noChartPatternsInView')}
        </span>
      )}
      {chartScanStatus.kind === 'error' && (
        <span data-testid="scan-chart-patterns-error" role="alert" className={styles.scanError}>
          {chartScanStatus.message}
        </span>
      )}
    </div>
  )
}
