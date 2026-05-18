/**
 * Single route: the OHLCV candlestick view. Plan 0001 phase 5.
 *
 * Multi-route navigation lands in a future plan; the bootstrap is intentionally
 * one screen.
 */
import { OhlcvView } from './views/OhlcvView'

export function App(): JSX.Element {
  return (
    <main className="appShell">
      <header className="appHeader">
        <h1>market-analyser</h1>
      </header>
      <OhlcvView />
    </main>
  )
}
