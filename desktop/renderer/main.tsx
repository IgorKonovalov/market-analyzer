import React from 'react'
import ReactDOM from 'react-dom/client'
import { App } from './App'
import { applyTheme, getStoredTheme } from './lib/theme'
import './styles.css'

// Re-apply the stored theme on the SPA boot path. The inline bootstrap in
// index.html already set the attribute pre-paint (no flash); this keeps the
// applied attribute consistent with theme.ts's view of the preference and
// covers any path where the inline script did not run (Plan 0033 phase 1).
applyTheme(getStoredTheme())

const root = document.getElementById('root')
if (!root) throw new Error('missing #root element')

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
