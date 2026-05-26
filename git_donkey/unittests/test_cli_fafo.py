"""Unit tests for the git-fafo command-line interface."""

from __future__ import annotations

import typing as typ

import pytest

from git_donkey import cli, fafo

if typ.TYPE_CHECKING:
    import collections.abc as cabc


def test_fafo_cli_passes_trust_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The --trust CLI option should enable trusted Copier execution."""
    recorded: dict[str, object] = {}

    def _fake_run_git_fafo(
        repo_name: str,
        language: str,
        *,
        trust: bool,
    ) -> int:
        recorded["repo_name"] = repo_name
        recorded["language"] = language
        recorded["trust"] = trust
        return 0

    monkeypatch.setattr(fafo, "run_git_fafo", _fake_run_git_fafo)

    with pytest.raises(SystemExit) as excinfo:
        typ.cast("cabc.Callable[[list[str]], None]", cli._fafo_app)([
            "demo-repo",
            "python",
            "--trust",
        ])

    assert excinfo.value.code == 0
    assert recorded == {
        "repo_name": "demo-repo",
        "language": "python",
        "trust": True,
    }
