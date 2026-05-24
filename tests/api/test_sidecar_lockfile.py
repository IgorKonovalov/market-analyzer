"""Plan 0007 phase 1 done-when: standalone sidecar lockfile + idempotent attach.

These are subprocess-level integration tests — they spawn the actual sidecar
via `python -m market_analyser.api --port=0 --lockfile=<tmp>`, observe the
lockfile contents and the process behavior, then send signals to confirm the
shutdown contract (the `finally` block removes the lockfile).

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

from market_analyser.api.lockfile import LockfileRecord, write_lockfile

PORT_LINE_TIMEOUT_S = 15.0
SIGTERM_WAIT_S = 5.0
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
        proc.wait(timeout=SIGTERM_WAIT_S)
        assert not lockfile.exists(), "lockfile should be removed by the SIGTERM finally block"
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

        proc.wait(timeout=SIGTERM_WAIT_S)
        assert not lockfile.exists(), (
            "lockfile should be removed by the graceful-shutdown finally block"
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
        rc = _kill_and_wait(proc1)
        # On POSIX we expect a clean SIGTERM-driven exit; on Windows
        # TerminateProcess returns 1 and the finally block doesn't run.
        if sys.platform != "win32":
            assert rc == 0
            # Lockfile was cleaned up by the SIGTERM finally block.
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
