"""Unit tests for the record lifecycle a completed sweep leaves behind.

A ``git-plonk`` cleanup run makes three writes around every branch it finishes
with: it preserves the branch's tip as a tombstone, deletes the branch, and
clears the live record the tombstone replaces. These tests pin what those
writes owe each other — a branch is never deleted without its tombstone, a
refusal leaves the record beside the branch, and a clear the store refuses is
reported rather than fatal — together with the record housekeeping the sweep
performs beside them: the orphans it rescues or clears, the stale tombstones
it prunes by the window it names, and the refusals that stop a run before any
worktree is touched.

The workflow's own examples live in ``test_plonk_cleanup.py``, the batch rules
they generalise in ``test_plonk_cleanup_properties.py``, and the doubles and
builders all three share in ``plonk_cleanup_helpers``.
"""

from __future__ import annotations

import pytest

from git_donkey import plonk, stack_store
from tests.unit.plonk_cleanup_helpers import (
    RECORDED_TIP,
    WORKTREE_BRANCH,
    EntombFirstAdapter,
    RecordingGitAdapter,
    RecordingStackStore,
    UnnameableStackStore,
    candidate,
    cleanup_surfaces,
    marker_for,
    run_cleanup,
)

# Exit codes a record step reports with when it could not be taken, and when
# the run was never usable at all.
_FAILURE_EXIT_CODE = 1
_USAGE_EXIT_CODE = 2

_FORGOTTEN_BRANCH = "issue-124-forgotten"
"""Branch whose tombstone the store refuses to write."""

_ALREADY_ENTOMBED = "issue-100-already-entombed"
"""Branch whose tombstone the store already holds, so the guard cannot pass."""

_UNSAFE_BRANCH = "issue-125-release.lock"
"""Completed branch whose name the record store refuses in a ref path.

The name follows the issue convention, so the run selects it, and ends in a
component Git reserves for its own lock files, which is the shape
``stack_records.validate_ref_component`` refuses with ``ValueError``.
"""


def test_a_refused_deletion_keeps_the_record_beside_the_branch() -> None:
    """A branch Git will not delete keeps the record of its own boundary.

    The tombstone preserves the tip either way, so a refusal costs the run no
    tip it would have to report as lost. Clearing the record before Git is
    asked would cost the branch the only statement of where its own work
    starts, and leave it with neither, so the clear waits for the deletion.
    """
    stubborn = candidate(WORKTREE_BRANCH, 123)
    records = RecordingStackStore()
    adapter = EntombFirstAdapter(
        [marker_for(stubborn)],
        records,
        deletion_failures=[stubborn.branch_name],
    )

    result = run_cleanup(
        [stubborn], cleanup_surfaces(adapter, records), plonk._PlonkMode.HARD
    )

    assert records.entombed == [(stubborn.branch_name, RECORDED_TIP)], (
        "the tip is preserved before Git is asked for the deletion"
    )
    assert not records.cleared, (
        "the record outlives a deletion that never happened, which is the state "
        "the sweep leaves alone"
    )
    assert result.entombed_branches == (stubborn.branch_name,), (
        "the entombment is reported beside the deletion it did not make"
    )
    assert result.failed_branch_deletions == (stubborn.branch_name,), (
        "and the deletion Git refused is reported as the failure it is"
    )


def test_a_record_that_cannot_be_cleared_is_reported_and_the_run_continues(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A refused clear leaves an orphan for the sweep, so it is not fatal.

    Clearing the record removes the anchor the deletion could not carry away,
    and a refusal leaves a record with no branch — the INV-9 violation the next
    run's sweep repairs. The run reports it and carries on: the branch this
    candidate named is gone, so the work the run owes is done, and stopping
    here would leave the rest of the candidates untouched.
    """
    cleaned = candidate(WORKTREE_BRANCH, 123)
    records = RecordingStackStore(clear_failures=[cleaned.branch_name])
    adapter = RecordingGitAdapter([marker_for(cleaned)])

    result = run_cleanup(
        [cleaned], cleanup_surfaces(adapter, records), plonk._PlonkMode.HARD
    )

    assert records.entombed == [(cleaned.branch_name, RECORDED_TIP)], (
        "the tip was preserved before the branch went"
    )
    assert adapter.deleted == [cleaned.branch_name], "and the branch was deleted"
    assert not records.cleared, (
        "the record the store refused to clear is not reported as cleared"
    )
    assert result.entombed_branches == (cleaned.branch_name,), (
        "the tip is preserved and the branch is gone, so neither step failed"
    )
    assert not result.is_incomplete(), (
        "the leftover anchor is the sweep's to repair rather than a step owed"
    )
    assert cleaned.branch_name in capsys.readouterr().err, (
        "and the refusal is reported where the run reports its other refusals"
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
    someone else must not be enough for the sweep to delete this one: the
    tombstone the store would not write for the branch being deleted leaves it
    where it is, and the sweep reports that branch's entombment as failed. It
    is only that branch the refusal reaches, which is why the sibling is still
    deleted.
    """
    preserved = candidate(WORKTREE_BRANCH, 123)
    forgotten = candidate(_FORGOTTEN_BRANCH, 124)
    records = RecordingStackStore(entomb_failures=(forgotten.branch_name,))
    records.preserve_tip(_ALREADY_ENTOMBED, RECORDED_TIP)
    records.clear_record(_ALREADY_ENTOMBED)
    adapter = EntombFirstAdapter(
        [marker_for(preserved), marker_for(forgotten)], records
    )

    result = run_cleanup(
        [preserved, forgotten],
        cleanup_surfaces(adapter, records),
        plonk._PlonkMode.HARD,
    )

    assert result.failed_entombments == (forgotten.branch_name,), (
        "the branch whose own tombstone could not be written is reported"
    )
    assert forgotten.branch_name not in [branch for branch, _ in records.entombed], (
        "no tombstone names the tip of the branch that was refused"
    )
    assert forgotten.branch_name not in adapter.deleted, (
        "so the branch nothing preserves is left where it is"
    )
    assert preserved.branch_name in adapter.deleted, (
        "and the refusal reaches only the branch whose own tombstone is missing"
    )


def test_hard_mode_reports_a_branch_name_the_store_will_not_read() -> None:
    """A name the record store refuses is one more failed entombment.

    ``preserve_tip`` documents two refusals — a tombstone Git would not write,
    and a branch name that would be unsafe in a ref path — and the cleanup
    answers both the same way: the tip was not preserved, so the branch is left
    where it is and the run reports the tombstone it could not make rather than
    ending on it. The name here is one the writer's own validator refuses, so
    the refusal the run meets is the store's rather than the double's opinion
    of a name.

    No branch Git has stored carries the name — Git reserves the suffix, so the
    selection cannot presently produce it — and what is pinned here is the
    handler's contract, which is written against the store's documented
    refusals rather than against the names a run has happened to meet.
    """
    unsafe = candidate(_UNSAFE_BRANCH, 125)
    records = RecordingStackStore()
    adapter = EntombFirstAdapter([marker_for(unsafe)], records)

    result = run_cleanup(
        [unsafe],
        cleanup_surfaces(adapter, records),
        plonk._PlonkMode.HARD,
    )

    assert result.failed_entombments == (unsafe.branch_name,), (
        "a name the store refuses is reported as a failed entombment"
    )
    assert unsafe.branch_name not in adapter.deleted, (
        "and the branch nothing preserves is left where it is"
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


@pytest.mark.parametrize("dry_run", [True, False], ids=["dry-run", "sweep"])
def test_an_orphan_name_the_store_cannot_read_stops_the_run(
    capsys: pytest.CaptureFixture[str],
    *,
    dry_run: bool,
) -> None:
    """A name Git will not accept in a ref path stops the run, not the sweep.

    The record namespace is not this command's alone: a ref under
    ``refs/stack-bases/`` may begin with ``-``, which Git allows and the anchor
    path does not, so reading that name raises ``ValueError`` from the store
    rather than reporting an orphan. The sweep is repository-wide, so the raise
    has to stop the run before a single worktree is removed — and a dry run
    reads every orphan's anchor too, so it stops on exactly the same name.
    """
    completed = candidate(WORKTREE_BRANCH, 123)
    records = UnnameableStackStore(orphaned=("--upload-pack=x",))
    adapter = RecordingGitAdapter([marker_for(completed)])

    with pytest.raises(SystemExit) as excinfo:
        run_cleanup(
            [completed],
            cleanup_surfaces(adapter, records),
            plonk._PlonkMode.HARD,
            dry_run=dry_run,
        )

    assert excinfo.value.code == _FAILURE_EXIT_CODE, (
        "a record that cannot be read is a run that did not finish"
    )
    assert not adapter.removed, "no worktree is removed behind an unreadable name"
    assert "the stack record sweep failed" in capsys.readouterr().err, (
        "the error says which step could not finish"
    )
