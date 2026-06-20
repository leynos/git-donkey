"""Unit tests for the ``git-fafo`` command-line interface.

These tests verify the Cyclopts wrapper in ``git_donkey.cli`` passes parsed
arguments through to ``git_donkey.fafo.run_git_fafo`` without exercising the
GitHub or Git orchestration layers.
"""

from __future__ import annotations

import typing as typ

import pytest

from git_donkey import cli, fafo

if typ.TYPE_CHECKING:
    import collections.abc as cabc


@pytest.mark.parametrize(
    ("argv", "expected_language", "expected_trust", "expected_yes"),
    [
        pytest.param(
            ["demo-repo", "python", "--trust"],
            "python",
            True,
            False,
            id="trust-option",
        ),
        pytest.param(["demo-repo"], None, False, False, id="missing-language"),
        pytest.param(["demo-repo", "-y"], None, False, True, id="yes-short-option"),
    ],
)
def test_fafo_cli_passes_scaffold_options(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    *,
    expected_language: str | None,
    expected_trust: bool,
    expected_yes: bool,
) -> None:
    """CLI scaffold options should pass through to the workflow function."""
    recorded: dict[str, object] = {}

    def _fake_run_git_fafo(
        repo_name: str,
        language: str | None,
        *,
        trust: bool,
        yes: bool,
    ) -> int:
        recorded["repo_name"] = repo_name
        recorded["language"] = language
        recorded["trust"] = trust
        recorded["yes"] = yes
        return 0

    monkeypatch.setattr(fafo, "run_git_fafo", _fake_run_git_fafo)

    with pytest.raises(SystemExit) as excinfo:
        typ.cast("cabc.Callable[[list[str]], None]", cli._fafo_app)(argv)

    assert excinfo.value.code == 0
    assert recorded == {
        "repo_name": "demo-repo",
        "language": expected_language,
        "trust": expected_trust,
        "yes": expected_yes,
    }
