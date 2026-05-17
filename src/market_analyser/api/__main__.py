"""Uvicorn entrypoint for the market-analyser sidecar.

Usage:
    python -m market_analyser.api --port=<n> --secret=<hex>

Per ADR-0002: binds 127.0.0.1 only; if `--port=0` is passed, the OS picks
an ephemeral port and we print `PORT=<n>` to stdout on a single line so the
Electron main process can read it back and forward to the renderer.
"""

from __future__ import annotations

import argparse
import asyncio
import socket
import sys

import uvicorn

from market_analyser.api.app import create_app

HOST = "127.0.0.1"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="market_analyser.api")
    parser.add_argument("--port", type=int, required=True, help="TCP port; 0 for OS-picked")
    parser.add_argument("--secret", type=str, required=True, help="bearer token for auth")
    return parser.parse_args(argv)


def _bind_socket(port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HOST, port))
    return sock


async def _serve(sock: socket.socket, secret: str) -> None:
    app = create_app(secret=secret)
    config = uvicorn.Config(app, log_level="info", access_log=False)
    server = uvicorn.Server(config)
    await server.serve(sockets=[sock])


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    sock = _bind_socket(args.port)
    actual_port = sock.getsockname()[1]
    print(f"PORT={actual_port}", flush=True)
    try:
        asyncio.run(_serve(sock, args.secret))
    finally:
        sock.close()


if __name__ == "__main__":
    main()
