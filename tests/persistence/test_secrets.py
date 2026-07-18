"""Plan 0032 phase 1 done-when (ADR-0038): the `SecretsStore`.

Asserted behaviors:
- Setting a key writes it to `<data>/secrets.json` at `0600` (POSIX) and the
  value persists across a "restart" (a fresh store over the same path).
- An env-var override (`MARKET_ANALYSER_ZERION_API_KEY`) takes precedence over
  the file value.
- `status()` reports presence/absence per key, never the value.
- The value never appears in `repr()` / log output (redaction discipline).
- An empty `set` is refused; a malformed file fails loudly.
"""

from __future__ import annotations

import json
import logging
import stat
import sys
from pathlib import Path

import pytest

from market_analyser.persistence.secrets import (
    SECRETS_FILENAME,
    SecretsFile,
    SecretsStore,
)

ZERION_KEY = "zk_live_supersecret_value_0123456789"


@pytest.fixture
def secrets_path(tmp_path: Path) -> Path:
    return tmp_path / SECRETS_FILENAME


def test_set_then_get_roundtrips(secrets_path: Path) -> None:
    store = SecretsStore(secrets_path, environ={})
    store.set("zerion_api_key", ZERION_KEY)
    assert store.get("zerion_api_key") == ZERION_KEY


def test_set_writes_file_with_only_known_set_keys(secrets_path: Path) -> None:
    SecretsStore(secrets_path, environ={}).set("zerion_api_key", ZERION_KEY)
    on_disk = json.loads(secrets_path.read_text(encoding="utf-8"))
    assert on_disk == {"zerion_api_key": ZERION_KEY}


def test_value_persists_across_restart(secrets_path: Path) -> None:
    """A fresh store over the same path reads back the set value — the on-disk
    file is the source of truth, not in-process state."""
    SecretsStore(secrets_path, environ={}).set("zerion_api_key", ZERION_KEY)
    reopened = SecretsStore(secrets_path, environ={})
    assert reopened.get("zerion_api_key") == ZERION_KEY


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file mode bits don't apply on Windows")
def test_set_creates_file_at_0600(secrets_path: Path) -> None:
    SecretsStore(secrets_path, environ={}).set("zerion_api_key", ZERION_KEY)
    mode_bits = stat.S_IMODE(secrets_path.stat().st_mode)
    assert mode_bits == 0o600, f"expected 0600, got {oct(mode_bits)}"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file mode bits don't apply on Windows")
def test_overwrite_reasserts_0600(secrets_path: Path) -> None:
    store = SecretsStore(secrets_path, environ={})
    store.set("zerion_api_key", ZERION_KEY)
    store.set("zerion_api_key", "zk_rotated_value")
    mode_bits = stat.S_IMODE(secrets_path.stat().st_mode)
    assert mode_bits == 0o600, f"expected 0600 after overwrite, got {oct(mode_bits)}"


def test_env_override_takes_precedence_over_file(secrets_path: Path) -> None:
    store = SecretsStore(
        secrets_path,
        environ={"MARKET_ANALYSER_ZERION_API_KEY": "env_override_value"},
    )
    store.set("zerion_api_key", ZERION_KEY)  # file says one thing...
    assert store.get("zerion_api_key") == "env_override_value"  # ...env wins


def test_env_override_works_without_a_file(secrets_path: Path) -> None:
    store = SecretsStore(
        secrets_path,
        environ={"MARKET_ANALYSER_ZERION_API_KEY": "env_only_value"},
    )
    assert store.get("zerion_api_key") == "env_only_value"
    assert not secrets_path.exists()  # reading an env override never touches disk


def test_empty_env_var_is_absence_not_value(secrets_path: Path) -> None:
    store = SecretsStore(secrets_path, environ={"MARKET_ANALYSER_ZERION_API_KEY": ""})
    assert store.get("zerion_api_key") is None


def test_get_returns_none_for_unset_key(secrets_path: Path) -> None:
    assert SecretsStore(secrets_path, environ={}).get("graph_api_key") is None


def test_status_reports_presence_not_value(secrets_path: Path) -> None:
    store = SecretsStore(secrets_path, environ={})
    store.set("zerion_api_key", ZERION_KEY)
    status = store.status()
    assert status["zerion_api_key"] == "set"
    assert status["graph_api_key"] == "unset"
    # The whole status map is presence/absence strings — no value leaks.
    assert ZERION_KEY not in json.dumps(status)


def test_status_reflects_env_override(secrets_path: Path) -> None:
    store = SecretsStore(
        secrets_path,
        environ={"MARKET_ANALYSER_GRAPH_API_KEY": "env_graph"},
    )
    assert store.status()["graph_api_key"] == "set"


def test_alchemy_prices_key_is_a_known_secret(secrets_path: Path) -> None:
    """Plan 0087 / ADR-0081: the keyed historical-price fallback's credential is a
    registered key — settable, retrievable, env-overridable, and default-unset —
    so a `secrets.json` carrying it loads (the store is `extra="forbid"`)."""
    assert SecretsStore(secrets_path, environ={}).get("alchemy_prices_key") is None
    assert SecretsStore(secrets_path, environ={}).status()["alchemy_prices_key"] == "unset"
    store = SecretsStore(secrets_path, environ={"MARKET_ANALYSER_ALCHEMY_PRICES_KEY": "env_alch"})
    assert store.get("alchemy_prices_key") == "env_alch"
    file_store = SecretsStore(secrets_path, environ={})
    file_store.set("alchemy_prices_key", "sk-file-value")
    assert SecretsStore(secrets_path, environ={}).get("alchemy_prices_key") == "sk-file-value"


def test_reddit_oauth_keys_are_known_secrets(secrets_path: Path) -> None:
    """Plan 0111 / ADR-0105: the Reddit app-only OAuth pair is registered — each key is
    settable, retrievable, env-overridable, and default-unset — so a `secrets.json` carrying
    them loads (the store is `extra="forbid"`)."""
    for key, env_var in (
        ("reddit_client_id", "MARKET_ANALYSER_REDDIT_CLIENT_ID"),
        ("reddit_client_secret", "MARKET_ANALYSER_REDDIT_CLIENT_SECRET"),
    ):
        assert SecretsStore(secrets_path, environ={}).get(key) is None
        assert SecretsStore(secrets_path, environ={}).status()[key] == "unset"
        env_store = SecretsStore(secrets_path, environ={env_var: "env_reddit"})
        assert env_store.get(key) == "env_reddit"
        assert env_store.status()[key] == "set"
        file_store = SecretsStore(secrets_path, environ={})
        file_store.set(key, f"file-{key}")
        assert SecretsStore(secrets_path, environ={}).get(key) == f"file-{key}"


def test_repr_redacts_the_value(secrets_path: Path) -> None:
    store = SecretsStore(secrets_path, environ={})
    store.set("zerion_api_key", ZERION_KEY)
    assert ZERION_KEY not in repr(store)
    assert "set" in repr(store)  # it does say *which* keys are set


def test_value_never_logged_during_set_or_get(
    secrets_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """No code path in the store emits the value to logging."""
    store = SecretsStore(secrets_path, environ={})
    with caplog.at_level(logging.DEBUG):
        store.set("zerion_api_key", ZERION_KEY)
        store.get("zerion_api_key")
        store.status()
    assert ZERION_KEY not in caplog.text


def test_set_empty_value_is_refused(secrets_path: Path) -> None:
    with pytest.raises(ValueError, match="empty value"):
        SecretsStore(secrets_path, environ={}).set("zerion_api_key", "")


def test_malformed_file_fails_loudly(secrets_path: Path) -> None:
    secrets_path.write_text('{"unknown_key": "x"}', encoding="utf-8")
    with pytest.raises(ValueError):  # pydantic extra="forbid" → ValidationError (a ValueError)
        SecretsStore(secrets_path, environ={}).get("zerion_api_key")


def test_secrets_file_rejects_unknown_field() -> None:
    with pytest.raises(ValueError):
        SecretsFile.model_validate({"bogus": "x"})
