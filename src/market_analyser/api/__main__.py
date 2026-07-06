"""Uvicorn entrypoint for the market-analyser sidecar.

Usage:
    python -m market_analyser.api --port=<n> [--config=<path>]
    python -m market_analyser.api stop

Per ADR-0002: binds 127.0.0.1 only; if `--port=0` is passed, the OS picks
an ephemeral port and we print `PORT=<n>` to stdout on a single line so the
Electron main process can read it back.

Under ADR-0016 (standalone sidecar mode):

- The renderer bearer secret is **generated fresh on every sidecar boot** if
  `MARKET_ANALYSER_SECRET` is not set. The env-var path is retained as a
  fallback for cold-spawn from Electron, but the lockfile is the source of
  truth — Electron reads the bearer from `sidecar.lock` after boot regardless
  of whether it spawned the sidecar or attached to a running one.
- A lockfile is written atomically at boot and removed on clean shutdown
  (SIGTERM / SIGINT / normal exit). Single-instance is enforced via a PID +
  `process_create_time` cross-check at boot — a stale lockfile (pid dead, or
  pid alive but create_time mismatched) is taken over with a one-line warning.
- The `stop` subcommand reads the lockfile, cross-checks the owner's create
  time, then sends SIGTERM. Refuses if the lockfile is stale or owned by an
  unrelated process.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import re
import secrets
import socket
import sys
import urllib.error
import urllib.request
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from market_analyser import __version__
from market_analyser.api.app import create_app
from market_analyser.api.lockfile import (
    DEFAULT_LOCKFILE_NAME,
    build_self_record,
    is_owner_alive,
    read_lockfile,
    remove_lockfile,
    write_lockfile,
)
from market_analyser.api.mcp_secret import load_or_generate_mcp_secret
from market_analyser.api.ui_events.agent_mode import AGENT_MODE_FILENAME
from market_analyser.config import default_app_data_dir, load_config
from market_analyser.persistence.engine import make_engine
from market_analyser.persistence.secrets import SECRETS_FILENAME, SecretsStore

MCP_SECRET_FILENAME = "mcp-secret.json"

HOST = "127.0.0.1"
SECRET_ENV_VAR = "MARKET_ANALYSER_SECRET"
SECRET_BYTES = 32  # → 64 hex chars

_DEV_ORIGIN_RE = re.compile(r"^http://(localhost|127\.0\.0\.1):\d+$")

# Repo-root .env, resolved from this file's location (src/market_analyser/api/
# __main__.py → parents[3] is the repo root). Loaded explicitly, never via a CWD
# walk, so a stray .env in some working directory can't be picked up.
_REPO_ROOT_DOTENV = Path(__file__).resolve().parents[3] / ".env"


def _load_repo_dotenv(dotenv_path: Path = _REPO_ROOT_DOTENV) -> None:
    """Populate `MARKET_ANALYSER_*` env overrides from a repo-root `.env` in dev.

    Loads the developer's gitignored repo-root `.env` (if present) into the
    process environment so keys like `MARKET_ANALYSER_ZERION_API_KEY` take effect
    without a manual export. `override=False` means a real environment variable
    always wins, and a missing file is a silent no-op — so packaged builds, which
    ship no `.env` next to the bundled source, load nothing. Canonical secret
    storage remains `secrets.json` (ADR-0038); this only feeds the env-override
    layer that `SecretsStore` already reads.
    """
    load_dotenv(dotenv_path=dotenv_path, override=False)


def _dev_origin(raw: str) -> str:
    if not _DEV_ORIGIN_RE.fullmatch(raw):
        raise argparse.ArgumentTypeError(
            f"--dev-origin {raw!r} is not a loopback http URL "
            f"(must match http://localhost:<port> or http://127.0.0.1:<port>)",
        )
    return raw


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="market_analyser.api")
    subparsers = parser.add_subparsers(dest="command")

    stop_parser = subparsers.add_parser(
        "stop",
        help="Stop a running standalone sidecar (reads sidecar.lock, sends SIGTERM).",
    )
    stop_parser.add_argument(
        "--lockfile",
        type=Path,
        default=None,
        help="optional lockfile path; defaults to <user-data>/sidecar.lock",
    )

    parser.add_argument("--port", type=int, default=None, help="TCP port; 0 for OS-picked")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="optional path to config.json; defaults to AppConfig defaults",
    )
    parser.add_argument(
        "--dev-origin",
        type=_dev_origin,
        default=None,
        help=(
            "Electron dev-mode renderer origin to allow via CORS (e.g. "
            "http://localhost:5173). Loopback-only; set by `pnpm dev`. "
            "Omitted in packaged builds."
        ),
    )
    parser.add_argument(
        "--lockfile",
        type=Path,
        default=None,
        help="optional lockfile path; defaults to <user-data>/sidecar.lock",
    )
    return parser.parse_args(argv)


def _resolve_secret() -> str:
    """Return the renderer bearer for this sidecar launch.

    Honour `MARKET_ANALYSER_SECRET` if set (cold-spawn from Electron's existing
    code path; ADR-0016 retains this as a fallback). Otherwise generate a fresh
    32-byte hex token — the standalone path. Either way, the value is what gets
    persisted into `sidecar.lock` for downstream readers (Electron attach,
    Settings page, MCP clients via `/settings/...`).
    """
    env_secret = os.environ.get(SECRET_ENV_VAR, "")
    if env_secret:
        return env_secret
    return secrets.token_hex(SECRET_BYTES)


def _bind_socket(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, port))
    return sock


def _default_lockfile_path() -> Path:
    return default_app_data_dir() / DEFAULT_LOCKFILE_NAME


def _probe_and_prepare_lockfile(lockfile_path: Path) -> None:
    """Refuse to start when a live sidecar already owns the lockfile.

    Stale lockfiles (pid dead, or pid alive but create_time mismatched) are
    taken over with a one-line stderr warning naming the prior PID.
    """
    existing = read_lockfile(lockfile_path)
    if existing is None:
        return
    if is_owner_alive(existing):
        sys.stderr.write(
            f"sidecar already running at PID {existing.pid}, port {existing.port}; stop it first\n",
        )
        sys.stderr.flush()
        raise SystemExit(1)
    sys.stderr.write(
        f"stale lockfile from prior PID {existing.pid} (no longer alive); taking over\n",
    )
    sys.stderr.flush()
    # Claim the lock by removing the prior owner's file now, so the subsequent
    # write is a create (rename onto an absent path) rather than a
    # replace-existing — the operation that hit ERROR_ACCESS_DENIED on Windows
    # when something briefly held the stale file open. A PermissionError here is
    # that same transient-handle race; tolerate it (write_lockfile's bounded
    # retry is the backstop) instead of crashing the takeover.
    with contextlib.suppress(PermissionError):
        remove_lockfile(lockfile_path)


async def _serve(
    sock: socket.socket,
    secret: str,
    config_path: Path | None,
    dev_origin: str | None,
    lockfile_path: Path,
) -> None:
    config = load_config(config_path)
    engine = make_engine(config.db_path)
    mcp_secret_path = default_app_data_dir() / MCP_SECRET_FILENAME
    mcp_secret = load_or_generate_mcp_secret(mcp_secret_path)
    runs_dir = default_app_data_dir() / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    agent_mode_path = default_app_data_dir() / AGENT_MODE_FILENAME
    secrets_store = SecretsStore(default_app_data_dir() / SECRETS_FILENAME)
    app = create_app(
        secret=secret,
        mcp_secret=mcp_secret,
        mcp_secret_path=mcp_secret_path,
        secrets_store=secrets_store,
        engine=engine,
        runs_dir=runs_dir,
        dev_origin=dev_origin,
        agent_mode_path=agent_mode_path,
        # Metric-store self-warming (Plan 0061, ADR-0056): the config.json
        # off-switch and interval reach the lifespan job here.
        metric_accrual_enabled=config.metric_accrual_enabled,
        metric_accrual_interval_seconds=config.metric_accrual_interval_seconds,
        # Remove the lockfile during the app's lifespan shutdown so cleanup runs
        # before uvicorn re-raises a captured SIGTERM (ADR-0022). The `_run_serve`
        # `finally` below is an idempotent backstop for non-serve exit paths.
        on_shutdown=[lambda: remove_lockfile(lockfile_path)],
    )
    uvicorn_config = uvicorn.Config(app, log_level="info", access_log=False)
    server = uvicorn.Server(uvicorn_config)
    await server.serve(sockets=[sock])


def _run_serve(
    *,
    port: int,
    config_path: Path | None,
    dev_origin: str | None,
    lockfile_path: Path,
) -> None:
    """Serve until the OS signals shutdown.

    The lockfile is removed by the app's lifespan shutdown hook (ADR-0022),
    which runs before uvicorn re-raises a captured SIGTERM. The `finally` here
    is an idempotent backstop for exit paths that never enter `serve()`.
    """
    _probe_and_prepare_lockfile(lockfile_path)
    secret = _resolve_secret()
    sock = _bind_socket(port)
    actual_port = sock.getsockname()[1]
    record = build_self_record(
        port=actual_port,
        renderer_secret=secret,
        sidecar_version=__version__,
    )
    write_lockfile(lockfile_path, record)
    # PORT line lands AFTER the lockfile is in place — anything that races on
    # the PORT line and immediately reads the lockfile will see a valid record.
    print(f"PORT={actual_port}", flush=True)
    try:
        asyncio.run(_serve(sock, secret, config_path, dev_origin, lockfile_path))
    finally:
        sock.close()
        remove_lockfile(lockfile_path)


def _run_stop(lockfile_path: Path) -> int:
    """Stop a running sidecar identified by the lockfile.

    Uses the sidecar's `POST /settings/stop` HTTP route rather than cross-
    process signalling. The route delivers `SIGINT`/`SIGTERM` in-process via
    `os.kill(os.getpid(), ...)` after writing the 200 response — the only
    portable way to trigger a graceful shutdown across POSIX and Windows
    (cross-process `os.kill` with `SIGINT` is a no-op on Windows for processes
    not attached to the caller's console).
    """
    record = read_lockfile(lockfile_path)
    if record is None:
        sys.stderr.write(f"no lockfile at {lockfile_path}; sidecar not running\n")
        return 1
    if not is_owner_alive(record):
        sys.stderr.write(
            f"lockfile points at PID {record.pid} but that process is gone; "
            "removing stale lockfile\n",
        )
        remove_lockfile(lockfile_path)
        return 1
    url = f"http://{HOST}:{record.port}/settings/stop"
    req = urllib.request.Request(
        url,
        method="POST",
        headers={"Authorization": f"Bearer {record.renderer_secret}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            if resp.status != 200:
                sys.stderr.write(
                    f"stop endpoint at {url} returned {resp.status}; "
                    "sidecar may still be running\n",
                )
                return 1
    except urllib.error.URLError as e:
        sys.stderr.write(f"failed to reach {url}: {e}\n")
        return 1
    return 0


def main(argv: list[str] | None = None) -> None:
    _load_repo_dotenv()
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    if args.command == "stop":
        lockfile_path = args.lockfile or _default_lockfile_path()
        raise SystemExit(_run_stop(lockfile_path))

    if args.port is None:
        raise SystemExit("--port is required (use 0 for OS-picked)")

    lockfile_path = args.lockfile or _default_lockfile_path()
    _run_serve(
        port=args.port,
        config_path=args.config,
        dev_origin=args.dev_origin,
        lockfile_path=lockfile_path,
    )


# Async loop signal-handler hookup: uvicorn installs its own SIGTERM/SIGINT
# handlers and, after a graceful shutdown, restores the original handler and
# RE-RAISES the captured signal (uvicorn 0.46 `capture_signals`). For SIGTERM
# that default disposition kills the process *inside* `server.serve()`, so the
# `_run_serve` `finally` never runs on SIGTERM. Lockfile removal therefore lives
# in the app's lifespan shutdown (runs before the re-raise) — see ADR-0022. The
# `finally` remains an idempotent backstop for non-serve exit paths.

if __name__ == "__main__":
    main()
