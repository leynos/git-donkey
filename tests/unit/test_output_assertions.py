"""Unit tests for the exact-output assertion helpers.

The helpers are the shared vocabulary every integration assertion uses, so
their failure behaviour matters as much as their success behaviour: a matcher
that quietly accepts a truncated stream would weaken every suite that adopts
it.
"""

from __future__ import annotations

import pytest

from tests.output_assertions import (
    GIT_ERROR_PLACEHOLDER,
    require_line,
    require_stream,
    stream,
)


def test_stream_terminates_every_line_including_blanks() -> None:
    """The builder should append a newline to each line, blanks included."""
    expected = "first\n\nthird\n"
    assert stream("first", "", "third") == expected, (
        "the builder should terminate a blank line with its own newline"
    )


def test_stream_of_nothing_is_empty() -> None:
    """An empty stream should stay empty rather than gain a newline."""
    assert len(stream()) == 0, "an empty line list should build an empty stream"


def test_require_stream_accepts_an_identical_stream() -> None:
    """A whole stream that matches exactly should pass."""
    require_stream(stream("git-donkey: done"), stream("git-donkey: done"))


def test_require_stream_rejects_a_missing_final_newline() -> None:
    """A stream missing its final newline should fail the comparison."""
    with pytest.raises(pytest.fail.Exception):
        require_stream("git-donkey: done", stream("git-donkey: done"))


def test_require_stream_rejects_extra_output() -> None:
    """A stream with an unexpected trailing line should fail the comparison."""
    with pytest.raises(pytest.fail.Exception):
        require_stream(
            stream("git-donkey: done", "unexpected"),
            stream("git-donkey: done"),
        )


def test_require_stream_accepts_a_trailing_git_error() -> None:
    """A trailing placeholder should accept Git's own multi-line text."""
    git_error = "fatal: unable to access remote\nfatal: could not read"
    require_stream(
        stream(f"git-donkey: fetch failed: {git_error}"),
        f"git-donkey: fetch failed: {GIT_ERROR_PLACEHOLDER}",
    )


def test_require_stream_rejects_an_empty_git_error() -> None:
    """The placeholder should require some diagnostic text to follow."""
    with pytest.raises(pytest.fail.Exception):
        require_stream(
            "git-donkey: fetch failed: ",
            f"git-donkey: fetch failed: {GIT_ERROR_PLACEHOLDER}",
        )


def test_require_stream_rejects_a_changed_literal_prefix() -> None:
    """Text before the placeholder should still be compared exactly."""
    with pytest.raises(pytest.fail.Exception):
        require_stream(
            stream("git-donkey: push failed: fatal: boom"),
            f"git-donkey: fetch failed: {GIT_ERROR_PLACEHOLDER}",
        )


def test_require_stream_rejects_a_non_trailing_placeholder() -> None:
    """A template may not put the placeholder anywhere but at the end."""
    with pytest.raises(ValueError, match="final characters"):
        require_stream(
            stream("fatal: boom: git-donkey"),
            f"{GIT_ERROR_PLACEHOLDER}: git-donkey",
        )


def test_require_stream_rejects_a_repeated_placeholder() -> None:
    """A template may hold the placeholder at most once."""
    with pytest.raises(ValueError, match="only placeholder"):
        require_stream(
            stream("a: b: c"),
            f"{GIT_ERROR_PLACEHOLDER}: {GIT_ERROR_PLACEHOLDER}",
        )


def test_require_line_accepts_a_complete_line() -> None:
    """A whole line in the middle of a stream should pass."""
    require_line(stream("Planned worktree removals:", "- demo", ""), "- demo")


def test_require_line_accepts_an_expected_line_with_its_newline() -> None:
    """An expected line that already ends in a newline should pass unchanged."""
    require_line(stream("- demo", ""), "- demo\n")


def test_require_line_rejects_a_line_prefix() -> None:
    """A longer line that starts with the expected text should not match."""
    with pytest.raises(pytest.fail.Exception):
        require_line(stream("- demo (uncommitted changes)", ""), "- demo")


def test_require_line_rejects_an_unterminated_final_line() -> None:
    """An unterminated final line should not count as a complete line."""
    with pytest.raises(pytest.fail.Exception):
        require_line("- demo", "- demo")
