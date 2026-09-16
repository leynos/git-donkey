"""Unit contracts for git-donkey base selection and opt-in pull selection.

Implicit base selection is the shared principal-remote default-branch discovery;
its contracts live with the module in ``tests.unit.test_remote_default``.
"""

from __future__ import annotations

import typing as typ

import pytest
from git import Repo

from git_donkey import donkey
from tests import git_repo_helpers

if typ.TYPE_CHECKING:
    from pathlib import Path


# Exit status reserved for a command-line usage error.
_USAGE_ERROR_EXIT_CODE = 2

# The trunk the cases compare a base against. It is a commit no case's base
# resolves to, so every base that resolves at all is one that gets recorded.
_TRUNK = donkey._Trunk(ref="refs/remotes/origin/main", commit="f" * 40)


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        (donkey._PullOptions(), None),
        (donkey._PullOptions(pull_rebase=True), "--rebase"),
        (donkey._PullOptions(pull_ff=True), "--ff-only"),
    ],
)
def test_pull_mode_is_explicit(
    options: donkey._PullOptions,
    expected: str | None,
) -> None:
    """Pulling has no default mode and each opt-in selects one strategy."""
    assert donkey._pull_mode(options, no_pull=False) == expected, (
        "each option combination selects its documented mode"
    )


@pytest.mark.parametrize(
    "pull_case",
    [
        (donkey._PullOptions(pull_rebase=True, pull_ff=True), False),
        (donkey._PullOptions(pull_rebase=True), True),
        (donkey._PullOptions(pull_ff=True), True),
    ],
)
def test_conflicting_pull_options_fail_before_repository_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    pull_case: tuple[donkey._PullOptions, bool],
) -> None:
    """Conflicting flags are usage errors, even outside a repository."""
    options, no_pull = pull_case
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        donkey.run_git_donkey("feature/test", options=options, no_pull=no_pull)
    assert excinfo.value.code == _USAGE_ERROR_EXIT_CODE, (
        "conflicting options are a usage error"
    )
    assert "mutually exclusive" in capsys.readouterr().err, (
        "the error names the conflicting options"
    )
    assert not list(tmp_path.iterdir()), (
        "the failure happens before any filesystem change"
    )


def test_no_pull_remains_a_compatible_no_op() -> None:
    """The existing no-pull option retains the new non-pulling default."""
    assert donkey._pull_mode(donkey._PullOptions(), no_pull=True) is None, (
        "an explicit no-pull retains the non-pulling default"
    )


def _repository(tmp_path: Path) -> Repo:
    """Return a fresh repository with one commit on its default branch.

    The commit identity is configured in the repository itself, so the case
    neither depends on nor writes to the runner's own Git configuration.

    Returns
    -------
    Repo
        The repository, checked out on the branch ``Repo.init`` created.

    """
    repo = Repo.init(tmp_path)
    git_repo_helpers.configure_repo(repo)
    repo.git.commit("--allow-empty", "-m", "the trunk")
    return repo


def _context(tmp_path: Path) -> donkey._DonkeyContext:
    """Return a context over a fresh repository, with no remote configured."""
    return donkey._DonkeyContext(
        repo_home=_repository(tmp_path),
        remote="origin",
        branch_to_worktree={},
        worktrees_root=tmp_path / "worktrees",
    )


def test_a_base_the_repository_does_not_hold_is_not_recorded(
    tmp_path: Path,
) -> None:
    """A base that resolves to no commit is refused by Git, not by a traceback."""
    context = _context(tmp_path)

    stack = donkey._stack_context(
        context,
        trunk=_TRUNK,
        base=donkey._Base(ref="no-such-branch", commit=None),
    )

    assert stack is None, (
        "a base with no start point to freeze is one no record can be written from"
    )


def test_a_base_only_a_remote_tracking_ref_names_is_resolved(
    tmp_path: Path,
) -> None:
    """A base that is a remote-tracking ref resolves through that ref."""
    context = _context(tmp_path)
    head = context.repo_home.head.commit.hexsha
    context.repo_home.git.update_ref("refs/remotes/origin/feature", head)

    stack = donkey._stack_context(
        context,
        trunk=_TRUNK,
        base=donkey._Base(
            ref="feature",
            commit=donkey._base_commit(context, "feature"),
        ),
    )

    assert stack is not None, (
        "the branch was created from a base that exists, so the record is owed"
    )
    assert stack.parent == "feature", (
        "the parent the record names is the base the caller selected"
    )
    assert donkey._base_commit(context, "feature") == head, (
        "and the commit the record freezes is the one the remote-tracking ref names"
    )


def test_a_base_named_for_the_trunk_is_still_weighed_by_commit(
    tmp_path: Path,
) -> None:
    """A base named as the default branch is recorded when its commit differs.

    The default branch is discovered from the remote, and a local branch of that
    name is a different ref that may hold commits the remote's does not. The
    branch is then created from a commit the trunk does not have, which is what
    being stacked means here, so the record is owed rather than skipped on the
    strength of the two refs sharing a name.
    """
    context = _context(tmp_path)
    head = context.repo_home.head.commit.hexsha

    stack = donkey._stack_context(
        context,
        trunk=_TRUNK,
        base=donkey._Base(ref="main", commit=head),
    )

    assert stack is not None, (
        "a base at a commit the trunk does not have is a parent to record"
    )
    assert stack.parent == "main", (
        "the parent is the name the caller selected, which is the branch they hold"
    )
