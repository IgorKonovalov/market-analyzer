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
import { enforceSingleInstance } from './single-instance'
import { resolveSharedDataDir } from '../shared/data-dir'

// ADR-0020: anchor `app.getName()` to the contract name so OS-level surfaces
// (window title, taskbar grouping, recent-files) read correctly. The data dir
// does NOT depend on this — `resolveSharedDataDir()` computes the path from
// the literal `APP_DIRNAME` directly — but Electron uses the name in places
// that have no contract-shaped substitute. Must run before any other Electron
// API touches the name to avoid `userData` getting cached at the wrong path
// by a third-party plugin.
app.setName('market-analyser')

const isDev = !app.isPackaged
const rendererUrl = process.env.ELECTRON_RENDERER_URL
const isE2E = process.env.MARKET_ANALYSER_E2E === '1'

if (process.platform === 'win32') {
  app.setAppUserModelId('io.marketanalyser.desktop')
}

// Track the main window so `second-instance` can focus it (Plan 0014).
let mainWindow: BrowserWindow | null = null

// Single-instance: agent mode is sidecar-resident state, so only one viewer
// may own it. A second launch focuses the existing window and quits. Must run
// before `whenReady` so we never spawn/attach for a duplicate instance.
const isPrimaryInstance = enforceSingleInstance(app, () => mainWindow)

const supervisor = new SidecarSupervisor(resolveSharedDataDir())

if (isE2E) {
  // E2E helper: exposes the supervisor on globalThis so Playwright's
  // `app.evaluate(...)` can read the sidecar PID and target the python child
  // directly (rather than the Electron main, whose PID is meaningless to the
  // restart-once policy). Gated by env to keep production builds clean.
  ;(globalThis as { __sidecarSupervisor?: SidecarSupervisor }).__sidecarSupervisor = supervisor
}

// A second instance has already quit via enforceSingleInstance; do not boot.
if (isPrimaryInstance) {
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
      mainWindow = window

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
}

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    void app.whenReady().then(() => {
      const paths = getRendererPaths()
      mainWindow = createWindow({
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
