"""Sweep-level tests for git-plonk's recorded-identity guards.

``test_plonk_worktree_adapter.py`` pins the adapter's own preflight and
deletion checks. These tests pin the wiring between the sweep and that
adapter: the recorded branch and commit must reach the preflight and the
conditional deletion, and a branch that moves after selection must survive the
whole sweep. They use a real repository so the guard is exercised against Git
rather than a double's idea of it.
"""

from __future__ import annotations

from pathlib import Path

from git import Repo

from git_donkey import helpers, plonk, plonk_records
from tests import git_repo_helpers

_BRANCH = "issue-123-fix"
_MARKER_COMMIT = "Ship feature (#123)"


def _context(tmp_path: Path) -> plonk._PlonkContext:
    """Return a context with one completed worktree and its marker on trunk."""
    repo = git_repo_helpers.seed_repo(tmp_path / "repo")
    marker_path = Path(repo.working_tree_dir or ".") / "done.txt"
    marker_path.write_text("done")
    repo.index.add(["done.txt"])
    repo.index.commit(_MARKER_COMMIT)
    trunk_ref = repo.head.commit.hexsha
    worktrees_root = tmp_path / "repo.worktrees"
    worktrees_root.mkdir()
    repo.git.worktree("add", "-q", (worktrees_root / _BRANCH).as_posix(), "-b", _BRANCH)
    return plonk._PlonkContext(
        repo_home=repo,
        stanzas=helpers._parse_worktree_porcelain(repo),
        worktrees_root=worktrees_root,
        anchor_dir=Path(repo.working_tree_dir or "."),
        trunk_ref=trunk_ref,
        invoking_worktree=None,
        remote="origin",
        branch="main",
        github_repository=None,
    )


class _RacingAdapter(plonk._GitWorktreeAdapter):
    """Commit to the candidate branch after selection, before its preflight."""

    def __init__(self, repo: Repo) -> None:
        """Start with the branch not yet moved."""
        super().__init__(repo)
        self._moved = False

    def skip_reason(
        self,
        worktree_path: Path,
        *,
        expected_branch: str | None = None,
        expected_commit: str | None = None,
    ) -> plonk_records._SkipReason | None:
        """Move the branch once, then run the real preflight."""
        if not self._moved:
            self._moved = True
            worktree_repo = Repo(worktree_path)
            later_path = Path(worktree_repo.working_tree_dir or ".") / "later.txt"
            later_path.write_text("later")
            worktree_repo.index.add(["later.txt"])
            worktree_repo.index.commit("Later work")
        return super().skip_reason(
            worktree_path,
            expected_branch=expected_branch,
            expected_commit=expected_commit,
        )


def test_branch_that_moves_after_selection_survives_the_sweep(
    tmp_path: Path,
) -> None:
    """A commit made during the sweep skips the candidate and keeps its branch."""
    context = _context(tmp_path)
    worktree_path = context.worktrees_root / _BRANCH
    adapter = _RacingAdapter(context.repo_home)

    result = plonk._run_completed_cleanup(
        context,
        plonk._PlonkMode.HARD,
        adapter,
    )

    assert result.skipped_worktrees == (
        plonk_records._SkippedWorktree(
            worktree_path, plonk_records._SkipReason.CHANGED
        ),
    ), "expected the moved branch to be reported as changed"
    assert not result.removed_worktrees, "the worktree must survive"
    assert not result.removed_branches, "the branch must survive"
    assert _BRANCH in context.repo_home.heads, "the branch ref is still present"
    assert worktree_path.is_dir(), "the worktree directory is still present"


def test_sweep_records_the_selected_commit_before_cleanup(tmp_path: Path) -> None:
    """Selection must record the branch tip the guards compare against."""
    context = _context(tmp_path)
    expected_commit = context.repo_home.commit(_BRANCH).hexsha

    candidates = plonk._donkey_worktree_candidates(
        context.stanzas,
        context.worktrees_root,
        context.anchor_dir,
    )

    assert [candidate.commit_id for candidate in candidates] == [expected_commit], (
        "expected the candidate to carry the commit the worktree has checked out"
    )
