"""Unit tests for shared CLI helper utilities.

Covers base-branch selection, slug validation, and the fetch-refspec
precondition used by git-donkey commands.
"""

from __future__ import annotations

import typing as typ

import pytest

from git_donkey import donkey, helpers
from tests.git_repo_helpers import repo_with_remote_default

if typ.TYPE_CHECKING:
    from pathlib import Path

_USAGE_ERROR_EXIT_CODE = 2


@pytest.mark.parametrize("base", ["main", "release/1", "refs/remotes/upstream/trunk"])
def test_choose_base_branch_preserves_explicit_base(base: str) -> None:
    """Explicit bases must not be replaced by the current branch."""
    assert donkey.choose_base_branch("feature/demo", base) == base, (
        "an explicit base must not be replaced by the current branch"
    )


def test_choose_base_branch_uses_cwd_on_dot() -> None:
    """A dot origin should select the saved CWD branch."""
    assert donkey.choose_base_branch("feature/demo", ".") == "feature/demo", (
        "expected dot origin to select the saved CWD branch"
    )


@pytest.mark.parametrize(
    ("value", "expected_exit_code"),
    [
        ("good-slug_1.2", None),
        ("bad slug", 1),
    ],
)
def test_validate_slug(value: str, expected_exit_code: int | None) -> None:
    """Validate slugs for allowed characters and error handling."""
    if expected_exit_code is None:
        assert (
            helpers.validate_slug(value, label="repo name", prefix="git-fafo") == value
        ), "expected valid slug to be returned unchanged"
    else:
        with pytest.raises(SystemExit) as excinfo:
            helpers.validate_slug(value, label="repo name", prefix="git-fafo")

        assert excinfo.value.code == expected_exit_code, (
            "expected invalid slug to exit with code 1"
        )


def test_require_fetch_refspec_accepts_a_tracking_remote(tmp_path: Path) -> None:
    """A remote that fetches into remote-tracking refs is accepted."""
    repo, _remote = repo_with_remote_default(tmp_path / "repo", tmp_path / "remote.git")

    helpers._require_fetch_refspec(repo, "origin", "git-donkey")


def test_require_fetch_refspec_refuses_a_remote_without_one(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A remote configured as ``git clone --bare`` leaves it is refused."""
    repo, _remote = repo_with_remote_default(tmp_path / "repo", tmp_path / "remote.git")
    repo.git.config("--unset-all", "remote.origin.fetch")

    with pytest.raises(SystemExit) as excinfo:
        helpers._require_fetch_refspec(repo, "origin", "git-donkey")

    assert excinfo.value.code == _USAGE_ERROR_EXIT_CODE, (
        "expected a missing refspec to exit 2"
    )
    stderr = capsys.readouterr().err
    assert "remote 'origin' has no fetch refspec" in stderr, (
        "expected the error to name the remote lacking a refspec"
    )
    assert (
        "git config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'" in stderr
    ), "expected the error to give the command that restores branch tracking"
