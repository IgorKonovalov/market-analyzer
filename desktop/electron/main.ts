/**
 * Electron main entry point.
 *
 * Lifecycle:
 *   1. `app.whenReady()` →
 *   2. Spawn the Python sidecar (free port + 32-byte hex secret).
 *   3. Wait for /healthz to return 200 (max 10s).
 *   4. Install double-CSP headers.
 *   5. Register IPC handlers.
 *   6. Open the renderer window (loads Vite dev server in dev, file in prod).
 *   On `before-quit`: SIGTERM the sidecar, wait up to 3s, SIGKILL otherwise.
 */
import { app, BrowserWindow } from 'electron'
import { createWindow, getRendererPaths, installCsp, showFatalWindow } from './window'
import { registerIpcHandlers, cleanupServices } from './ipc'
import { SidecarSupervisor } from './sidecar'

const isDev = !app.isPackaged
const rendererUrl = process.env.ELECTRON_RENDERER_URL
const isE2E = process.env.MARKET_ANALYSER_E2E === '1'

if (process.platform === 'win32') {
  app.setAppUserModelId('io.marketanalyser.desktop')
}

const supervisor = new SidecarSupervisor()

if (isE2E) {
  // E2E helper: exposes the supervisor on globalThis so Playwright's
  // `app.evaluate(...)` can read the sidecar PID and target the python child
  // directly (rather than the Electron main, whose PID is meaningless to the
  // restart-once policy). Gated by env to keep production builds clean.
  ;(globalThis as { __sidecarSupervisor?: SidecarSupervisor }).__sidecarSupervisor = supervisor
}

app.whenReady().then(async () => {
  try {
    const info = await supervisor.start()
    // CSP install MUST follow supervisor.start so connect-src can be pinned to
    // the actual sidecar port rather than the broader http://127.0.0.1:*.
    installCsp(isDev, info.port)
    const paths = getRendererPaths()
    registerIpcHandlers({ supervisor, info })
    const window = createWindow({
      preloadPath: paths.preloadPath,
      rendererUrl,
      rendererFile: paths.rendererFile,
    })

    supervisor.onStatus((status) => {
      if (status.kind === 'fatal') {
        if (!window.isDestroyed()) window.close()
        showFatalWindow(status.message ?? 'sidecar fatal error')
      } else if (!window.isDestroyed()) {
        window.webContents.send('sidecar:status', status)
      }
    })
  } catch (err) {
    showFatalWindow(`startup failed: ${(err as Error).message}`)
  }
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    void app.whenReady().then(() => {
      const paths = getRendererPaths()
      createWindow({
        preloadPath: paths.preloadPath,
        rendererUrl,
        rendererFile: paths.rendererFile,
      })
    })
  }
})

app.on('before-quit', async (event) => {
  if (supervisor.getInfo() === null) return
  event.preventDefault()
  cleanupServices()
  await supervisor.stop()
  app.exit(0)
})
