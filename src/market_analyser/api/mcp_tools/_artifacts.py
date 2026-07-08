"""Shared explanation-artifact plumbing for the MCP tool layer (Plan 0063,
ADR-0058).

The `forecast` and `recommend` tools each persist a per-call explanation JSON
under the sidecar's configured ``runs_dir`` (``runs_dir/forecast/…`` and
``runs_dir/advice/…`` respectively). The filesystem-safe path component and the
plain-write helper are generic to both tools, so they live here rather than in
either tool module — neither reaches into the other's private surface for them.
"""

from __future__ import annotations

import re
from pathlib import Path

# Path components of an explanation artifact are derived from user input
# (symbol); anything outside this conservative set is replaced so the path is
# valid on every filesystem (Windows included: ^GSPC and ES=F both sanitise).
_UNSAFE_PATH_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def _fs_safe(component: str) -> str:
    return _UNSAFE_PATH_CHARS.sub("_", component)


def _write_explanation_artifact(artifact_json: str, runs_dir: Path, rel_path: str) -> None:
    """Persist one call's explanation JSON at ``runs_dir / rel_path`` (Plan
    0063). Plain write, no tmp-dir ceremony: the directory is unique per call
    (wall-clock stamp), so there is no concurrent writer to race."""

    target = runs_dir / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(artifact_json, encoding="utf-8")


__all__ = ["_fs_safe", "_write_explanation_artifact"]
