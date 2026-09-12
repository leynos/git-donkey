"""Doubles and builders shared by the ``git-plonk`` completed-cleanup suites.

The cleanup workflow is exercised twice over: ``test_plonk_cleanup.py`` pins the
contract with readable one- and two-candidate examples, and
``test_plonk_cleanup_properties.py`` generalises the same rules over generated
batches. Both compose one Git double, so the rule the double enforces — hard
mode deletes a branch only once its worktree is gone — holds for every example
and every generated batch alike, and a change to how a candidate is described
cannot leave the two suites asserting against differently shaped adapters.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path
from types import SimpleNamespace

import pytest

from git_donkey import plonk, plonk_records

if typ.TYPE_CHECKING:
    from git import Repo

# Trunk ref shape a real run resolves: the fetched remote-tracking ref of the
# default branch the principal remote advertises.
TRUNK_REF = "refs/remotes/origin/main"

# The branch and directory ``git donkey`` would have created for the candidate
# worktrees the examples exercise.
WORKTREE_BRANCH = "issue-123-fix"


class FailingGitAdapter:
    """Git adapter double that plans cleanup but refuses to mutate.

    Dry-run cleanup must reuse candidate discovery while skipping destructive
    Git APIs, so ``remove_worktree`` and ``delete_branch`` fail the test if the
    planner ever invokes them. History reports the candidate under test as
    complete, so the planner reaches the mutations it must not perform.
    """

    @staticmethod
    def history_messages(ref: str) -> typ.Iterator[str]:
        """Assert the resolved trunk ref, then yield the completion marker."""
        if ref != TRUNK_REF:
            msg = "expected configured trunk ref"
            raise AssertionError(msg)
        yield marker_for(candidate(WORKTREE_BRANCH, 123))

    @staticmethod
    def skip_reason(worktree_path: Path) -> plonk._SkipReason | None:
        """Report every candidate as clean, so planning reaches the mutations."""
        return None

    @staticmethod
    def remove_worktree(worktree_path: Path) -> bool:
        """Fail the test unconditionally — dry runs must not remove worktrees."""
        pytest.fail(f"dry run should not remove worktree {worktree_path}")

    @staticmethod
    def delete_branch(branch_name: str) -> bool:
        """Fail the test unconditionally — dry runs must not delete branches."""
        pytest.fail(f"dry run should not delete branch {branch_name}")


class RecordingGitAdapter:
    """Git adapter double that records removals instead of performing them.

    Trunk history is driven by the ``markers`` the double yields and each
    candidate's state by ``skip_reasons``, so one batch can mix worktrees Git
    would remove with worktrees it would refuse. ``removal_failures`` models a
    worktree that passes the preflight but whose removal still fails, and
    ``deletion_failures`` a branch Git refuses to delete.
    """

    def __init__(
        self,
        markers: typ.Iterable[str],
        *,
        skip_reasons: dict[Path, plonk._SkipReason] | None = None,
        removal_failures: typ.Iterable[Path] = (),
        deletion_failures: typ.Iterable[str] = (),
    ) -> None:
        self._markers = tuple(markers)
        self.skip_reasons = dict(skip_reasons or {})
        self.removal_failures = set(removal_failures)
        self.deletion_failures = set(deletion_failures)
        self.removed: list[Path] = []
        self.deleted: list[str] = []

    def history_messages(self, ref: str) -> typ.Iterator[str]:
        """Assert the resolved trunk ref, then yield the configured history."""
        if ref != TRUNK_REF:
            msg = "expected configured trunk ref"
            raise AssertionError(msg)
        yield from self._markers

    def skip_reason(self, worktree_path: Path) -> plonk._SkipReason | None:
        """Return the configured reason for ``worktree_path``, if it has one."""
        if worktree_path in self.skip_reasons:
            return self.skip_reasons[worktree_path]
        return None

    def remove_worktree(self, worktree_path: Path) -> bool:
        """Record a removal request, reporting success unless told to fail."""
        if worktree_path in self.removal_failures:
            return False
        self.removed.append(worktree_path)
        return True

    def delete_branch(self, branch_name: str) -> bool:
        """Record a branch deletion, which must follow its worktree's removal."""
        removed_branches = [path.name for path in self.removed]
        if branch_name not in removed_branches:
            msg = "hard mode deletes a branch only once its worktree is gone"
            raise AssertionError(msg)
        if branch_name in self.deletion_failures:
            return False
        self.deleted.append(branch_name)
        return True


def candidate(branch_name: str, issue_number: int) -> plonk_records._PlonkCandidate:
    """Return the candidate git donkey creates for ``branch_name``.

    Parameters
    ----------
    branch_name : str
        Branch, and worktree directory name, of the candidate.
    issue_number : int
        Issue number the branch marker refers to.

    Returns
    -------
    plonk_records._PlonkCandidate
        The candidate, marked complete by the matching issue marker.

    """
    return plonk_records._PlonkCandidate(
        branch_name=branch_name,
        worktree_path=Path(f"/repo.worktrees/{branch_name}"),
        marker=f"(#{issue_number})",
    )


def marker_for(candidate: plonk_records._PlonkCandidate) -> str:
    """Return the trunk history line that marks ``candidate`` complete."""
    return f"Merge pull request {candidate.marker}"


def cleanup_context(
    candidates: typ.Iterable[plonk_records._PlonkCandidate],
) -> plonk_records._PlonkContext:
    """Return the repository state a completed cleanup of ``candidates`` sees.

    Parameters
    ----------
    candidates : collections.abc.Iterable[plonk_records._PlonkCandidate]
        Candidates whose worktree stanzas the context reports.

    Returns
    -------
    plonk_records._PlonkContext
        Context holding one stanza per candidate and no invoking worktree, so
        every candidate is eligible for cleanup.

    """
    return plonk_records._PlonkContext(
        repo_home=typ.cast("Repo", SimpleNamespace()),
        stanzas=[
            {
                "branch": f"refs/heads/{one.branch_name}",
                "worktree": one.worktree_path,
            }
            for one in candidates
        ],
        worktrees_root=Path("/repo.worktrees"),
        trunk_ref=TRUNK_REF,
        invoking_worktree=None,
    )


def run_cleanup(
    candidates: typ.Iterable[plonk_records._PlonkCandidate],
    adapter: object,
    *,
    mode: plonk._PlonkMode,
    dry_run: bool = False,
) -> plonk._PlonkResult:
    """Run completed cleanup for ``candidates`` against an adapter double.

    Parameters
    ----------
    candidates : collections.abc.Iterable[plonk_records._PlonkCandidate]
        Candidates to clean up.
    adapter : object
        Any double exposing the adapter's history, skip, and mutation surface.
    mode : plonk._PlonkMode
        Cleanup mode to run.
    dry_run : bool, optional
        Whether to plan the work without mutating the adapter.

    Returns
    -------
    plonk._PlonkResult
        What the run reports it removed and skipped.

    """
    return plonk._run_completed_cleanup(
        cleanup_context(candidates),
        mode,
        typ.cast("plonk._GitWorktreeAdapter", adapter),
        dry_run=dry_run,
    )
