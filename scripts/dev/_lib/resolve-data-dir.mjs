/**
 * Resolve the canonical data dir by asking the Python config module directly.
 *
 * Per ADR-0020 the data dir is a contract — both halves of the system compute
 * the same path from the same algorithm. This helper runs the Python resolver
 * once at `pnpm dev:all` boot and returns the path as a forward-slash string
 * the rest of the chain (wait-on, the .mcp.json writer, log lines) can embed
 * in shell command lines without per-platform escaping.
 *
 * Fails loudly when `uv run` cannot reach the project venv. Silent fallback to
 * a guessed path would produce a confusing wait-on timeout three steps later.
 */
import { spawnSync } from "node:child_process";

const PYTHON_ONELINER =
  "from market_analyser.config import default_app_data_dir; print(default_app_data_dir())";

export function resolveDataDir() {
  const result = spawnSync(
    "uv",
    ["run", "python", "-c", PYTHON_ONELINER],
    { encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"] },
  );
  if (result.error) {
    throw new Error(
      `failed to invoke \`uv run python\` to resolve data dir: ${result.error.message}\n` +
        "is the project venv set up? Run `uv sync` in the repo root.",
    );
  }
  if (result.status !== 0) {
    throw new Error(
      `\`uv run python -c\` exited ${result.status} resolving data dir:\n${result.stderr}`,
    );
  }
  const resolved = result.stdout.trim();
  if (!resolved) {
    throw new Error("Python data-dir resolver returned empty stdout");
  }
  return resolved.replace(/\\/g, "/");
}
