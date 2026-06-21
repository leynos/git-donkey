"""Unit tests for the ``git-fafo`` command-line interface.

These tests verify the Cyclopts wrapper in ``git_donkey.cli`` passes parsed
arguments through to ``git_donkey.fafo.run_git_fafo`` without exercising the
GitHub or Git orchestration layers.
"""

from __future__ import annotations

import dataclasses
import typing as typ

import pytest

from git_donkey import cli, fafo

if typ.TYPE_CHECKING:
    import collections.abc as cabc


@dataclasses.dataclass(frozen=True, slots=True)
class _ExpectedOptions:
    """Expected scaffold option values for a single parametrised case."""

    language: str | None
    trust: bool
    yes: bool


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        pytest.param(
            ["demo-repo", "python", "--trust"],
            _ExpectedOptions(
                language="python",
                trust=True,
                yes=False,
            ),
            id="trust-option",
        ),
        pytest.param(
            ["demo-repo"],
            _ExpectedOptions(
                language=None,
                trust=False,
                yes=False,
            ),
            id="missing-language",
        ),
        pytest.param(
            ["demo-repo", "-y"],
            _ExpectedOptions(
                language=None,
                trust=False,
                yes=True,
            ),
            id="yes-short-option",
        ),
    ],
)
def test_fafo_cli_passes_scaffold_options(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    expected: _ExpectedOptions,
) -> None:
    """CLI scaffold options should pass through to the workflow function."""
    recorded: dict[str, object] = {}

    def _fake_run_git_fafo(
        repo_name: str,
        language: str | None,
        *,
        token: str,
        options: fafo._FafoOptions,
    ) -> int:
        recorded["repo_name"] = repo_name
        recorded["language"] = language
        recorded["token"] = token
        recorded["trust"] = options.trust
        recorded["yes"] = options.yes
        return 0

    auth_value = "fake-token"
    monkeypatch.setattr(fafo, "_github_token", lambda: auth_value)
    monkeypatch.setattr(fafo, "run_git_fafo", _fake_run_git_fafo)

    with pytest.raises(SystemExit) as excinfo:
        typ.cast("cabc.Callable[[list[str]], None]", cli._fafo_app)(argv)

    assert excinfo.value.code == 0, "git-fafo CLI should exit successfully"
    assert recorded == {
        "repo_name": "demo-repo",
        "language": expected.language,
        "token": auth_value,
        "trust": expected.trust,
        "yes": expected.yes,
    }, "git-fafo CLI should pass parsed scaffold options to run_git_fafo"
