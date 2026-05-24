"""Plan 0010 phase 1 — unit tests for the whole-word ticker matcher."""

from __future__ import annotations

from market_analyser.data._symbol_match import symbol_matches


def test_matches_whole_word_token() -> None:
    assert symbol_matches("BTC", "BTC reaches new high")


def test_match_is_case_insensitive() -> None:
    assert symbol_matches("btc", "Bitcoin and BTC rally")
    assert symbol_matches("BTC", "the btc price moved sharply")


def test_no_substring_false_positive() -> None:
    # "ETH" must not match inside "Together"; token match, not substring.
    assert not symbol_matches("ETH", "Together they invest")
    # "BTC" must not match inside "BTCUSD".
    assert not symbol_matches("BTC", "BTCUSD pair listed today")


def test_rejects_tickers_shorter_than_two_chars() -> None:
    # A one-letter ticker (e.g. AT&T's "T") would hit almost any sentence.
    assert not symbol_matches("T", "There is time to act")
    assert not symbol_matches("", "anything at all")


def test_empty_text_never_matches() -> None:
    assert not symbol_matches("BTC", "")


def test_matches_against_summary_text() -> None:
    assert symbol_matches("AAPL", "Apple event draws a crowd. AAPL up 2% today.")
