"""Uvicorn entrypoint for the market-analyser sidecar.

Usage:
    MARKET_ANALYSER_SECRET=<hex> python -m market_analyser.api --port=<n> [--config=<path>]

Per ADR-0002: binds 127.0.0.1 only; if `--port=0` is passed, the OS picks
an ephemeral port and we print `PORT=<n>` to stdout on a single line so the
Electron main process can read it back and forward to the renderer.

The bearer secret is read from the `MARKET_ANALYSER_SECRET` env var rather
than argv so it does not appear in process listings (Plan 0004 phase 3,
closing the Open Question in Plan 0001 noted in ADR-0002's Notes).

Phase 3 of Plan 0001: builds the SQLite engine from `AppConfig`, runs Alembic
migrations before serving the first request, and exposes a cache-aware provider.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import socket
import sys
from pathlib import Path

import uvicorn

from market_analyser.api.app import create_app
from market_analyser.api.mcp_secret import load_or_generate_mcp_secret
from market_analyser.config import default_app_data_dir, load_config
from market_analyser.persistence.engine import make_engine

MCP_SECRET_FILENAME = "mcp-secret.json"

HOST = "127.0.0.1"
SECRET_ENV_VAR = "MARKET_ANALYSER_SECRET"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="market_analyser.api")
    parser.add_argument("--port", type=int, required=True, help="TCP port; 0 for OS-picked")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="optional path to config.json; defaults to AppConfig defaults",
    )
    return parser.parse_args(argv)


def _read_secret_from_env() -> str:
    secret = os.environ.get(SECRET_ENV_VAR, "")
    if not secret:
        raise SystemExit(
            f"{SECRET_ENV_VAR} must be set to a non-empty bearer token; refusing to start.",
        )
    return secret


def _bind_socket(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, port))
    return sock


async def _serve(sock: socket.socket, secret: str, config_path: Path | None) -> None:
    config = load_config(config_path)
    engine = make_engine(config.db_path)
    mcp_secret = load_or_generate_mcp_secret(default_app_data_dir() / MCP_SECRET_FILENAME)
    app = create_app(secret=secret, mcp_secret=mcp_secret, engine=engine)
    uvicorn_config = uvicorn.Config(app, log_level="info", access_log=False)
    server = uvicorn.Server(uvicorn_config)
    await server.serve(sockets=[sock])


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    secret = _read_secret_from_env()
    sock = _bind_socket(args.port)
    actual_port = sock.getsockname()[1]
    print(f"PORT={actual_port}", flush=True)
    try:
        asyncio.run(_serve(sock, secret, args.config))
    finally:
        sock.close()


if __name__ == "__main__":
    main()
