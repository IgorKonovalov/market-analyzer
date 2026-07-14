"""Unit tests for the uvicorn entrypoint module.

Under ADR-0016 the renderer bearer is generated fresh on every sidecar boot
when `MARKET_ANALYSER_SECRET` is unset (the env-var path stays as a fallback
for Electron cold-spawn). These tests cover argv parsing, socket binding (must
bind 127.0.0.1 only), secret resolution, and the top-level `main()` flow that
writes the lockfile + prints `PORT=<n>` before handing off to uvicorn.
uvicorn.Server is faked so the tests don't actually serve.
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
    assert ns.command is None


def test_parse_args_recognises_stop_subcommand() -> None:
    ns = entry._parse_args(["stop"])
    assert ns.command == "stop"


def test_main_refuses_serve_without_port() -> None:
    """`--port` is required for the serve path; argparse leaves it None and
    main() raises SystemExit rather than picking an arbitrary default."""
    with pytest.raises(SystemExit):
        entry.main([])


def test_parse_args_no_longer_accepts_secret_flag() -> None:
    # The bearer secret moved to MARKET_ANALYSER_SECRET; passing --secret on
    # argv must be rejected so a misconfigured caller can't leak the token
    # back into the process listing.
    with pytest.raises(SystemExit):
        entry._parse_args(["--port=0", "--secret=leak"])


def test_resolve_secret_returns_env_value_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(entry.SECRET_ENV_VAR, "explicit-secret")
    assert entry._resolve_secret() == "explicit-secret"


def test_resolve_secret_generates_fresh_when_env_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(entry.SECRET_ENV_VAR, raising=False)
    s1 = entry._resolve_secret()
    s2 = entry._resolve_secret()
    assert len(s1) == 64 and len(s2) == 64
    assert s1 != s2  # fresh per call


def test_resolve_secret_generates_fresh_when_env_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(entry.SECRET_ENV_VAR, "")
    s = entry._resolve_secret()
    assert len(s) == 64


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
    monkeypatch.setattr(
        "market_analyser.config.default_app_data_dir",
        lambda: tmp_path,
    )

    sock = entry._bind_socket(0)
    try:
        asyncio.run(entry._serve(sock, "secret", None, None, tmp_path / "sidecar.lock"))
    finally:
        sock.close()

    assert captured["sockets"] == [sock]
    assert "config" in captured


def test_serve_removes_stale_agent_mode_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """ADR-0101 removed agent mode; a leftover `agent_mode.json` pre-seeded in
    the data dir must be gone after startup."""

    class FakeServer:
        def __init__(self, config: object) -> None:
            pass

        async def serve(self, sockets: list[socket.socket]) -> None:
            pass

    monkeypatch.setattr("market_analyser.api.__main__.uvicorn.Server", FakeServer)
    monkeypatch.setattr("market_analyser.api.__main__.default_app_data_dir", lambda: tmp_path)
    monkeypatch.setattr("market_analyser.config.default_app_data_dir", lambda: tmp_path)
    stale = tmp_path / "agent_mode.json"
    stale.write_text('{"enabled": true}', encoding="utf-8")

    sock = entry._bind_socket(0)
    try:
        asyncio.run(entry._serve(sock, "secret", None, None, tmp_path / "sidecar.lock"))
    finally:
        sock.close()

    assert not stale.exists()


def test_remove_stale_agent_mode_file_noop_when_absent(tmp_path: Any) -> None:
    entry._remove_stale_agent_mode_file(tmp_path)  # must not raise


def test_remove_stale_agent_mode_file_ignores_errors(tmp_path: Any) -> None:
    """Best-effort: an undeletable path (here a non-empty directory occupying
    the filename) must be ignored, never crash startup."""
    blocker = tmp_path / "agent_mode.json"
    blocker.mkdir()
    (blocker / "occupant.txt").write_text("x", encoding="utf-8")

    entry._remove_stale_agent_mode_file(tmp_path)  # must not raise

    assert blocker.exists()


def test_main_prints_port_line_and_writes_lockfile(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """`main()` writes the lockfile + emits `PORT=<n>` before invoking uvicorn,
    and removes the lockfile in the `finally` block."""
    ran: dict[str, bool] = {}

    def fake_run(coro: Any) -> None:
        coro.close()
        ran["yes"] = True

    monkeypatch.setattr("market_analyser.api.__main__.asyncio.run", fake_run)
    monkeypatch.delenv(entry.SECRET_ENV_VAR, raising=False)
    lockfile = tmp_path / "sidecar.lock"

    entry.main(["--port=0", f"--lockfile={lockfile}"])

    out = capsys.readouterr().out.strip()
    assert out.startswith("PORT=")
    port_str = out.removeprefix("PORT=")
    assert port_str.isdigit() and int(port_str) > 0
    assert ran.get("yes") is True
    # `finally` block removed the lockfile on serve return.
    assert not lockfile.exists()


def test_main_removes_lockfile_when_serve_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    """A crash inside the asyncio loop must still run the `finally` block."""

    def raising_run(coro: Any) -> None:
        coro.close()
        raise RuntimeError("boom")

    monkeypatch.setattr("market_analyser.api.__main__.asyncio.run", raising_run)
    monkeypatch.setenv(entry.SECRET_ENV_VAR, "test-secret")
    lockfile = tmp_path / "sidecar.lock"

    with pytest.raises(RuntimeError, match="boom"):
        entry.main(["--port=0", f"--lockfile={lockfile}"])

    # Even on crash, the finally block removed it.
    assert not lockfile.exists()


def test_main_refuses_to_start_when_lockfile_owned_by_live_pid(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Any,
) -> None:
    """`_probe_and_prepare_lockfile` exits non-zero with the existing PID in
    stderr when the lockfile's owner is still alive."""
    import os

    import psutil

    from market_analyser.api.lockfile import LockfileRecord, write_lockfile

    pid = os.getpid()
    live_record = LockfileRecord(
        pid=pid,
        port=12345,
        renderer_secret="a" * 64,
        started_at=__import__("datetime").datetime.now(tz=__import__("datetime").UTC),
        process_create_time=psutil.Process(pid).create_time(),
        sidecar_version="0.0.0-test",
    )
    lockfile = tmp_path / "sidecar.lock"
    write_lockfile(lockfile, live_record)

    monkeypatch.delenv(entry.SECRET_ENV_VAR, raising=False)
    with pytest.raises(SystemExit) as excinfo:
        entry.main(["--port=0", f"--lockfile={lockfile}"])

    assert excinfo.value.code == 1
    stderr = capsys.readouterr().err
    assert "sidecar already running at PID" in stderr
    assert str(pid) in stderr
    # The existing lockfile is untouched.
    from market_analyser.api.lockfile import read_lockfile

    still_there = read_lockfile(lockfile)
    assert still_there is not None
    assert still_there.renderer_secret == "a" * 64


def test_main_takes_over_stale_lockfile(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Any,
) -> None:
    """A lockfile whose `process_create_time` doesn't match the live PID's
    `create_time()` is stale — main() warns + proceeds."""
    import os

    import psutil

    from market_analyser.api.lockfile import LockfileRecord, write_lockfile

    pid = os.getpid()
    # Force a create_time mismatch beyond CREATE_TIME_TOLERANCE_S (5s).
    bogus_create_time = psutil.Process(pid).create_time() + 60.0
    stale_record = LockfileRecord(
        pid=pid,
        port=12345,
        renderer_secret="c" * 64,
        started_at=__import__("datetime").datetime.now(tz=__import__("datetime").UTC),
        process_create_time=bogus_create_time,
        sidecar_version="0.0.0-test",
    )
    lockfile = tmp_path / "sidecar.lock"
    write_lockfile(lockfile, stale_record)

    def fake_run(coro: Any) -> None:
        coro.close()

    monkeypatch.setattr("market_analyser.api.__main__.asyncio.run", fake_run)
    monkeypatch.delenv(entry.SECRET_ENV_VAR, raising=False)

    entry.main(["--port=0", f"--lockfile={lockfile}"])

    stderr = capsys.readouterr().err
    assert "stale lockfile" in stderr
    assert str(pid) in stderr
