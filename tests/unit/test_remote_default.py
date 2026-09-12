"""Unit tests for the shared principal-remote default-branch discovery.

`git donkey` and `git plonk` resolve the repository's trunk through
``git_donkey.remote_default``. These tests pin the advertisement parsing, the
principal-remote selection rule, the caller-supplied advice that discovery adds
when no default branch is advertised, and the ref the explicit fetch returns.
"""

from __future__ import annotations

import typing as typ

import pytest
from git import Repo

from git_donkey import remote_default
from tests import git_repo_helpers

if typ.TYPE_CHECKING:
    from pathlib import Path

# Exit status ``helpers._die`` uses when the command cannot run at all, which a
# missing remote is: the command rejects its own precondition rather than
# falling back to a local branch.
_USAGE_ERROR_EXIT_CODE = 2

# Exit status for a remote that cannot be queried or cannot supply a branch.
_DISCOVERY_FAILURE_EXIT_CODE = 1


@pytest.mark.parametrize(
    ("advertisement", "expected"),
    [
        ("ref: refs/heads/trunk\tHEAD\nabc\tHEAD", "trunk"),
        ("ref: refs/heads/release/stable\tHEAD", "release/stable"),
        ("abc\tHEAD", None),
        ("", None),
        ("ref: refs/tags/v1\tHEAD", None),
        ("ref: refs/heads/main\tother", None),
        ("garbage refs/heads/main HEAD", None),
        ("ref: refs/heads/main HEAD extra", None),
    ],
)
def test_advertised_default_branch(
    advertisement: str,
    expected: str | None,
) -> None:
    """Only the advertised HEAD branch supplies an implicit base."""
    assert remote_default.advertised_default_branch(advertisement) == expected, (
        "only a symbolic HEAD ref names the default branch"
    )


def test_principal_remote_is_the_first_configured_remote(tmp_path: Path) -> None:
    """The principal remote should stay the first configured remote."""
    repo = git_repo_helpers.seed_repo(tmp_path / "local")
    repo.create_remote("upstream", "https://example.invalid/one.git")
    repo.create_remote("origin", "https://example.invalid/two.git")

    assert remote_default.principal_remote(repo, "git-plonk") == "upstream", (
        "the first configured remote is the principal remote"
    )


def test_principal_remote_requires_a_remote(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A repository with no remote should fail, naming the calling command."""
    repo = git_repo_helpers.seed_repo(tmp_path / "local")

    with pytest.raises(SystemExit) as exc_info:
        remote_default.principal_remote(repo, "git-plonk")

    assert exc_info.value.code == _USAGE_ERROR_EXIT_CODE, (
        "a repository with no remote cannot run the command"
    )
    assert "git-plonk: no remotes configured" in capsys.readouterr().err, (
        "the failure names the calling command"
    )


def test_discover_default_branch_reads_the_advertised_branch(tmp_path: Path) -> None:
    """Discovery should return the branch the remote advertises."""
    repo, _remote_repo = git_repo_helpers.repo_with_remote_default(
        tmp_path / "local", tmp_path / "remote.git", default_branch="trunk"
    )

    assert remote_default.discover_default_branch(repo, "origin", "git-donkey") == (
        "trunk"
    ), "discovery returns the advertised branch name"


def test_missing_default_appends_the_caller_advice(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A remote with an unborn HEAD should fail with the caller's remedy."""
    remote_path = tmp_path / "remote.git"
    Repo.init(remote_path, bare=True)
    repo = git_repo_helpers.seed_repo(tmp_path / "local")
    repo.create_remote("origin", remote_path.as_posix())

    with pytest.raises(SystemExit) as exc_info:
        remote_default.discover_default_branch(
            repo,
            "origin",
            "git-donkey",
            missing_advice="specify a base branch explicitly",
        )

    assert exc_info.value.code == _DISCOVERY_FAILURE_EXIT_CODE, (
        "an unadvertised default is an error"
    )
    assert (
        "remote 'origin' does not advertise a default branch; "
        "specify a base branch explicitly" in capsys.readouterr().err
    ), "the advice the calling command supplied is appended"


def test_fetch_default_branch_ref_returns_the_tracking_ref(tmp_path: Path) -> None:
    """The explicit fetch should resolve and populate the remote-tracking ref."""
    repo, _remote_repo = git_repo_helpers.repo_with_remote_default(
        tmp_path / "local", tmp_path / "remote.git"
    )

    remote_ref = remote_default.fetch_default_branch_ref(
        repo, "origin", "main", "git-plonk"
    )

    assert remote_ref == "refs/remotes/origin/main", (
        "the fully qualified remote-tracking ref is returned"
    )
    assert repo.commit(remote_ref).hexsha == repo.commit("refs/heads/main").hexsha, (
        "the fetched ref points at the remote commit"
    )
