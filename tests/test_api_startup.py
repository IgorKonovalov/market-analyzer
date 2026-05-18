"""Integration test: the sidecar refuses to start without the env-var secret,
and accepts the secret via MARKET_ANALYSER_SECRET (Plan 0004 phase 3).

We spawn `python -m market_analyser.api --port=0` as a subprocess and assert
the secret-transport contract end-to-end. The successful-start path waits for
the `PORT=<n>` stdout line and then terminates the child so the test does not
leave a long-lived server bound.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _spawn(env_overrides: dict[str, str | None]) -> subprocess.Popen[str]:
    env = dict(os.environ)
    # Sanitize: remove any pre-existing secret so test cases control the var.
    env.pop("MARKET_ANALYSER_SECRET", None)
    for k, v in env_overrides.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    return subprocess.Popen(
        [sys.executable, "-m", "market_analyser.api", "--port=0"],
        cwd=_repo_root(),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_sidecar_starts_when_secret_env_is_set() -> None:
    proc = _spawn({"MARKET_ANALYSER_SECRET": "smoke-test-secret"})
    try:
        # Read stdout line-by-line until we see the PORT line or the process exits.
        assert proc.stdout is not None
        port_line: str | None = None
        deadline = time.time() + 10.0
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            if line.startswith("PORT="):
                port_line = line.strip()
                break
        assert port_line is not None, (
            f"sidecar did not print PORT= within 10s; "
            f"exit={proc.poll()} stderr={_drain_stderr(proc)}"
        )
        port = int(port_line.removeprefix("PORT="))
        assert port > 0
    finally:
        _terminate(proc)


def test_sidecar_refuses_to_start_without_secret_env() -> None:
    proc = _spawn({"MARKET_ANALYSER_SECRET": None})
    try:
        # Without the env var, _read_secret_from_env raises SystemExit before
        # uvicorn ever binds; the process should exit non-zero quickly.
        return_code = proc.wait(timeout=10.0)
        assert return_code != 0, "sidecar should exit non-zero when secret env is missing"
        stderr = _drain_stderr(proc)
        assert "MARKET_ANALYSER_SECRET" in stderr, (
            f"expected MARKET_ANALYSER_SECRET error message in stderr, got: {stderr!r}"
        )
    finally:
        _terminate(proc)


def test_sidecar_refuses_to_start_with_empty_secret_env() -> None:
    proc = _spawn({"MARKET_ANALYSER_SECRET": ""})
    try:
        return_code = proc.wait(timeout=10.0)
        assert return_code != 0, "sidecar should exit non-zero when secret env is empty"
        stderr = _drain_stderr(proc)
        assert "MARKET_ANALYSER_SECRET" in stderr
    finally:
        _terminate(proc)


def _terminate(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5.0)


def _drain_stderr(proc: subprocess.Popen[str]) -> str:
    assert proc.stderr is not None
    try:
        return proc.stderr.read() or ""
    except Exception:
        return ""
