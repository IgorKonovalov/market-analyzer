"""User-supplied third-party API-key storage (ADR-0038, Plan 0032 phase 1).

Every TradFi source shipped to date is keyless; the DeFi program is the first to
need a long-lived, user-supplied credential (the Zerion API key, later RPC/Graph
keys). Those keys are a different category from the two secrets the app already
handles — the per-launch renderer bearer (ADR-0011) and the machine-generated
MCP secret (ADR-0014) — because they are *user-supplied* and must persist across
launches. They live in a dedicated `0600` `secrets.json` in the user-data dir
(ADR-0020), the same on-disk model as `mcp-secret.json` — **not** `config.json`
(the shareable, non-secret config surface) and **not** SQLite (copied as data).

Three rules bound the handling (ADR-0038):

1. **Never logged, never in argv.** `SecretsStore.__repr__` renders only which
   keys are set, never a value; nothing here writes a value to a log.
2. **Write-only to the renderer.** `status()` reports presence/absence per key;
   no method returns a stored value to the renderer. (`get()` exists for the
   *sidecar's own* adapters to inject the key server-side.)
3. **Env-var override per key.** `MARKET_ANALYSER_<KEY>` takes precedence over
   the file, so CI/dev inject keys without touching disk.

`secrets.json` is a flat, Pydantic-validated map of known keys to string values;
it is created `0600` on first write and is never committed. Windows `0600` is
weak (a user's own processes can read files in their own profile regardless of
ACL) — accepted, consistent with `mcp-secret.json`, documented not engineered
around (ADR-0038, ADR-0011 Alt C).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, cast, get_args

from pydantic import BaseModel, ConfigDict

POSIX_FILE_MODE = 0o600
SECRETS_FILENAME = "secrets.json"
ENV_VAR_PREFIX = "MARKET_ANALYSER_"

# The known secret keys. `zerion_api_key` is Plan 0032's consumer; the RPC/Graph
# keys are reserved for the deep-adapter / pricing plans that reuse this store
# unchanged (ADR-0034 notes, ADR-0035 §Authenticated-source prerequisite). The
# `binance_read_*` pair is the Plan 0041 portfolio leg's **read-only** credential
# (ADR-0042) — a lower-value secret than a trade key, which is why it lives here
# and not in the Pillar-5 trade keychain (ADR-0044). `alchemy_prices_key` is the
# Plan 0087 / ADR-0081 keyed historical-price fallback's credential (the DeFi P&L
# leg DefiLlama cannot price); read-only, injected server-side via the Alchemy
# Prices `Authorization` header, never the URL path (secret-hygiene note there).
# `lunarcrush_api_key` is the Plan 0108 / ADR-0103 X-social sentiment source's
# credential (LunarCrush reference provider); read-only, injected server-side via
# a Bearer `Authorization` header — absent the key the source is inert and
# returns honest-empty, so the key is optional by design.
# `reddit_client_id` / `reddit_client_secret` are the Plan 0111 / ADR-0105 pair
# for the Reddit sentiment adapter's keyed app-only OAuth path: both present →
# the adapter obtains a `client_credentials` bearer and searches `oauth.reddit.com`
# to climb over the keyless-JSON anti-bot wall; either absent → today's keyless
# path stands unchanged (honest-empty when blocked), so the pair is optional.
# `fred_api_key` is the Plan 0113 / ADR-0107 event-calendar macro provider's free
# self-serve credential (St. Louis Fed FRED — CPI/PCE release dates); read-only,
# injected server-side as a query param (path-only failure logging keeps it out of
# logs) — absent the key the FRED provider is inert and the macro read is FOMC-only,
# so the key is optional by design.
# `finnhub_api_key` is the Plan 0113 / ADR-0107 event-calendar earnings provider's
# free self-serve credential (Finnhub earnings calendar); read-only, injected
# server-side via the `X-Finnhub-Token` header (never the URL) — absent the key the
# earnings category is inert and honest-empty, so the key is optional by design.
SecretKey = Literal[
    "zerion_api_key",
    "graph_api_key",
    "eth_rpc_url",
    "base_rpc_url",
    "binance_read_api_key",
    "binance_read_api_secret",
    "alchemy_prices_key",
    "lunarcrush_api_key",
    "reddit_client_id",
    "reddit_client_secret",
    "fred_api_key",
    "finnhub_api_key",
]
KNOWN_SECRET_KEYS: tuple[SecretKey, ...] = get_args(SecretKey)
SecretStatus = Literal["set", "unset"]


class SecretsFile(BaseModel):
    """The on-disk `secrets.json` shape — a flat map of known keys to values.

    `extra="forbid"` so an unknown key in a hand-edited file fails loudly at load
    rather than being silently carried (mirrors `AppConfig`'s strict-extra rule).
    """

    model_config = ConfigDict(extra="forbid")

    zerion_api_key: str | None = None
    graph_api_key: str | None = None
    eth_rpc_url: str | None = None
    base_rpc_url: str | None = None
    binance_read_api_key: str | None = None
    binance_read_api_secret: str | None = None
    alchemy_prices_key: str | None = None
    lunarcrush_api_key: str | None = None
    reddit_client_id: str | None = None
    reddit_client_secret: str | None = None
    fred_api_key: str | None = None
    finnhub_api_key: str | None = None


class SecretsStore:
    """Read/write third-party API keys: env-override-first, never logging values.

    Constructed once per sidecar over the user-data `secrets.json` path. Adapters
    read their key via `get()` inside the sidecar and inject it into outbound
    calls there (ADR-0038 server-side injection); the renderer only ever `set`s a
    key or reads `status()`.

    `environ` is injectable for tests/headless runs; it defaults to the live
    `os.environ` so the per-key override is read dynamically at `get()` time.
    """

    def __init__(self, path: Path, *, environ: Mapping[str, str] | None = None) -> None:
        self._path = path
        self._environ: Mapping[str, str] = environ if environ is not None else os.environ

    def get(self, key: SecretKey) -> str | None:
        """Return the value for `key`: env override first, then the file, else None.

        Returns None for an env var set to the empty string — an empty key is
        absence, not a value.
        """
        env_value = self._environ.get(f"{ENV_VAR_PREFIX}{key.upper()}")
        if env_value:
            return env_value
        # `key` is a known field of SecretsFile; the value is `str | None`.
        return cast("str | None", getattr(self._read_file(), key))

    def set(self, key: SecretKey, value: str) -> None:
        """Persist `value` for `key` to the `0600` secrets file (atomic write).

        Refuses an empty value — clearing a key is a file edit, not a set.
        """
        if not value:
            raise ValueError(f"refusing to set an empty value for {key!r}")
        updated = self._read_file().model_copy(update={key: value})
        self._write_file(updated)

    def status(self) -> dict[SecretKey, SecretStatus]:
        """Presence/absence per known key — never a value (ADR-0038 write-only).

        Reflects the env override too: a key present only via `MARKET_ANALYSER_*`
        reports `"set"`.
        """
        return {key: ("set" if self.get(key) else "unset") for key in KNOWN_SECRET_KEYS}

    def _read_file(self) -> SecretsFile:
        if not self._path.exists():
            return SecretsFile()
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        return SecretsFile.model_validate(raw)

    def _write_file(self, data: SecretsFile) -> None:
        """Atomic-replace the file, reasserting `0600` on POSIX.

        mkstemp + os.replace so a crash mid-write cannot leave a half-formed
        file; chmod after replace because mkstemp's mode is platform-dependent
        and we need the guarantee on every write (mirrors `mcp_secret.py`).
        Only known, non-None keys are serialized (`exclude_none`).
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = data.model_dump(exclude_none=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".secrets.", suffix=".tmp", dir=self._path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp_name, self._path)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
        if sys.platform != "win32":
            os.chmod(self._path, POSIX_FILE_MODE)

    def __repr__(self) -> str:
        # Redaction (ADR-0038 rule 1): render which keys are set, never a value.
        return f"SecretsStore(path={self._path!r}, status={self.status()})"
