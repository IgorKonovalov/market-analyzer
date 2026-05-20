/**
 * Electron main entry point.
 *
 * Lifecycle (ADR-0016 — standalone sidecar mode):
 *   1. `app.whenReady()` →
 *   2. Resolve the shared data directory (Electron's userData) and attach to
 *      a running sidecar or spawn one. Lockfile is the source of truth.
 *   3. Wait for /healthz to return 200 (max 10s).
 *   4. Install double-CSP headers.
 *   5. Register IPC handlers.
 *   6. Open the renderer window (Vite in dev, file in prod).
 *   On `before-quit`: DETACH from the sidecar (no signal). The sidecar
 *     outlives the viewer so MCP clients can keep using it.
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

/**
 * Shared data directory between Electron and the Python sidecar (ADR-0016).
 *
 * Electron's `userData` defaults to `<appData>/<productName>`, which on Windows
 * and macOS aligns with Python's `default_app_data_dir()`. On Linux, Electron
 * defaults to `~/.config/...` while Python defaults to `~/.local/share/...` —
 * the asymmetry would split lockfile readers between two directories. We
 * resolve it by passing `MARKET_ANALYSER_DATA_DIR` to the sidecar so both
 * processes agree, and the lockfile is always at `<this dir>/sidecar.lock`.
 */
function resolveDataDir(): string {
  return app.getPath('userData')
}

const supervisor = new SidecarSupervisor(resolveDataDir())

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

    // The supervisor only emits `starting` / `ready` under ADR-0016. The
    // `crashed`/`restarted`/`fatal` kinds no longer fire (no crash supervision
    // in standalone mode); the IPC channel stays so the renderer's existing
    // readiness hook keeps working.
    supervisor.onStatus((status) => {
      if (!window.isDestroyed()) {
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

app.on('before-quit', () => {
  cleanupServices()
  // ADR-0016: the sidecar's lifecycle is decoupled from Electron's. We detach
  // (unref the spawned child if any) so Node doesn't keep the event loop alive
  // waiting on it, but we do NOT signal the sidecar.
  supervisor.detach()
})
