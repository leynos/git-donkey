"""Unit tests for incoming and outgoing comparison helpers."""

from __future__ import annotations

import typing as typ

from git_donkey import incoming_outgoing

if typ.TYPE_CHECKING:
    import pytest


class _FakeGit:
    """Minimal ``repo.git`` double for comparison-range tests."""

    def __init__(self, log_output: str) -> None:
        self.log_output = log_output
        self.calls: list[tuple[str, ...]] = []

    def log(self, *args: str) -> str:
        """Record log arguments and return the configured output."""
        self.calls.append(args)
        return self.log_output


class _FakeRepo:
    """Minimal repository double with a ``git`` command surface."""

    def __init__(self, log_output: str) -> None:
        self.git = _FakeGit(log_output)


def test_commits_unique_to_ref_prints_log_lines(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Comparison output should pass through concise ``git log`` lines."""
    repo = _FakeRepo("abc1234 Remote commit")

    has_commits = incoming_outgoing._print_commits_unique_to(
        repo.git,
        include_ref="origin/main",
        exclude_ref="HEAD",
    )

    assert has_commits is True
    assert capsys.readouterr().out == "abc1234 Remote commit\n"
    assert repo.git.calls == [
        (
            "--oneline",
            "--decorate",
            "origin/main",
            "--not",
            "HEAD",
        )
    ]


def test_commits_unique_to_ref_handles_empty_log(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No comparison commits should produce no output."""
    repo = _FakeRepo("")

    has_commits = incoming_outgoing._print_commits_unique_to(
        repo.git,
        include_ref="HEAD",
        exclude_ref="origin/main",
    )

    assert has_commits is False
    assert capsys.readouterr().out == ""
