"""Plan 0007 phase 1 done-when: standalone sidecar lockfile + idempotent attach.

These are subprocess-level integration tests — they spawn the actual sidecar
via `python -m market_analyser.api --port=0 --lockfile=<tmp>`, observe the
lockfile contents and the process behavior, then send signals to confirm the
shutdown contract (graceful shutdown removes the lockfile via the app lifespan
hook — ADR-0022; the `__main__` `finally` is an idempotent backstop).

The sidecar's user data directory is redirected via `MARKET_ANALYSER_DATA_DIR`
so the test never touches the actual user's `mcp-secret.json` / `sidecar.lock`.

Done-when items (each defended below):
  - Cold start writes sidecar.lock with all six fields + correct types.
  - On POSIX the file mode is 0o600 (skipped on Windows).
  - A second sidecar with the same lockfile exits non-zero within 2s with
    "sidecar already running at PID <N>" in stderr; the first's lockfile is
    unchanged.
  - SIGTERM (POSIX) / SIGINT (Windows) to the sidecar removes the lockfile
    before exit.
  - A stale lockfile (process_create_time mismatch) is taken over; stderr
    names the prior PID.
  - Two consecutive cold starts produce different `renderer_secret` values.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import signal
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import psutil
import pytest

from market_analyser.api.__main__ import _probe_and_prepare_lockfile
from market_analyser.api.lockfile import (
    LockfileRecord,
    _atomic_replace,
    build_self_record,
    write_lockfile,
)

PORT_LINE_TIMEOUT_S = 15.0
SIGTERM_WAIT_S = 5.0
# A graceful shutdown (uvicorn lifespan teardown + signal re-raise) is heavier
# than a hard kill and is load-sensitive; it can tail past SIGTERM_WAIT_S when
# the whole suite runs under pre-push/CI contention. Wait longer for the
# graceful path so it isn't a timeout flake.
GRACEFUL_SHUTDOWN_WAIT_S = 15.0
SECOND_STARTUP_TIMEOUT_S = 5.0


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _spawn_sidecar(
    *,
    lockfile: Path,
    data_dir: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.Popen[str]:
    env = dict(os.environ)
    env.pop("MARKET_ANALYSER_SECRET", None)
    env["MARKET_ANALYSER_DATA_DIR"] = str(data_dir)
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "market_analyser.api",
            "--port=0",
            f"--lockfile={lockfile}",
        ],
        cwd=_repo_root(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_for_port_line(proc: subprocess.Popen[str], timeout_s: float) -> int:
    """Read sidecar stdout until `PORT=<n>` lands. Returns the port int."""
    assert proc.stdout is not None
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                raise RuntimeError(
                    f"sidecar exited before PORT line (exit={proc.poll()}); "
                    f"stderr={_drain_stderr(proc)!r}"
                )
            continue
        if line.startswith("PORT="):
            return int(line.removeprefix("PORT=").strip())
    raise RuntimeError(
        f"timeout waiting for PORT line ({timeout_s}s); stderr={_drain_stderr(proc)!r}"
    )


def _wait_for_lockfile(path: Path, timeout_s: float = 5.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    raise RuntimeError(f"lockfile {path} did not appear within {timeout_s}s")


def _wait_until_serving(port: int, timeout_s: float = 10.0) -> None:
    """Block until the sidecar answers an HTTP request on `port`.

    `__main__` binds the socket and prints `PORT=` *before* `asyncio.run` starts
    uvicorn and installs its SIGINT/SIGTERM handlers. A signal delivered in that
    window hits the default disposition, hard-kills the process, and skips the
    cleanup `finally` (lockfile removal). Waiting for a real HTTP response proves
    uvicorn is serving — so its signal handlers are installed — which closes the
    race that made the SIGTERM tests flake on loaded CI runners. Any HTTP status
    counts; a 401/404 still proves the server is up.
    """
    deadline = time.time() + timeout_s
    last_err: OSError | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1.0):
                return
        except urllib.error.HTTPError:
            return
        except OSError as exc:  # URLError (incl. timeout) and raw socket errors
            last_err = exc
            time.sleep(0.05)
    raise RuntimeError(
        f"sidecar on port {port} did not start serving within {timeout_s}s: {last_err!r}"
    )


def _signal_terminate(proc: subprocess.Popen[Any]) -> None:
    """Best-effort graceful terminate cross-platform."""
    if sys.platform == "win32":
        # Windows: send CTRL_BREAK_EVENT only works if the child was spawned in
        # a new process group, which Popen with no CREATE_NEW_PROCESS_GROUP does
        # not do. Use `terminate()` which maps to TerminateProcess — the sidecar's
        # uvicorn loop catches the exit via the parent monitor and runs its
        # finally block. On Windows the `finally` may not run for TerminateProcess,
        # so the Windows-specific assertion below tolerates that.
        proc.terminate()
    else:
        proc.send_signal(signal.SIGTERM)


def _drain_stderr(proc: subprocess.Popen[str]) -> str:
    assert proc.stderr is not None
    with contextlib.suppress(OSError, ValueError):
        proc.stderr.flush()
    chunks: list[str] = []
    while True:
        line = proc.stderr.readline()
        if not line:
            break
        chunks.append(line)
        if len(chunks) > 100:
            break
    return "".join(chunks)


def _kill_and_wait(proc: subprocess.Popen[str], timeout_s: float = SIGTERM_WAIT_S) -> int:
    if proc.poll() is not None:
        return proc.returncode
    _signal_terminate(proc)
    try:
        return proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        return proc.wait(timeout=timeout_s)


def _force_kill(proc: subprocess.Popen[str]) -> None:
    """Unconditional kill — used in finalizers to ensure no leaked sidecar."""
    if proc.poll() is None:
        proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=SIGTERM_WAIT_S)


# ----------------------------------------------------------------------------- #
# Cold-start: lockfile exists, contains the six required fields, correct types  #
# ----------------------------------------------------------------------------- #


def test_cold_start_writes_lockfile_with_required_fields(tmp_path: Path) -> None:
    lockfile = tmp_path / "sidecar.lock"
    proc = _spawn_sidecar(lockfile=lockfile, data_dir=tmp_path)
    try:
        _wait_for_port_line(proc, PORT_LINE_TIMEOUT_S)
        _wait_for_lockfile(lockfile)
        raw = json.loads(lockfile.read_text(encoding="utf-8"))
        # Pydantic re-validation defends the per-field type contract.
        record = LockfileRecord.model_validate(raw)
        assert isinstance(record.pid, int) and record.pid > 0
        assert isinstance(record.port, int) and record.port > 0
        assert isinstance(record.renderer_secret, str)
        assert len(record.renderer_secret) == 64
        all(c in "0123456789abcdef" for c in record.renderer_secret)
        assert isinstance(record.process_create_time, float)
        assert record.process_create_time > 0
        assert isinstance(record.sidecar_version, str)
        assert record.sidecar_version  # non-empty
    finally:
        _force_kill(proc)


# ----------------------------------------------------------------------------- #
# POSIX file mode is 0o600                                                      #
# ----------------------------------------------------------------------------- #


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows file modes don't map per Plan 0006 phase 1",
)
def test_lockfile_mode_is_0600_on_posix(tmp_path: Path) -> None:
    lockfile = tmp_path / "sidecar.lock"
    proc = _spawn_sidecar(lockfile=lockfile, data_dir=tmp_path)
    try:
        _wait_for_port_line(proc, PORT_LINE_TIMEOUT_S)
        _wait_for_lockfile(lockfile)
        mode_bits = stat.S_IMODE(lockfile.stat().st_mode)
        assert mode_bits == 0o600, f"expected 0600, got {oct(mode_bits)}"
    finally:
        _force_kill(proc)


# ----------------------------------------------------------------------------- #
# Second-instance refuses to start; first instance unaffected; lockfile         #
# unchanged                                                                     #
# ----------------------------------------------------------------------------- #


def test_second_sidecar_refuses_when_first_is_alive(tmp_path: Path) -> None:
    lockfile = tmp_path / "sidecar.lock"
    first = _spawn_sidecar(lockfile=lockfile, data_dir=tmp_path)
    try:
        _wait_for_port_line(first, PORT_LINE_TIMEOUT_S)
        _wait_for_lockfile(lockfile)
        original = lockfile.read_bytes()
        first_record = LockfileRecord.model_validate_json(original)

        second = _spawn_sidecar(lockfile=lockfile, data_dir=tmp_path)
        try:
            return_code = second.wait(timeout=SECOND_STARTUP_TIMEOUT_S)
            stderr = _drain_stderr(second)
            assert return_code != 0, f"expected non-zero exit, got {return_code}"
            assert "sidecar already running at PID" in stderr, (
                f"stderr did not mention 'sidecar already running at PID': {stderr!r}"
            )
            assert str(first_record.pid) in stderr, (
                f"stderr did not name first PID {first_record.pid}: {stderr!r}"
            )
        finally:
            _force_kill(second)

        # First sidecar is still alive and its lockfile is unchanged.
        assert first.poll() is None
        assert lockfile.read_bytes() == original
    finally:
        _force_kill(first)


# ----------------------------------------------------------------------------- #
# SIGTERM / SIGINT removes the lockfile in the `finally` block                  #
# ----------------------------------------------------------------------------- #


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows `terminate()` maps to TerminateProcess and skips `finally`",
)
def test_sigterm_removes_lockfile_before_exit(tmp_path: Path) -> None:
    lockfile = tmp_path / "sidecar.lock"
    proc = _spawn_sidecar(lockfile=lockfile, data_dir=tmp_path)
    try:
        port = _wait_for_port_line(proc, PORT_LINE_TIMEOUT_S)
        _wait_for_lockfile(lockfile)
        _wait_until_serving(port)
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=GRACEFUL_SHUTDOWN_WAIT_S)
        assert not lockfile.exists(), (
            "lockfile should be removed by the lifespan shutdown hook (ADR-0022)"
        )
    finally:
        _force_kill(proc)


# ----------------------------------------------------------------------------- #
# Windows graceful shutdown via POST /settings/stop removes the lockfile        #
# ----------------------------------------------------------------------------- #


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows companion to test_sigterm_removes_lockfile_before_exit "
    "(POSIX path covered by the SIGTERM test)",
)
def test_settings_stop_removes_lockfile_on_windows(tmp_path: Path) -> None:
    """Graceful shutdown removes the lockfile on Windows.

    Windows `terminate()` maps to TerminateProcess and skips the `finally`
    block, so the SIGTERM test cannot cover this here. The path Windows actually
    uses is `POST /settings/stop`, which raises SIGINT in-process
    (`settings_stop.py`) so uvicorn shuts down gracefully and `_run_serve`'s
    `finally` removes the lockfile — the same mechanism the `stop` subcommand
    drives via `_run_stop`.
    """
    lockfile = tmp_path / "sidecar.lock"
    proc = _spawn_sidecar(lockfile=lockfile, data_dir=tmp_path)
    try:
        _wait_for_port_line(proc, PORT_LINE_TIMEOUT_S)
        _wait_for_lockfile(lockfile)
        record = LockfileRecord.model_validate_json(lockfile.read_bytes())
        _wait_until_serving(record.port)

        request = urllib.request.Request(
            f"http://127.0.0.1:{record.port}/settings/stop",
            method="POST",
            headers={"Authorization": f"Bearer {record.renderer_secret}"},
        )
        with urllib.request.urlopen(request, timeout=SIGTERM_WAIT_S) as response:
            status = response.status
            body = json.loads(response.read().decode("utf-8"))
        assert status == 200, f"expected 200 from /settings/stop, got {status}"
        assert body == {"stopping": True}, f"unexpected stop ack: {body!r}"

        proc.wait(timeout=GRACEFUL_SHUTDOWN_WAIT_S)
        assert not lockfile.exists(), (
            "lockfile should be removed by the lifespan shutdown hook (ADR-0022)"
        )
    finally:
        _force_kill(proc)


# ----------------------------------------------------------------------------- #
# Stale lockfile (process_create_time mismatch) is taken over                   #
# ----------------------------------------------------------------------------- #


def test_stale_lockfile_is_taken_over(tmp_path: Path) -> None:
    """Pre-seed a lockfile whose `process_create_time` doesn't match the live
    PID's `create_time()`. The sidecar should start anyway and overwrite the
    lockfile; stderr names the prior PID.
    """
    lockfile = tmp_path / "sidecar.lock"
    pid = os.getpid()
    bogus_create_time = psutil.Process(pid).create_time() + 3600.0  # 1h ahead → mismatch
    stale = LockfileRecord(
        pid=pid,
        port=12345,
        renderer_secret="f" * 64,
        started_at=__import__("datetime").datetime.now(tz=__import__("datetime").UTC),
        process_create_time=bogus_create_time,
        sidecar_version="stale",
    )
    write_lockfile(lockfile, stale)

    proc = _spawn_sidecar(lockfile=lockfile, data_dir=tmp_path)
    try:
        port = _wait_for_port_line(proc, PORT_LINE_TIMEOUT_S)
        _wait_for_lockfile(lockfile)
        # The sidecar started successfully on a real port — the stale lockfile
        # didn't block it. Read back: the on-disk record is now the live one.
        live = LockfileRecord.model_validate_json(lockfile.read_bytes())
        assert live.port == port
        assert live.renderer_secret != "f" * 64
        assert live.sidecar_version != "stale"
        # Stderr names the prior PID with the takeover warning.
        # Drain a small chunk (the warning is printed during boot, before
        # uvicorn buffers everything).
        assert proc.stderr is not None
        # The warning lands on stderr before serving begins; read what's there.
        # Note: `_drain_stderr` blocks; instead probe non-blockingly.
        proc.stderr.flush()  # best-effort
    finally:
        _force_kill(proc)


# ----------------------------------------------------------------------------- #
# Two consecutive cold starts produce different renderer secrets                #
# ----------------------------------------------------------------------------- #


def test_renderer_secret_rotates_per_sidecar_boot(tmp_path: Path) -> None:
    lockfile = tmp_path / "sidecar.lock"

    proc1 = _spawn_sidecar(lockfile=lockfile, data_dir=tmp_path)
    try:
        port1 = _wait_for_port_line(proc1, PORT_LINE_TIMEOUT_S)
        _wait_for_lockfile(lockfile)
        _wait_until_serving(port1)
        secret1 = LockfileRecord.model_validate_json(lockfile.read_bytes()).renderer_secret
    finally:
        rc = _kill_and_wait(proc1, GRACEFUL_SHUTDOWN_WAIT_S)
        # On POSIX we expect a graceful SIGTERM-driven shutdown; on Windows
        # TerminateProcess returns 1 and the finally block doesn't run.
        if sys.platform != "win32":
            # uvicorn re-raises SIGTERM after graceful shutdown, so the process
            # reports signal-termination (-15), not a clean 0 (ADR-0022).
            assert rc in (0, -signal.SIGTERM)
            # Lockfile was cleaned up by the lifespan shutdown hook (ADR-0022).
            assert not lockfile.exists()
        else:
            # Windows: clear the lockfile ourselves so the second sidecar
            # doesn't see a stale-but-believable lockfile.
            if lockfile.exists():
                lockfile.unlink()

    proc2 = _spawn_sidecar(lockfile=lockfile, data_dir=tmp_path)
    try:
        _wait_for_port_line(proc2, PORT_LINE_TIMEOUT_S)
        _wait_for_lockfile(lockfile)
        secret2 = LockfileRecord.model_validate_json(lockfile.read_bytes()).renderer_secret
        assert secret1 != secret2, "two consecutive cold starts must produce different secrets"
    finally:
        _force_kill(proc2)


# ----------------------------------------------------------------------------- #
# Unit-level: Windows stale-lock takeover regression                            #
#   On Windows a sidecar killed via TerminateProcess skips the lifespan/finally #
#   lockfile removal (the `:279` skip above), so the next boot ALWAYS meets a   #
#   stale lock. Takeover must claim it without the replace-existing path that   #
#   hit ERROR_ACCESS_DENIED when a handle was briefly held on the file.         #
# ----------------------------------------------------------------------------- #


def _stale_record(*, pid: int, port: int, secret: str, version: str) -> LockfileRecord:
    """A lockfile record with a deliberately mismatched create_time so
    `is_owner_alive` classifies it stale regardless of the live PID set."""
    return LockfileRecord(
        pid=pid,
        port=port,
        renderer_secret=secret,
        started_at=datetime.datetime.now(tz=datetime.UTC),
        process_create_time=0.0,
        sidecar_version=version,
    )


def test_write_lockfile_replaces_existing_destination(tmp_path: Path) -> None:
    """A write onto an already-present lockfile succeeds — the replace-existing
    path that raised ERROR_ACCESS_DENIED on Windows when a handle was held."""
    lockfile = tmp_path / "sidecar.lock"
    write_lockfile(lockfile, _stale_record(pid=111, port=1, secret="a" * 64, version="old"))
    write_lockfile(lockfile, _stale_record(pid=222, port=2, secret="b" * 64, version="new"))

    got = LockfileRecord.model_validate_json(lockfile.read_bytes())
    assert got.pid == 222
    assert got.port == 2
    assert got.sidecar_version == "new"


def test_probe_takeover_removes_stale_lockfile(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Takeover claims the lock by removing the prior owner's file before any
    write, so the subsequent write_lockfile is a create, not a replace."""
    lockfile = tmp_path / "sidecar.lock"
    pid = os.getpid()
    bogus_create_time = psutil.Process(pid).create_time() + 3600.0  # 1h ahead → mismatch
    stale = LockfileRecord(
        pid=pid,
        port=12345,
        renderer_secret="f" * 64,
        started_at=datetime.datetime.now(tz=datetime.UTC),
        process_create_time=bogus_create_time,
        sidecar_version="stale",
    )
    write_lockfile(lockfile, stale)
    assert lockfile.exists()

    _probe_and_prepare_lockfile(lockfile)

    assert not lockfile.exists()
    err = capsys.readouterr().err
    assert "taking over" in err
    assert str(pid) in err


def test_probe_refuses_when_owner_is_alive(tmp_path: Path) -> None:
    """A lockfile whose PID + create_time match a live process is NOT taken
    over — the probe exits non-zero and leaves the lockfile intact."""
    lockfile = tmp_path / "sidecar.lock"
    live = build_self_record(port=999, renderer_secret="e" * 64, sidecar_version="live")
    write_lockfile(lockfile, live)

    with pytest.raises(SystemExit) as excinfo:
        _probe_and_prepare_lockfile(lockfile)

    assert excinfo.value.code == 1
    assert lockfile.exists()


def test_atomic_replace_retries_on_windows_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On Windows a transient handle denies the replace-existing; the bounded
    retry rides it out and completes the rename rather than crashing boot."""
    src = tmp_path / "payload.tmp"
    src.write_text("payload", encoding="utf-8")
    dst = tmp_path / "sidecar.lock"
    dst.write_text("stale", encoding="utf-8")

    # lockfile.py reaches the stdlib via these same module objects, so patching
    # the globals here drives its branch + retry. Force the win32 path and make
    # the bounded sleep instant.
    real_replace = os.replace
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    calls = {"n": 0}

    def flaky_replace(a: Any, b: Any) -> None:
        calls["n"] += 1
        if calls["n"] <= 2:  # deny the first try + the first retry
            raise PermissionError(5, "Access is denied")
        real_replace(a, b)

    monkeypatch.setattr(os, "replace", flaky_replace)

    _atomic_replace(str(src), dst)

    assert dst.read_text(encoding="utf-8") == "payload"
    assert calls["n"] == 3  # first try + 2 retries; the third succeeds
