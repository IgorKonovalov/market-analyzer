/**
 * BrowserWindow factory + double-CSP installation per ADR-0008.
 *
 * Security defaults: contextIsolation, no nodeIntegration, sandboxed renderer.
 * CSP is set twice — as a <meta> in index.html AND as an HTTP response header
 * here — so Vite's dev-server CSP override is stripped before ours is applied.
 */
import { BrowserWindow, session } from 'electron'
import { join } from 'node:path'

const PROD_CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: https:",
  "connect-src 'self' http://127.0.0.1:*",
].join('; ')

const DEV_CSP = [
  "default-src 'self' http://localhost:5173",
  "script-src 'self' 'unsafe-inline' http://localhost:5173",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: https:",
  "connect-src 'self' http://127.0.0.1:* http://localhost:5173 ws://localhost:5173",
].join('; ')

export function installCsp(isDev: boolean): void {
  const csp = isDev ? DEV_CSP : PROD_CSP
  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    const headers = { ...details.responseHeaders }
    for (const key of Object.keys(headers)) {
      if (key.toLowerCase() === 'content-security-policy') delete headers[key]
    }
    headers['Content-Security-Policy'] = [csp]
    callback({ responseHeaders: headers })
  })
}

export interface CreateWindowOptions {
  preloadPath: string
  rendererUrl?: string
  rendererFile: string
}

export function createWindow(opts: CreateWindowOptions): BrowserWindow {
  const window = new BrowserWindow({
    width: 1280,
    height: 800,
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: opts.preloadPath,
      webSecurity: true,
    },
  })

  window.once('ready-to-show', () => window.show())

  window.webContents.setWindowOpenHandler(({ url }) => {
    const allowed =
      (opts.rendererUrl !== undefined && url.startsWith(opts.rendererUrl)) ||
      url.startsWith('file://')
    return allowed ? { action: 'allow' } : { action: 'deny' }
  })

  window.webContents.on('will-navigate', (event, url) => {
    const sameOrigin =
      (opts.rendererUrl !== undefined && url.startsWith(opts.rendererUrl)) ||
      url.startsWith(`file://${opts.rendererFile}`)
    if (!sameOrigin) event.preventDefault()
  })

  if (opts.rendererUrl !== undefined) {
    void window.loadURL(opts.rendererUrl)
  } else {
    void window.loadFile(opts.rendererFile)
  }

  return window
}

export function showFatalWindow(message: string): BrowserWindow {
  const window = new BrowserWindow({
    width: 600,
    height: 320,
    title: 'market-analyser — fatal error',
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  })
  const html = `data:text/html;charset=utf-8,${encodeURIComponent(
    `<!doctype html><html><body style="font-family:system-ui,sans-serif;padding:24px;">
      <h2>market-analyser stopped</h2>
      <p>${escapeHtml(message)}</p>
      <p style="color:#666;font-size:12px;">
        See the application data directory for logs; restart the app to retry.
      </p>
    </body></html>`,
  )}`
  void window.loadURL(html)
  window.once('ready-to-show', () => window.show())
  return window
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

export function getRendererPaths(): { rendererFile: string; preloadPath: string } {
  return {
    rendererFile: join(__dirname, '..', 'renderer', 'index.html'),
    preloadPath: join(__dirname, '..', 'preload', 'index.cjs'),
  }
}
