"""Unit tests for the uvicorn entrypoint module.

Exercises argv parsing, socket binding (must bind 127.0.0.1 only), and the
top-level `main()` flow that prints `PORT=<n>` to stdout before handing off
to uvicorn. uvicorn.Server is faked so the tests don't actually serve.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import pytest

from market_analyser.api import __main__ as entry


def test_parse_args_parses_port_and_secret() -> None:
    ns = entry._parse_args(["--port=42", "--secret=hex"])
    assert ns.port == 42
    assert ns.secret == "hex"


def test_parse_args_requires_port() -> None:
    with pytest.raises(SystemExit):
        entry._parse_args(["--secret=hex"])


def test_parse_args_requires_secret() -> None:
    with pytest.raises(SystemExit):
        entry._parse_args(["--port=0"])


def test_bind_socket_uses_loopback_and_ephemeral_port() -> None:
    sock = entry._bind_socket(0)
    try:
        host, port = sock.getsockname()
        assert host == "127.0.0.1"
        assert port > 0
    finally:
        sock.close()


def test_serve_constructs_app_and_delegates_to_uvicorn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeServer:
        def __init__(self, config: object) -> None:
            captured["config"] = config

        async def serve(self, sockets: list[socket.socket]) -> None:
            captured["sockets"] = sockets

    monkeypatch.setattr("market_analyser.api.__main__.uvicorn.Server", FakeServer)

    sock = entry._bind_socket(0)
    try:
        asyncio.run(entry._serve(sock, "secret"))
    finally:
        sock.close()

    assert captured["sockets"] == [sock]
    assert "config" in captured


def test_main_prints_port_line_and_invokes_asyncio_run(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran: dict[str, bool] = {}

    def fake_run(coro: Any) -> None:
        coro.close()
        ran["yes"] = True

    monkeypatch.setattr("market_analyser.api.__main__.asyncio.run", fake_run)

    entry.main(["--port=0", "--secret=test"])

    out = capsys.readouterr().out.strip()
    assert out.startswith("PORT=")
    port_str = out.removeprefix("PORT=")
    assert port_str.isdigit() and int(port_str) > 0
    assert ran.get("yes") is True


def test_main_closes_socket_even_when_serve_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sockets_seen: list[socket.socket] = []

    real_bind = entry._bind_socket

    def tracking_bind(port: int) -> socket.socket:
        sock = real_bind(port)
        sockets_seen.append(sock)
        return sock

    monkeypatch.setattr("market_analyser.api.__main__._bind_socket", tracking_bind)

    def raising_run(coro: Any) -> None:
        coro.close()
        raise RuntimeError("boom")

    monkeypatch.setattr("market_analyser.api.__main__.asyncio.run", raising_run)

    with pytest.raises(RuntimeError, match="boom"):
        entry.main(["--port=0", "--secret=test"])

    assert sockets_seen, "bind was not called"
    sock = sockets_seen[0]
    with pytest.raises(OSError):
        sock.getsockname()
