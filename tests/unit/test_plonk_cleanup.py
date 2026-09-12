"""Unit tests for the ``git-plonk`` completed-cleanup workflow.

One sweep walks the completed candidates and turns each into an outcome: a
worktree Git removed, one the preflight or Git itself refused, a branch that
went with its worktree, and a branch Git would not delete. These tests pin that
workflow — the skip that keeps one dirty worktree from abandoning the batch, the
hard-mode branch rule, dry runs that plan without mutating, the failures that
are reported rather than fatal, and the bounded record each step leaves behind.
The adapter's own contract against Git is covered in
``test_plonk_worktree_adapter.py``, and the soft pass in
``test_plonk_soft_mode.py``.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path
from types import SimpleNamespace

import pytest

from git_donkey import plonk, plonk_records

if typ.TYPE_CHECKING:
    from git import Repo

    from tests.observability_helpers import RecordingRecorder

# Trunk ref shape a real run resolves: the fetched remote-tracking ref of the
# default branch the principal remote advertises.
_TRUNK_REF = "refs/remotes/origin/main"

# The branch and directory ``git donkey`` would have created for the candidate
# worktrees these tests exercise.
_WORKTREE_BRANCH = "issue-123-fix"


class _FailingGitAdapter:
    """Git adapter double that plans cleanup but refuses to mutate.

    Dry-run cleanup must reuse candidate discovery while skipping destructive
    Git APIs, so ``remove_worktree`` and ``delete_branch`` fail the test if the
    planner ever invokes them. History reports the candidate under test as
    complete, so the planner reaches the mutations it must not perform.
    """

    @staticmethod
    def history_messages(ref: str) -> typ.Iterator[str]:
        """Assert the resolved trunk ref, then yield the completion marker."""
        assert ref == _TRUNK_REF, "expected configured trunk ref"
        yield _marker_for(_candidate(_WORKTREE_BRANCH, 123))

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


class _RecordingGitAdapter:
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
        assert ref == _TRUNK_REF, "expected configured trunk ref"
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
        assert branch_name in removed_branches, (
            "hard mode deletes a branch only once its worktree is gone"
        )
        if branch_name in self.deletion_failures:
            return False
        self.deleted.append(branch_name)
        return True


def _candidate(branch_name: str, issue_number: int) -> plonk_records._PlonkCandidate:
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


def _marker_for(candidate: plonk_records._PlonkCandidate) -> str:
    """Return the trunk history line that marks ``candidate`` complete."""
    return f"Merge pull request {candidate.marker}"


def _context(
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
                "branch": f"refs/heads/{candidate.branch_name}",
                "worktree": candidate.worktree_path,
            }
            for candidate in candidates
        ],
        worktrees_root=Path("/repo.worktrees"),
        trunk_ref=_TRUNK_REF,
        invoking_worktree=None,
    )


def _cleanup(
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
        _context(candidates),
        mode,
        typ.cast("plonk._GitWorktreeAdapter", adapter),
        dry_run=dry_run,
    )


def test_dirty_candidate_does_not_abandon_its_clean_siblings() -> None:
    """A skipped worktree should not stop the batch, or the sweep is pointless."""
    dirty = _candidate("issue-456-dirty", 456)
    clean = _candidate("issue-123-clean", 123)
    adapter = _RecordingGitAdapter(
        [_marker_for(dirty), _marker_for(clean)],
        skip_reasons={dirty.worktree_path: plonk._SkipReason.DIRTY},
    )

    result = _cleanup(
        [dirty, clean],
        adapter,
        mode=plonk._PlonkMode.DEFAULT,
    )

    assert result.removed_worktrees == (clean.worktree_path,), (
        "the clean worktree is removed even though its sibling was skipped"
    )
    assert result.skipped_worktrees == (
        plonk._SkippedWorktree(dirty.worktree_path, plonk._SkipReason.DIRTY),
    ), "the dirty worktree is reported with the reason it was left alone"
    assert adapter.removed == [clean.worktree_path], (
        "the dirty worktree is never handed to Git for removal"
    )


def test_hard_mode_keeps_the_branch_of_a_skipped_worktree() -> None:
    """Hard mode deletes completed branches, but only those whose worktree went."""
    clean = _candidate("issue-123-clean", 123)
    dirty = _candidate("issue-456-dirty", 456)
    adapter = _RecordingGitAdapter(
        [_marker_for(clean), _marker_for(dirty)],
        skip_reasons={dirty.worktree_path: plonk._SkipReason.DIRTY},
    )

    result = _cleanup([clean, dirty], adapter, mode=plonk._PlonkMode.HARD)

    assert result.removed_branches == (clean.branch_name,), (
        "only the branch whose worktree was actually removed is deleted"
    )
    assert adapter.deleted == [clean.branch_name], (
        "the skipped worktree keeps the branch that holds its uncommitted work"
    )


def test_dry_run_reports_skips_without_mutating() -> None:
    """A preview should name the worktrees a real run would leave alone."""
    clean = _candidate("issue-123-clean", 123)
    gone = _candidate("issue-456-gone", 456)
    adapter = _RecordingGitAdapter(
        [_marker_for(clean), _marker_for(gone)],
        skip_reasons={gone.worktree_path: plonk._SkipReason.UNAVAILABLE},
    )

    result = _cleanup(
        [clean, gone],
        adapter,
        mode=plonk._PlonkMode.HARD,
        dry_run=True,
    )

    assert result.is_dry_run, "expected dry-run result marker"
    assert result.removed_worktrees == (clean.worktree_path,), (
        "the clean worktree is planned for removal"
    )
    assert result.removed_branches == (clean.branch_name,), (
        "the clean branch is planned for deletion in hard mode"
    )
    assert result.skipped_worktrees == (
        plonk._SkippedWorktree(gone.worktree_path, plonk._SkipReason.UNAVAILABLE),
    ), "the missing worktree is reported as skipped"
    assert not adapter.removed, "a dry run removes no worktree"
    assert not adapter.deleted, "a dry run deletes no branch"


def test_failed_removal_is_skipped_and_the_batch_continues() -> None:
    """A removal Git refuses should report a skip, not abandon the sweep."""
    stubborn = _candidate("issue-456-stubborn", 456)
    clean = _candidate("issue-123-clean", 123)
    adapter = _RecordingGitAdapter(
        [_marker_for(stubborn), _marker_for(clean)],
        removal_failures=[stubborn.worktree_path],
    )

    result = _cleanup([stubborn, clean], adapter, mode=plonk._PlonkMode.HARD)

    assert result.removed_worktrees == (clean.worktree_path,), (
        "the following clean candidate is still removed"
    )
    assert result.removed_branches == (clean.branch_name,), (
        "a branch is only deleted once its worktree is gone"
    )
    assert result.skipped_worktrees == (
        plonk._SkippedWorktree(
            stubborn.worktree_path, plonk._SkipReason.REMOVAL_FAILED
        ),
    ), "the failed removal is reported with its own reason"


@pytest.mark.parametrize(
    ("mode", "expected_branches"),
    [
        (plonk._PlonkMode.DEFAULT, ()),
        (plonk._PlonkMode.HARD, ("issue-123-fix",)),
    ],
)
def test_dry_run_completed_mode_reports_plans_without_mutating(
    mode: plonk._PlonkMode,
    expected_branches: tuple[str, ...],
) -> None:
    """Dry-run completed cleanup should plan work without destructive Git APIs."""
    candidate = _candidate(_WORKTREE_BRANCH, 123)

    result = _cleanup([candidate], _FailingGitAdapter(), mode=mode, dry_run=True)

    assert result.mode is mode, "expected completed dry-run mode to be preserved"
    assert result.is_dry_run, "expected dry-run result marker"
    assert result.removed_worktrees == (candidate.worktree_path,), (
        "expected planned worktree removal"
    )
    assert result.removed_branches == expected_branches, (
        f"expected planned branch deletion for {mode.value} mode"
    )


def test_failed_branch_deletion_is_reported_and_the_batch_continues() -> None:
    """A branch Git refuses to delete should be reported, not fatal."""
    stubborn = _candidate("issue-456-stubborn", 456)
    clean = _candidate("issue-123-clean", 123)
    adapter = _RecordingGitAdapter(
        [_marker_for(stubborn), _marker_for(clean)],
        deletion_failures=[stubborn.branch_name],
    )

    result = _cleanup([stubborn, clean], adapter, mode=plonk._PlonkMode.HARD)

    assert result.removed_worktrees == (stubborn.worktree_path, clean.worktree_path), (
        "both worktrees are still removed"
    )
    assert result.failed_branch_deletions == (stubborn.branch_name,), (
        "the surviving branch is reported as a failed deletion"
    )
    assert result.removed_branches == (clean.branch_name,), (
        "a branch is only reported as removed once Git deleted it"
    )
    assert not result.skipped_worktrees, (
        "a failed branch deletion is not a skipped worktree"
    )
    assert adapter.deleted == [clean.branch_name], (
        "the following candidate's branch is still deleted"
    )


def test_skipped_candidate_records_its_bounded_reason(
    recording_recorder: RecordingRecorder,
) -> None:
    """A skipped worktree should record why the preflight refused it."""
    dirty = _candidate("issue-456-dirty", 456)
    adapter = _RecordingGitAdapter(
        [_marker_for(dirty)],
        skip_reasons={dirty.worktree_path: plonk._SkipReason.DIRTY},
    )

    _cleanup([dirty], adapter, mode=plonk._PlonkMode.DEFAULT)

    assert len(recording_recorder.observations) == 1, (
        "a skipped candidate records the preflight and nothing else"
    )
    record = recording_recorder.observations[0]
    assert record.operation == "worktree_preflight", "the preflight records the skip"
    assert record.outcome == "skipped", "the skip is an outcome of its own"
    assert record.skip_reason == "dirty", "the reason is a bounded label"
    assert record.mode == "default", "the record names the mode the sweep ran in"
    assert recording_recorder.unbounded_values() == set(), (
        "every recorded value comes from a declared vocabulary"
    )


def test_refused_removal_and_deletion_record_bounded_failures(
    recording_recorder: RecordingRecorder,
) -> None:
    """Each refused Git command should record a failure with its error class."""
    stubborn = _candidate("issue-456-stubborn", 456)
    branchless = _candidate("issue-789-branchless", 789)
    adapter = _RecordingGitAdapter(
        [_marker_for(stubborn), _marker_for(branchless)],
        removal_failures=[stubborn.worktree_path],
        deletion_failures=[branchless.branch_name],
    )

    _cleanup([stubborn, branchless], adapter, mode=plonk._PlonkMode.HARD)

    assert recording_recorder.outcomes("worktree_removal") == ["failure", "success"], (
        "the refused removal is recorded before the removal that succeeded"
    )
    assert recording_recorder.error_kinds("worktree_removal") == [
        "git_command_error"
    ], "the failed removal names the error class"
    assert recording_recorder.outcomes("branch_deletion") == ["failure"], (
        "the refused deletion records a failure"
    )
    assert recording_recorder.error_kinds("branch_deletion") == ["git_command_error"], (
        "the failed deletion names the error class"
    )
    assert recording_recorder.unbounded_values() == set(), (
        "every recorded value comes from a declared vocabulary"
    )
    assert recording_recorder.leaked_details(("/repo.worktrees/", "issue-")) == set(), (
        "no path or branch name reaches the bounded vocabulary"
    )


@pytest.mark.parametrize(
    ("failed_branch_deletions", "expected_exit_code"),
    [
        pytest.param((), 0, id="complete-sweep"),
        pytest.param(("issue-123-fix",), 1, id="surviving-branch"),
    ],
)
def test_run_git_plonk_reports_partial_sweeps(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failed_branch_deletions: tuple[str, ...],
    expected_exit_code: int,
) -> None:
    """A branch the sweep could not delete should reach the caller's exit code."""
    result = plonk._PlonkResult(
        mode=plonk._PlonkMode.HARD,
        removed_worktrees=(Path("/repo.worktrees/issue-123-fix"),),
        failed_branch_deletions=failed_branch_deletions,
    )
    monkeypatch.setattr(plonk, "_load_plonk_context", lambda: None)
    monkeypatch.setattr(
        plonk, "_run_completed_cleanup", lambda *_args, **_kwargs: result
    )

    exit_code = plonk.run_git_plonk(hard=True)

    assert exit_code == expected_exit_code, "the sweep reports what it left behind"
    summary = capsys.readouterr().out
    assert ("Failed branch deletions:" in summary) is bool(failed_branch_deletions), (
        "the summary names the branch that survived, and only then"
    )
