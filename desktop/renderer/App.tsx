/**
 * Two views: OHLCV chart (default) and MCP settings. Plan 0006 phase 5 added
 * the toggle. Top-level state is the view selector; no router because two
 * screens don't warrant a new dep — adopting react-router is an ADR-level
 * decision left to a later plan with more views in flight.
 */
import { useState } from 'react'

import styles from './App.module.css'
import { OhlcvView } from './views/OhlcvView'
import { SettingsView } from './views/SettingsView'

type View = 'chart' | 'settings'

export function App(): JSX.Element {
  const [view, setView] = useState<View>('chart')

  return (
    <main className="appShell">
      <header className="appHeader">
        <h1>market-analyser</h1>
        <nav className={styles.nav} aria-label="Primary">
          <button
            type="button"
            className={styles.tab}
            aria-current={view === 'chart' ? 'page' : undefined}
            onClick={() => setView('chart')}
            data-testid="nav-chart"
          >
            Chart
          </button>
          <button
            type="button"
            className={styles.tab}
            aria-current={view === 'settings' ? 'page' : undefined}
            onClick={() => setView('settings')}
            data-testid="nav-settings"
          >
            Settings
          </button>
        </nav>
      </header>
      {view === 'chart' ? <OhlcvView /> : <SettingsView />}
    </main>
  )
}
