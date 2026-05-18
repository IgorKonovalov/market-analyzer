/**
 * Playwright globalSetup: builds desktop bundles (main + preload + renderer)
 * so `_electron.launch` can spawn `dist/main/index.cjs` and the renderer
 * resolves `dist/renderer/index.html` over `file://`.
 *
 * Do not set `ELECTRON_RENDERER_URL` here — its absence is the signal that
 * selects the `loadFile` branch in `electron/main.ts` (plan 0001 phase 4.1,
 * ADR-0008 Notes).
 *
 * `MARKET_ANALYSER_E2E=1` opts the main process into exposing the sidecar
 * supervisor on globalThis so specs can read the python child PID via
 * `app.evaluate(...)`. Gated by env so production builds never see the leak.
 */
import { spawnSync } from "node:child_process";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const desktopDir = dirname(__dirname);

export default async function globalSetup() {
  const result = spawnSync("pnpm", ["build"], {
    cwd: desktopDir,
    stdio: "inherit",
    shell: true,
  });
  if (result.status !== 0) {
    throw new Error(
      `playwright globalSetup: pnpm build failed (exit ${result.status ?? "?"})`,
    );
  }
  process.env.MARKET_ANALYSER_E2E = "1";
}
