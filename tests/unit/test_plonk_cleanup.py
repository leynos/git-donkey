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

from git_donkey import plonk, plonk_records, stack_store
from tests.unit.plonk_cleanup_helpers import (
    PLANNED_ORPHAN,
    PLANNED_STALE_TOMBSTONE,
    RECORDED_TIP,
    WORKTREE_BRANCH,
    EntombFirstAdapter,
    FailingGitAdapter,
    FailingStackStore,
    ForgetfulStackStore,
    RecordingGitAdapter,
    RecordingStackStore,
    candidate,
    cleanup_surfaces,
    marker_for,
    run_cleanup,
)

if typ.TYPE_CHECKING:
    from tests.observability_helpers import RecordingRecorder

# Exit codes the command reports a completed cleanup with: everything asked for
# was done, something asked for was not, and the run was never usable at all.
_SUCCESS_EXIT_CODE = 0
_FAILURE_EXIT_CODE = 1
_USAGE_EXIT_CODE = 2

_FORGOTTEN_BRANCH = "issue-124-forgotten"
"""Branch whose tombstone the forgetful store double never writes."""

_ALREADY_ENTOMBED = "issue-100-already-entombed"
"""Branch whose tombstone the store already holds, so the guard cannot pass."""


def test_dirty_candidate_does_not_abandon_its_clean_siblings() -> None:
    """A skipped worktree should not stop the batch, or the sweep is pointless."""
    dirty = candidate("issue-456-dirty", 456)
    clean = candidate("issue-123-clean", 123)
    adapter = RecordingGitAdapter(
        [marker_for(dirty), marker_for(clean)],
        skip_reasons={dirty.worktree_path: plonk_records._SkipReason.DIRTY},
    )

    result = run_cleanup(
        [dirty, clean],
        cleanup_surfaces(adapter),
        plonk._PlonkMode.DEFAULT,
    )

    assert result.removed_worktrees == (clean.worktree_path,), (
        "the clean worktree is removed even though its sibling was skipped"
    )
    assert result.skipped_worktrees == (
        plonk_records._SkippedWorktree(
            dirty.worktree_path, plonk_records._SkipReason.DIRTY
        ),
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
        skip_reasons={dirty.worktree_path: plonk_records._SkipReason.DIRTY},
    )

    result = run_cleanup(
        [clean, dirty], cleanup_surfaces(adapter), plonk._PlonkMode.HARD
    )

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
        skip_reasons={gone.worktree_path: plonk_records._SkipReason.UNAVAILABLE},
    )

    result = run_cleanup(
        [clean, gone],
        cleanup_surfaces(adapter),
        plonk._PlonkMode.HARD,
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
        plonk_records._SkippedWorktree(
            gone.worktree_path, plonk_records._SkipReason.UNAVAILABLE
        ),
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

    result = run_cleanup(
        [stubborn, clean], cleanup_surfaces(adapter), plonk._PlonkMode.HARD
    )

    assert result.removed_worktrees == (clean.worktree_path,), (
        "the following clean candidate is still removed"
    )
    assert result.removed_branches == (clean.branch_name,), (
        "a branch is only deleted once its worktree is gone"
    )
    assert result.skipped_worktrees == (
        plonk_records._SkippedWorktree(
            stubborn.worktree_path, plonk_records._SkipReason.REMOVAL_FAILED
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

    result = run_cleanup(
        [completed],
        cleanup_surfaces(FailingGitAdapter(), FailingStackStore()),
        mode,
        dry_run=True,
    )

    assert result.mode is mode, "expected completed dry-run mode to be preserved"
    assert result.is_dry_run, "expected dry-run result marker"
    assert result.removed_worktrees == (completed.worktree_path,), (
        "expected planned worktree removal"
    )
    assert result.removed_branches == expected_branches, (
        f"expected planned branch deletion for {mode.value} mode"
    )
    expected_entombments = (
        (completed.branch_name,) if mode is plonk._PlonkMode.HARD else ()
    )
    assert result.entombed_branches == expected_entombments, (
        "a hard dry run plans the tombstone it would write, and writes none of it"
    )
    assert result.swept_records == (PLANNED_ORPHAN,), (
        "a dry run classifies orphans with reads rather than skipping them"
    )
    assert not result.unrescuable_records, (
        "the double reports every orphan as rescuable, and nothing was written"
    )
    assert result.pruned_tombstones == (PLANNED_STALE_TOMBSTONE,), (
        "a dry run classifies stale tombstones with reads too"
    )


def test_failed_branch_deletion_is_reported_and_the_batch_continues() -> None:
    """A branch Git refuses to delete should be reported, not fatal."""
    stubborn = candidate("issue-456-stubborn", 456)
    clean = candidate("issue-123-clean", 123)
    adapter = RecordingGitAdapter(
        [marker_for(stubborn), marker_for(clean)],
        deletion_failures=[stubborn.branch_name],
    )

    result = run_cleanup(
        [stubborn, clean], cleanup_surfaces(adapter), plonk._PlonkMode.HARD
    )

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
        skip_reasons={dirty.worktree_path: plonk_records._SkipReason.DIRTY},
    )

    run_cleanup([dirty], cleanup_surfaces(adapter), plonk._PlonkMode.DEFAULT)

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

    run_cleanup(
        [stubborn, branchless], cleanup_surfaces(adapter), plonk._PlonkMode.HARD
    )

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
    ("failed_branch_deletions", "failed_entombments"),
    [
        pytest.param((), (), id="complete-sweep"),
        pytest.param(("issue-123-fix",), (), id="surviving-branch"),
        pytest.param((), ("issue-123-fix",), id="unpreserved-tip"),
    ],
)
def test_run_git_plonk_reports_partial_sweeps(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failed_branch_deletions: tuple[str, ...],
    failed_entombments: tuple[str, ...],
) -> None:
    """A branch the sweep could not finish with should reach the caller's code."""
    expected_exit_code = (
        _FAILURE_EXIT_CODE
        if failed_branch_deletions or failed_entombments
        else _SUCCESS_EXIT_CODE
    )
    result = plonk._PlonkResult(
        mode=plonk._PlonkMode.HARD,
        removed_worktrees=(Path("/repo.worktrees/issue-123-fix"),),
        failed_branch_deletions=failed_branch_deletions,
        failed_entombments=failed_entombments,
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
    assert ("Failed entombments:" in summary) is bool(failed_entombments), (
        "the summary names the branch whose tip could not be preserved, and only then"
    )


def test_hard_mode_preserves_the_tip_before_deleting_the_branch() -> None:
    """The tombstone is written while the branch still names the tip."""
    completed = candidate(WORKTREE_BRANCH, 123)
    records = RecordingStackStore()
    adapter = EntombFirstAdapter([marker_for(completed)], records)

    result = run_cleanup(
        [completed],
        cleanup_surfaces(adapter, records),
        plonk._PlonkMode.HARD,
    )

    assert records.entombed == [(completed.branch_name, RECORDED_TIP)], (
        "the branch's tip is preserved as its tombstone"
    )
    assert result.entombed_branches == (completed.branch_name,), (
        "the entombment is reported beside the deletion it made safe"
    )
    assert result.removed_branches == (completed.branch_name,), (
        "the branch is still reported as deleted"
    )
    assert not result.failed_entombments, "a written tombstone is not a failure"


def test_hard_mode_refuses_a_branch_whose_own_tip_was_not_preserved() -> None:
    """A tombstone another branch left behind does not excuse the deletion.

    The rule is about one branch, so a store that already holds a tombstone for
    someone else must not be enough for the sweep to delete this one: the guard
    names the branch being deleted rather than asking whether anything was
    entombed at all.
    """
    preserved = candidate(WORKTREE_BRANCH, 123)
    forgotten = candidate(_FORGOTTEN_BRANCH, 124)
    records = ForgetfulStackStore(forgotten.branch_name)
    records.entomb(_ALREADY_ENTOMBED, RECORDED_TIP)
    adapter = EntombFirstAdapter(
        [marker_for(preserved), marker_for(forgotten)], records
    )

    with pytest.raises(AssertionError) as excinfo:
        run_cleanup(
            [preserved, forgotten],
            cleanup_surfaces(adapter, records),
            plonk._PlonkMode.HARD,
        )

    assert "preserves a branch's tip" in str(excinfo.value), (
        "the refusal names the rule the sweep was about to break"
    )
    assert forgotten.branch_name not in [branch for branch, _ in records.entombed], (
        "no tombstone names the tip of the branch that was refused"
    )
    assert forgotten.branch_name not in adapter.deleted, (
        "so the branch nothing preserves is left where it is"
    )


def test_default_mode_preserves_no_tip() -> None:
    """Default mode deletes no branch, so it has no tip to preserve."""
    completed = candidate(WORKTREE_BRANCH, 123)
    records = RecordingStackStore()
    adapter = RecordingGitAdapter([marker_for(completed)])

    result = run_cleanup(
        [completed],
        cleanup_surfaces(adapter, records),
        plonk._PlonkMode.DEFAULT,
    )

    assert not records.entombed, "a branch that survives keeps naming its own tip"
    assert not result.entombed_branches, "nothing was preserved and nothing is claimed"


def test_an_unpreservable_tip_keeps_the_branch_and_fails_the_run() -> None:
    """A branch is never deleted without the tombstone that survives it."""
    completed = candidate(WORKTREE_BRANCH, 123)
    records = RecordingStackStore(entomb_failures=[completed.branch_name])
    adapter = RecordingGitAdapter([marker_for(completed)])

    result = run_cleanup(
        [completed],
        cleanup_surfaces(adapter, records),
        plonk._PlonkMode.HARD,
    )

    assert result.failed_entombments == (completed.branch_name,), (
        "the branch whose tip could not be preserved is reported"
    )
    assert result.removed_worktrees == (completed.worktree_path,), (
        "the worktree really did go; only the branch was held back"
    )
    assert not result.removed_branches, "a branch with no tombstone is not deleted"
    assert not result.failed_branch_deletions, (
        "nothing was asked of Git, so no deletion failed"
    )
    assert not adapter.deleted, "Git is never asked to delete the branch"
    assert result.is_incomplete(), "the run did not do everything it was asked to"


def test_a_branch_already_gone_is_reported_as_a_failed_deletion() -> None:
    """A branch that vanished before the run is not a branch to entomb."""
    completed = candidate(WORKTREE_BRANCH, 123)
    records = RecordingStackStore(branch_tips={completed.branch_name: None})
    adapter = RecordingGitAdapter([marker_for(completed)])

    result = run_cleanup(
        [completed],
        cleanup_surfaces(adapter, records),
        plonk._PlonkMode.HARD,
    )

    assert result.failed_branch_deletions == (completed.branch_name,), (
        "the deletion the run could not do is what is reported"
    )
    assert not records.entombed, "no tombstone is invented for a tip nobody read"
    assert not result.entombed_branches, "nothing was preserved, so nothing is claimed"
    assert not adapter.deleted, "Git is never asked to delete a branch that is gone"


def test_a_sweep_separates_the_orphan_it_rescued_from_the_one_it_cleared() -> None:
    """An orphan with no record left has no tip, and is not reported as rescued."""
    completed = candidate(WORKTREE_BRANCH, 123)
    rescued, cleared = "issue-456-orphaned", "issue-789-cleared"
    records = RecordingStackStore(
        orphaned=(rescued, cleared),
        preservable={rescued},
    )
    adapter = RecordingGitAdapter([marker_for(completed)])

    result = run_cleanup(
        [completed],
        cleanup_surfaces(adapter, records),
        plonk._PlonkMode.DEFAULT,
    )

    assert records.swept == [(rescued, cleared)], (
        "one sweep clears every orphan the reader found"
    )
    assert result.swept_records == (rescued,), "only the parsed record yields a tip"
    assert result.unrescuable_records == (cleared,), (
        "the orphan Git deleted alone is reported as having no tip to preserve"
    )


def test_a_prune_names_the_window_it_applied() -> None:
    """The summary must say what the tombstones were pruned by."""
    completed = candidate(WORKTREE_BRANCH, 123)
    records = RecordingStackStore(stale=("issue-456-stale",), expire="30.days.ago")
    adapter = RecordingGitAdapter([marker_for(completed)])

    result = run_cleanup(
        [completed],
        cleanup_surfaces(adapter, records),
        plonk._PlonkMode.DEFAULT,
    )

    assert records.pruned == ["30.days.ago"], "the configured window is what prunes"
    assert result.pruned_tombstones == ("issue-456-stale",), (
        "the pruned tombstones are reported"
    )
    assert result.tombstone_expire == "30.days.ago", (
        "the window actually used is reported beside them"
    )
    summary = plonk._render_summary(result)
    assert "Pruned tombstones (older than 30.days.ago):" in summary, (
        "a mistyped window is visible in the summary rather than silently effective"
    )


def test_an_unusable_window_stops_the_run_before_anything_is_touched(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A window Git reads as *now* would prune every tombstone, so it is refused."""
    completed = candidate(WORKTREE_BRANCH, 123)
    records = RecordingStackStore(unusable_expiry=True)
    adapter = RecordingGitAdapter([marker_for(completed)])

    with pytest.raises(SystemExit) as excinfo:
        run_cleanup(
            [completed],
            cleanup_surfaces(adapter, records),
            plonk._PlonkMode.HARD,
        )

    assert excinfo.value.code == _USAGE_EXIT_CODE, (
        "an unusable configuration is a usage error"
    )
    assert not adapter.removed, "no worktree is removed before the window is read"
    assert not records.swept, "no orphan is swept behind a broken window"
    assert not records.pruned, "no tombstone is pruned behind a broken window"
    assert not records.entombed, "the branch is not entombed behind a broken window"
    assert stack_store.TOMBSTONE_EXPIRE_KEY in capsys.readouterr().err, (
        "the error names the configuration key the user has to fix"
    )
