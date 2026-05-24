"""VADER sentiment scoring wrapper — Plan 0010 phase 2.

Keeps the `vaderSentiment` import and the project's scoring policy in one place:
which fields to concatenate for a headline, and how empty input is handled.
VADER is a lexicon scorer — fully deterministic on the same input (no model
weights, no randomness), so a re-run yields a byte-identical compound score.
"""

from __future__ import annotations

from functools import lru_cache

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer


@lru_cache(maxsize=1)
def _analyzer() -> SentimentIntensityAnalyzer:
    # The lexicon (~5 MB) loads once on first use, then is reused process-wide.
    return SentimentIntensityAnalyzer()


def score(text: str) -> float:
    """Return VADER's compound score in [-1.0, 1.0] for `text`.

    Empty or whitespace-only text scores 0.0 (neutral). A non-string (e.g.
    ``None``) raises ``TypeError`` — callers must pass real text.
    """
    if not isinstance(text, str):
        raise TypeError(f"score() expects str, got {type(text).__name__}")
    stripped = text.strip()
    if not stripped:
        return 0.0
    compound: float = _analyzer().polarity_scores(stripped)["compound"]
    return compound


def score_headline(title: str, summary: str = "") -> float:
    """Score a news headline using the project policy: VADER over the title and
    summary combined (``title`` alone when there is no summary)."""
    combined = f"{title} {summary}".strip() if summary else title
    return score(combined)


__all__ = ["score", "score_headline"]
