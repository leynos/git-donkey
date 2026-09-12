"""Unit tests for the ``git-plonk`` completed-cleanup workflow.

One sweep walks the completed candidates and turns each into an outcome: a
worktree Git removed, one the preflight or Git itself refused, a branch that
went with its worktree, and a branch Git would not delete. These tests pin that
workflow with readable examples — the skip that keeps one dirty worktree from
abandoning the batch, the hard-mode branch rule, dry runs that plan without
mutating, the failures that are reported rather than fatal, and the bounded
record each step leaves behind. The doubles and builders they share with the
generated batches in ``test_plonk_cleanup_properties.py`` live in
``plonk_cleanup_helpers``; the adapter's own contract against Git is covered in
``test_plonk_worktree_adapter.py``, and the soft pass in
``test_plonk_soft_mode.py``.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest

from git_donkey import plonk
from tests.unit.plonk_cleanup_helpers import (
    WORKTREE_BRANCH,
    FailingGitAdapter,
    RecordingGitAdapter,
    candidate,
    marker_for,
    run_cleanup,
)

if typ.TYPE_CHECKING:
    from tests.observability_helpers import RecordingRecorder


def test_dirty_candidate_does_not_abandon_its_clean_siblings() -> None:
    """A skipped worktree should not stop the batch, or the sweep is pointless."""
    dirty = candidate("issue-456-dirty", 456)
    clean = candidate("issue-123-clean", 123)
    adapter = RecordingGitAdapter(
        [marker_for(dirty), marker_for(clean)],
        skip_reasons={dirty.worktree_path: plonk._SkipReason.DIRTY},
    )

    result = run_cleanup(
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
    clean = candidate("issue-123-clean", 123)
    dirty = candidate("issue-456-dirty", 456)
    adapter = RecordingGitAdapter(
        [marker_for(clean), marker_for(dirty)],
        skip_reasons={dirty.worktree_path: plonk._SkipReason.DIRTY},
    )

    result = run_cleanup([clean, dirty], adapter, mode=plonk._PlonkMode.HARD)

    assert result.removed_branches == (clean.branch_name,), (
        "only the branch whose worktree was actually removed is deleted"
    )
    assert adapter.deleted == [clean.branch_name], (
        "the skipped worktree keeps the branch that holds its uncommitted work"
    )


def test_dry_run_reports_skips_without_mutating() -> None:
    """A preview should name the worktrees a real run would leave alone."""
    clean = candidate("issue-123-clean", 123)
    gone = candidate("issue-456-gone", 456)
    adapter = RecordingGitAdapter(
        [marker_for(clean), marker_for(gone)],
        skip_reasons={gone.worktree_path: plonk._SkipReason.UNAVAILABLE},
    )

    result = run_cleanup(
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
    stubborn = candidate("issue-456-stubborn", 456)
    clean = candidate("issue-123-clean", 123)
    adapter = RecordingGitAdapter(
        [marker_for(stubborn), marker_for(clean)],
        removal_failures=[stubborn.worktree_path],
    )

    result = run_cleanup([stubborn, clean], adapter, mode=plonk._PlonkMode.HARD)

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
    completed = candidate(WORKTREE_BRANCH, 123)

    result = run_cleanup([completed], FailingGitAdapter(), mode=mode, dry_run=True)

    assert result.mode is mode, "expected completed dry-run mode to be preserved"
    assert result.is_dry_run, "expected dry-run result marker"
    assert result.removed_worktrees == (completed.worktree_path,), (
        "expected planned worktree removal"
    )
    assert result.removed_branches == expected_branches, (
        f"expected planned branch deletion for {mode.value} mode"
    )


def test_failed_branch_deletion_is_reported_and_the_batch_continues() -> None:
    """A branch Git refuses to delete should be reported, not fatal."""
    stubborn = candidate("issue-456-stubborn", 456)
    clean = candidate("issue-123-clean", 123)
    adapter = RecordingGitAdapter(
        [marker_for(stubborn), marker_for(clean)],
        deletion_failures=[stubborn.branch_name],
    )

    result = run_cleanup([stubborn, clean], adapter, mode=plonk._PlonkMode.HARD)

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
    dirty = candidate("issue-456-dirty", 456)
    adapter = RecordingGitAdapter(
        [marker_for(dirty)],
        skip_reasons={dirty.worktree_path: plonk._SkipReason.DIRTY},
    )

    run_cleanup([dirty], adapter, mode=plonk._PlonkMode.DEFAULT)

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
    stubborn = candidate("issue-456-stubborn", 456)
    branchless = candidate("issue-789-branchless", 789)
    adapter = RecordingGitAdapter(
        [marker_for(stubborn), marker_for(branchless)],
        removal_failures=[stubborn.worktree_path],
        deletion_failures=[branchless.branch_name],
    )

    run_cleanup([stubborn, branchless], adapter, mode=plonk._PlonkMode.HARD)

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
