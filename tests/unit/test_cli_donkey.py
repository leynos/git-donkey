"""Exercise the actual Cyclopts parser for git-donkey pull options."""

from __future__ import annotations

import typing as typ

import pytest

from git_donkey import cli, donkey

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path


# Exit status reserved for a command-line usage error.
_USAGE_ERROR_EXIT_CODE = 2


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["feature/test"], (None, False, donkey._PullOptions())),
        (["feature/test", "."], (".", False, donkey._PullOptions())),
        (["feature/test", "--no-pull"], (None, True, donkey._PullOptions())),
        (
            ["feature/test", "--pull-rebase"],
            (None, False, donkey._PullOptions(pull_rebase=True)),
        ),
        (
            ["feature/test", "release/1", "--pull-ff"],
            ("release/1", False, donkey._PullOptions(pull_ff=True)),
        ),
    ],
)
def test_donkey_cli_passes_pull_options(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    expected: tuple[str | None, bool, donkey._PullOptions],
) -> None:
    """Flat user-facing flags reach the workflow without implicit pulling."""
    recorded: list[object] = []

    def _fake_run(
        branch_name: str,
        origin_branch: str | None,
        *,
        no_pull: bool,
        options: donkey._PullOptions,
    ) -> int:
        """Capture the parser's workflow arguments."""
        recorded.extend((branch_name, origin_branch, no_pull, options))
        return 0

    monkeypatch.setattr(donkey, "run_git_donkey", _fake_run)
    with pytest.raises(SystemExit) as excinfo:
        typ.cast("cabc.Callable[[list[str]], None]", cli._donkey_app)(argv)
    assert excinfo.value.code == 0, "a valid invocation exits successfully"
    assert recorded == ["feature/test", *expected], (
        "the parser forwards the branch, base, no-pull flag, and pull options"
    )


@pytest.mark.parametrize(
    "flags",
    [
        ["--pull-rebase", "--pull-ff"],
        ["--pull-rebase", "--no-pull"],
        ["--pull-ff", "--no-pull"],
    ],
)
def test_donkey_cli_rejects_conflicting_pull_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    flags: list[str],
) -> None:
    """Contradictory CLI flags fail before discovery or filesystem changes."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        typ.cast("cabc.Callable[[list[str]], None]", cli._donkey_app)([
            "feature/test",
            *flags,
        ])
    assert excinfo.value.code == _USAGE_ERROR_EXIT_CODE, (
        "conflicting pull flags are a usage error"
    )
    assert "mutually exclusive" in capsys.readouterr().err, (
        "the error names the offending options"
    )
    assert not list(tmp_path.iterdir()), (
        "the failure happens before any filesystem change"
    )
