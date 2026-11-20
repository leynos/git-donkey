"""Exact assertions for user-facing command output.

The integration suites pin what a command writes to standard output and
standard error. Substring checks are too weak for that job: they accept a
message that was truncated, extended, or emitted beside unrelated noise. These
helpers compare whole streams and whole lines instead, and they keep the one
deliberate exception — a diagnostic produced by a third-party tool — narrow
enough that it can only stand in for the tail of the stream.

Four tools cover the cases the suites need:

- :func:`stream` builds the expected text, so a multi-line expectation reads
  as its lines rather than as escaped newlines.
- :func:`require_stream` compares a whole stream, optionally accepting a
  trailing :data:`GIT_ERROR_PLACEHOLDER` where Git's own error text is
  reported.
- :func:`require_terminal` compares the diagnostic a run ends with, for the
  commands that write progress before they fail.
- :func:`require_line` requires one complete line to be present, which is what
  a multi-line report needs when its other lines are not the subject of the
  assertion.

Each helper calls :func:`pytest.fail` with both the expected and the received
text, so a failure shows the whole stream instead of a truncated diff.
"""

from __future__ import annotations

import typing as typ

import pytest

#: Placeholder for a diagnostic produced by a third-party tool such as Git.
#: It is only accepted as the final characters of a template, and it must match
#: a non-empty remainder of the stream.
GIT_ERROR_PLACEHOLDER = "{git_error}"


def stream(*lines: str) -> str:
    r"""Build a newline-terminated output stream from ``lines``.

    Parameters
    ----------
    *lines : str
        Lines of the stream, in order. Pass an empty string for a blank line.

    Returns
    -------
    str
        The lines joined by newlines, with a final newline.

    Examples
    --------
    >>> stream("git-donkey: first", "second")
    'git-donkey: first\nsecond\n'

    """
    return "".join(f"{line}\n" for line in lines)


def require_stream(
    actual: str,
    expected_template: str,
    *,
    context: str = "output",
) -> None:
    """Fail unless ``actual`` is exactly ``expected_template``.

    A template whose final characters are :data:`GIT_ERROR_PLACEHOLDER`
    accepts any non-empty remainder in that position. That is how a stream
    that ends with Git's own error text is pinned without restating Git's
    wording. Every other character is compared literally, including the final
    newline.

    Parameters
    ----------
    actual : str
        The captured stream under test.
    expected_template : str
        The complete expected stream, or a literal prefix followed by the
        trailing placeholder.
    context : str
        What the stream represents, used in the failure message.

    Raises
    ------
    ValueError
        If the placeholder appears anywhere but once, as the end of the
        template.

    Examples
    --------
    >>> require_stream(stream("git-donkey: done"), stream("git-donkey: done"))
    >>> require_stream(
    ...     stream("git-donkey: fetch failed: fatal: boom"),
    ...     "git-donkey: fetch failed: {git_error}",
    ... )

    """
    if GIT_ERROR_PLACEHOLDER not in expected_template:
        if actual != expected_template:
            _fail_stream(actual, expected_template, context)
        return

    prefix = _git_error_prefix(expected_template)
    external_text = actual.removeprefix(prefix)
    if not actual.startswith(prefix) or not external_text:
        pytest.fail(
            f"expected {context} to start with {prefix!r} followed by a "
            f"non-empty external diagnostic\nreceived: {actual!r}"
        )


def require_terminal(
    actual: str,
    expected_template: str,
    *,
    context: str = "output",
) -> None:
    """Fail unless ``actual`` ends with a diagnostic matching the template.

    Some commands report progress before they fail, so their diagnostic is the
    tail of the stream rather than all of it. The template must begin at a line
    boundary, and its final :data:`GIT_ERROR_PLACEHOLDER` must match the
    non-empty remainder of the stream, which is where Git's own error text
    sits.

    Parameters
    ----------
    actual : str
        The captured stream under test.
    expected_template : str
        Literal diagnostic text ending with the placeholder.
    context : str
        What the stream represents, used in the failure message.

    Raises
    ------
    ValueError
        If the placeholder appears anywhere but once, as the end of the
        template.

    Examples
    --------
    >>> require_terminal(
    ...     stream("Using remote: origin", "git-donkey: fetch failed: fatal: boom"),
    ...     "git-donkey: fetch failed: {git_error}",
    ... )

    """
    prefix = _git_error_prefix(expected_template)
    for position in (
        0,
        *(index + 1 for index, char in enumerate(actual) if char == "\n"),
    ):
        if actual.startswith(prefix, position) and actual[position + len(prefix) :]:
            return
    pytest.fail(
        f"expected {context} to end with the diagnostic {prefix!r} followed by "
        f"a non-empty external diagnostic\nreceived: {actual!r}"
    )


def require_line(
    actual: str,
    expected_line: str,
    *,
    context: str = "output",
) -> None:
    """Fail unless ``expected_line`` is one complete line of ``actual``.

    The comparison keeps line endings, so a line that merely starts with the
    expected text does not match, and neither does an unterminated final line:
    the expected line must be terminated by its newline.

    Parameters
    ----------
    actual : str
        The captured stream under test.
    expected_line : str
        The complete line to find, with or without its newline.
    context : str
        What the stream represents, used in the failure message.

    Examples
    --------
    >>> require_line(stream("first", "- demo"), "- demo")

    """
    terminated = (
        expected_line if expected_line.endswith("\n") else (f"{expected_line}\n")
    )
    if terminated in actual.splitlines(keepends=True):
        return
    pytest.fail(
        f"expected {context} to contain the complete line {terminated!r}\n"
        f"received: {actual!r}"
    )


def _git_error_prefix(expected_template: str) -> str:
    """Return the literal prefix of a template ending with the placeholder."""
    if (
        not expected_template.endswith(GIT_ERROR_PLACEHOLDER)
        or expected_template.count(GIT_ERROR_PLACEHOLDER) != 1
    ):
        message = (
            f"{GIT_ERROR_PLACEHOLDER} must be the only placeholder and the "
            f"final characters of the template: {expected_template!r}"
        )
        raise ValueError(message)
    return expected_template.removesuffix(GIT_ERROR_PLACEHOLDER)


def _fail_stream(actual: str, expected: str, context: str) -> typ.NoReturn:
    """Fail with both streams rendered in full."""
    pytest.fail(
        f"expected {context} to match exactly\n"
        f"expected: {expected!r}\n"
        f"received: {actual!r}"
    )
