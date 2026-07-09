import React from 'react'
import ReactDOM from 'react-dom/client'
import { App } from './App'
import { applyLocale, getStoredLocale } from './lib/i18n'
import { applyTheme, getStoredTheme } from './lib/theme'
import './styles.css'

// Re-apply the stored theme on the SPA boot path. The inline bootstrap in
// index.html already set the attribute pre-paint (no flash); this keeps the
// applied attribute consistent with theme.ts's view of the preference and
// covers any path where the inline script did not run (Plan 0033 phase 1).
applyTheme(getStoredTheme())

// Apply the stored locale (sets <html lang>). Unlike the theme, the locale
// needs no pre-paint bootstrap — text is React-rendered, not CSS-driven, so
// there is no flash to prevent (Plan 0069 phase 1).
applyLocale(getStoredLocale())

const root = document.getElementById('root')
if (!root) throw new Error('missing #root element')

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
