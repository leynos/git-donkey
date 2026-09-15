"""The worktree reader: what the working tree holding a branch is stopped in.

The read-only port's second half answers a question about the worktree Git
listed, and the answer has to come from that worktree rather than from the
checkout the run was started in — the two are one directory for the main
checkout and different directories for every linked worktree. Git may write a
linked worktree's Git directory into its ``.git`` entry either way, and the
relative form is the one a reading can get wrong by starting from the wrong
directory, so that is the shape the case here builds.

The worktree is stopped in a rebase on purpose: a worktree in the middle of an
operation has detached from its branch, so the porcelain listing names no
branch for it, and the file the operation wrote is the only thing that says
which branch it left — which makes the read through the ``.git`` entry the one
thing between the run and an idle worktree reported for a rebase in progress.

Every commit here is made through Git itself rather than through GitPython's
ref API, because that API resolves a relative Git directory against the
directory the test process happens to run in — the same mistake the reader
under test must not make, and one that would fail the case for the wrong
reason.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest
from git import Repo

from git_donkey import wheresat_records, wheresat_worktrees
from tests import git_repo_helpers

pytestmark = pytest.mark.timeout(120)

_BRANCH: typ.Final = "issue-77-child"
"""Branch the linked worktree has checked out, and the rebase detaches from."""

_TRUNK: typ.Final = "main"
"""Branch the seed commit is on, and the rebase replays the child onto."""

_TRACKED: typ.Final = "README.md"
"""Tracked file both sides rewrite, so the replay cannot apply cleanly."""


def _relative_worktree(repo: Repo, root: Path) -> Path:
    """Add a linked worktree Git records a relative Git directory for.

    Parameters
    ----------
    repo : Repo
        Repository the worktree is added to.
    root : Path
        Directory to create the worktree under.

    Returns
    -------
    pathlib.Path
        The worktree's working directory.

    """
    worktree = root / "linked"
    repo.git.worktree("add", "--relative-paths", str(worktree), "-b", _BRANCH)
    return worktree


def _commit(repo: Repo, root: Path, text: str, message: str) -> None:
    """Rewrite the tracked file in ``root`` and commit it.

    Parameters
    ----------
    repo : Repo
        Repository to commit in, which is read from the working tree Git is
        run in rather than from any ref this process resolves itself.
    root : Path
        Working tree holding the file.
    text : str
        Contents to write to the tracked file.
    message : str
        Commit message.

    """
    (root / _TRACKED).write_text(text)
    repo.git.add(_TRACKED)
    repo.git.commit("-m", message)


def _stop_a_rebase(repo: Repo, worktree: Path) -> None:
    """Rewrite a tracked file on both sides, leaving the rebase stopped.

    Parameters
    ----------
    repo : Repo
        Repository whose checkout advances the trunk the child is replayed on.
    worktree : Path
        Working directory of the child's worktree, where the rebase is run.

    """
    trunk_root = Path(typ.cast("str", repo.working_dir))
    _commit(Repo(worktree), worktree, "child side\n", "Child rewrites it")
    _commit(repo, trunk_root, "trunk side\n", "Trunk rewrites it")

    status, _, stderr = Repo(worktree).git.rebase(
        _TRUNK, with_extended_output=True, with_exceptions=False
    )
    assert status != 0, f"expected the rebase to conflict, but it did not: {stderr}"


def test_a_relative_git_directory_is_read_where_the_worktree_lives(
    tmp_path: Path,
) -> None:
    """A relative Git directory is read from its own worktree, not from here.

    Read from the run's own directory, the entry names no Git directory at all,
    the branch the operation left is never found, and a worktree stopped in a
    rebase is reported as an idle one — which is the reading the report's
    warning about a replay rests on.
    """
    repo = git_repo_helpers.seed_repo(tmp_path / "repo")
    worktree = _relative_worktree(repo, tmp_path)
    _stop_a_rebase(repo, worktree)
    entry = (worktree / ".git").read_text(encoding="utf-8").strip()

    assert entry.startswith("gitdir: ../"), (
        f"the case needs a relative Git directory, but Git wrote {entry!r}"
    )

    state = wheresat_worktrees.worktree_state(repo, _BRANCH)

    assert state.operation is wheresat_records.GitOperation.REBASE, (
        "the worktree the stopped rebase detached from is found through the "
        "relative Git directory its own .git entry names"
    )
    assert state.dirty, "and the conflict the rebase stopped on is uncommitted work"
