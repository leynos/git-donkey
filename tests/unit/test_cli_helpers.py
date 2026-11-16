"""Unit tests for shared CLI helper utilities.

Covers base-branch selection, slug validation, and the fetch-refspec
precondition used by git-donkey commands.
"""

from __future__ import annotations

import typing as typ

import pytest

from git_donkey import donkey, fetch_refspec, helpers
from tests.git_repo_helpers import (
    FETCH_ALL_BRANCHES_REFSPEC,
    corrupt_config,
    repo_with_remote_default,
)

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


def test_fetch_refspec_refusal_accepts_a_tracking_remote() -> None:
    """A remote that fetches into remote-tracking refs has no refusal message."""
    assert (
        fetch_refspec.refusal_message("origin", [FETCH_ALL_BRANCHES_REFSPEC]) is None
    ), "a configured refspec must not produce a refusal"


def test_fetch_refspec_refusal_names_the_restoring_command() -> None:
    """A remote without a refspec is refused with a repair command."""
    message = fetch_refspec.refusal_message("origin", [])

    assert message is not None, "a bare remote must be refused"
    assert "remote 'origin' has no fetch refspec" in message, (
        "expected the refusal to name the remote lacking a refspec"
    )
    assert (
        "git config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'"
        in message
    ), "expected the refusal to give the command that restores branch tracking"


def test_read_fetch_refspecs_returns_the_configured_values(tmp_path: Path) -> None:
    """The adapter reads the refspec values the pure rule consumes."""
    repo, _remote = repo_with_remote_default(tmp_path / "repo", tmp_path / "remote.git")

    assert helpers._read_fetch_refspecs(repo, "origin") == [
        FETCH_ALL_BRANCHES_REFSPEC
    ], "expected the configured refspec to be returned"


def test_read_fetch_refspecs_returns_empty_when_the_key_is_unset(
    tmp_path: Path,
) -> None:
    """An unset key is an empty refspec list, not a read failure."""
    repo, _remote = repo_with_remote_default(tmp_path / "repo", tmp_path / "remote.git")
    repo.git.config("--unset-all", "remote.origin.fetch")

    assert helpers._read_fetch_refspecs(repo, "origin") == [], (
        "an unset fetch key yields no refspecs"
    )


def test_read_fetch_refspecs_reports_unreadable_configuration(
    tmp_path: Path,
) -> None:
    """A failure other than the key being unset is reported separately."""
    repo, _remote = repo_with_remote_default(tmp_path / "repo", tmp_path / "remote.git")
    corrupt_config(repo)

    with pytest.raises(fetch_refspec.FetchRefspecError) as excinfo:
        helpers._read_fetch_refspecs(repo, "origin")

    assert "could not read remote.origin.fetch" in str(excinfo.value), (
        "expected unreadable configuration to be named as such"
    )


def test_donkey_require_fetch_refspec_accepts_a_tracking_remote(
    tmp_path: Path,
) -> None:
    """The command boundary accepts a remote that fetches tracking refs."""
    repo, _remote = repo_with_remote_default(tmp_path / "repo", tmp_path / "remote.git")

    donkey._require_fetch_refspec(repo, "origin")


def test_donkey_require_fetch_refspec_refuses_a_remote_without_one(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A remote configured as ``git clone --bare`` leaves it is refused."""
    repo, _remote = repo_with_remote_default(tmp_path / "repo", tmp_path / "remote.git")
    repo.git.config("--unset-all", "remote.origin.fetch")

    with pytest.raises(SystemExit) as excinfo:
        donkey._require_fetch_refspec(repo, "origin")

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


def test_donkey_require_fetch_refspec_reports_unreadable_configuration(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Unreadable configuration exits as its own error, not as a missing rule."""
    repo, _remote = repo_with_remote_default(tmp_path / "repo", tmp_path / "remote.git")
    corrupt_config(repo)

    with pytest.raises(SystemExit) as excinfo:
        donkey._require_fetch_refspec(repo, "origin")

    assert excinfo.value.code == _USAGE_ERROR_EXIT_CODE, (
        "expected unreadable configuration to exit 2"
    )
    stderr = capsys.readouterr().err
    assert "could not read remote.origin.fetch" in stderr, (
        "expected the error to report the unreadable configuration"
    )
    assert "has no fetch refspec" not in stderr, (
        "an unreadable value must not be reported as a missing refspec"
    )
