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
    require_terminal,
    stream,
)

_GIT_FAILURE_TEMPLATE = f"git-donkey: fetch failed: {GIT_ERROR_PLACEHOLDER}"


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


def test_require_stream_accepts_a_terminal_placeholder_with_a_newline() -> None:
    """A whole-stream template may keep the final newline after the placeholder."""
    require_stream(
        stream("Using remote: origin", "git-donkey: fetch failed: fatal: boom"),
        stream(
            "Using remote: origin",
            f"git-donkey: fetch failed: {GIT_ERROR_PLACEHOLDER}",
        ),
    )


def test_require_stream_rejects_a_git_error_without_a_final_newline() -> None:
    """Git's text may span lines, but the application's final newline stays."""
    with pytest.raises(pytest.fail.Exception):
        require_stream(
            "git-donkey: fetch failed: fatal: boom",
            f"git-donkey: fetch failed: {GIT_ERROR_PLACEHOLDER}",
        )


def test_require_stream_rejects_a_newline_only_git_error() -> None:
    """A lone newline is not the diagnostic the placeholder stands in for."""
    with pytest.raises(pytest.fail.Exception):
        require_stream(
            "git-donkey: fetch failed: \n",
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


def test_require_terminal_accepts_a_diagnostic_after_progress() -> None:
    """A diagnostic that follows progress output should still match."""
    require_terminal(
        stream("Using remote: origin", "git-donkey: fetch failed: fatal: boom"),
        _GIT_FAILURE_TEMPLATE,
    )


def test_require_terminal_accepts_a_multi_line_git_error() -> None:
    """Git's own multi-line text should stand in for the placeholder."""
    require_terminal(
        stream("git-donkey: fetch failed: fatal: boom", "fatal: could not read"),
        _GIT_FAILURE_TEMPLATE,
    )


def test_require_terminal_rejects_a_missing_diagnostic() -> None:
    """A stream without the diagnostic should fail even if it has progress."""
    with pytest.raises(pytest.fail.Exception):
        require_terminal(stream("Using remote: origin"), _GIT_FAILURE_TEMPLATE)


def test_require_terminal_rejects_an_empty_git_error() -> None:
    """The diagnostic must carry the third-party text the placeholder stands for."""
    with pytest.raises(pytest.fail.Exception):
        require_terminal("git-donkey: fetch failed: ", _GIT_FAILURE_TEMPLATE)


def test_require_terminal_rejects_a_git_error_without_a_final_newline() -> None:
    """The terminating newline belongs to the application and must survive."""
    with pytest.raises(pytest.fail.Exception):
        require_terminal(
            "git-donkey: fetch failed: fatal: boom",
            _GIT_FAILURE_TEMPLATE,
        )


def test_require_terminal_rejects_a_newline_only_git_error() -> None:
    """A lone newline is not the diagnostic the placeholder stands in for."""
    with pytest.raises(pytest.fail.Exception):
        require_terminal("git-donkey: fetch failed: \n", _GIT_FAILURE_TEMPLATE)


def test_require_terminal_rejects_a_mid_line_match() -> None:
    """A diagnostic that does not start a line should not match."""
    with pytest.raises(pytest.fail.Exception):
        require_terminal(
            stream("progress: git-donkey: fetch failed: fatal: boom"),
            _GIT_FAILURE_TEMPLATE,
        )


def test_require_terminal_rejects_a_non_trailing_placeholder() -> None:
    """The terminal helper validates the placeholder position too."""
    with pytest.raises(ValueError, match="final characters"):
        require_terminal(
            stream("git-donkey: fetch failed: fatal: boom: done"),
            f"{GIT_ERROR_PLACEHOLDER}: done",
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
