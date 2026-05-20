"""Unit tests for the uvicorn entrypoint module.

Exercises argv parsing, socket binding (must bind 127.0.0.1 only), env-var
secret resolution (Plan 0004 phase 3 closed the argv-snooping risk by reading
MARKET_ANALYSER_SECRET from the environment), and the top-level `main()` flow
that prints `PORT=<n>` to stdout before handing off to uvicorn. uvicorn.Server
is faked so the tests don't actually serve.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import pytest

from market_analyser.api import __main__ as entry


def test_parse_args_parses_port() -> None:
    ns = entry._parse_args(["--port=42"])
    assert ns.port == 42


def test_parse_args_requires_port() -> None:
    with pytest.raises(SystemExit):
        entry._parse_args([])


def test_parse_args_no_longer_accepts_secret_flag() -> None:
    # The bearer secret moved to MARKET_ANALYSER_SECRET; passing --secret on
    # argv must be rejected so a misconfigured caller can't leak the token
    # back into the process listing.
    with pytest.raises(SystemExit):
        entry._parse_args(["--port=0", "--secret=leak"])


def test_read_secret_from_env_returns_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(entry.SECRET_ENV_VAR, "hexstring")
    assert entry._read_secret_from_env() == "hexstring"


def test_read_secret_from_env_refuses_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(entry.SECRET_ENV_VAR, "")
    with pytest.raises(SystemExit):
        entry._read_secret_from_env()


def test_read_secret_from_env_refuses_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(entry.SECRET_ENV_VAR, raising=False)
    with pytest.raises(SystemExit):
        entry._read_secret_from_env()


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
    tmp_path: Any,
) -> None:
    captured: dict[str, Any] = {}

    class FakeServer:
        def __init__(self, config: object) -> None:
            captured["config"] = config

        async def serve(self, sockets: list[socket.socket]) -> None:
            captured["sockets"] = sockets

    monkeypatch.setattr("market_analyser.api.__main__.uvicorn.Server", FakeServer)

    # Make the default db path land in tmp_path so the test doesn't touch %APPDATA%.
    monkeypatch.setattr(
        "market_analyser.config.default_app_data_dir",
        lambda: tmp_path,
    )

    sock = entry._bind_socket(0)
    try:
        asyncio.run(entry._serve(sock, "secret", None, None))
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
    monkeypatch.setenv(entry.SECRET_ENV_VAR, "test-secret")

    entry.main(["--port=0"])

    out = capsys.readouterr().out.strip()
    assert out.startswith("PORT=")
    port_str = out.removeprefix("PORT=")
    assert port_str.isdigit() and int(port_str) > 0
    assert ran.get("yes") is True


def test_main_refuses_to_start_without_env_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(entry.SECRET_ENV_VAR, raising=False)
    with pytest.raises(SystemExit):
        entry.main(["--port=0"])


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
    monkeypatch.setenv(entry.SECRET_ENV_VAR, "test-secret")

    with pytest.raises(RuntimeError, match="boom"):
        entry.main(["--port=0"])

    assert sockets_seen, "bind was not called"
    sock = sockets_seen[0]
    with pytest.raises(OSError):
        sock.getsockname()
