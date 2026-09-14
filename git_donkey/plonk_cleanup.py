"""Run the completed-cleanup workflow of ``git-plonk``.

The workflow owns the record lifecycle and the worktree removals in one pass.
The record lifecycle runs first, because it is repository-wide: the retention
window is resolved, orphaned records are swept into tombstones, and stale
tombstones are pruned before the first worktree is touched, so a window that
cannot be read stops the run while the candidates are still whole. Sweeping
here is also what keeps the deletions below from orphaning anything, since each
hard-mode branch is entombed before it is deleted.

Every candidate contributes its own outcome, and no refusal is fatal: a dirty
worktree is skipped, a branch Git kept is reported beside the worktree that
really did go, and a tip that could not be preserved keeps its branch. The
command entry point that resolves the repository state and renders the summary
lives in :mod:`git_donkey.plonk`.

"""

from __future__ import annotations

import dataclasses
import logging
import typing as typ

from git_donkey import helpers, observability, plonk_policy, stack_store
from git_donkey._constants import GIT_PLONK_PREFIX
from git_donkey.plonk_records import (
    _MODE_LABELS,
    _SKIP_REASON_LABELS,
    _CandidateOutcome,
    _CleanupTally,
    _PlonkContext,
    _PlonkMode,
    _PlonkResult,
    _SkipReason,
)
from git_donkey.plonk_selection import _donkey_worktree_candidates
from git_donkey.plonk_worktree_adapter import _GitWorktreeAdapter

if typ.TYPE_CHECKING:
    from git_donkey.plonk_records import _PlonkCandidate

_LOGGER = logging.getLogger(__name__)


def _log_plonk_cleanup_start(mode: _PlonkMode, context: _PlonkContext) -> None:
    """Log the start of completed git-plonk cleanup."""
    _LOGGER.info(
        "Starting git-plonk completed cleanup",
        extra={
            "mode": mode.value,
            "worktrees_root": context.worktrees_root.as_posix(),
            "trunk_ref": context.trunk_ref,
        },
    )


def _log_plonk_candidates_selected(
    mode: _PlonkMode,
    candidates: typ.Sequence[_PlonkCandidate],
    completed_candidates: typ.Sequence[_PlonkCandidate],
    removable_candidates: typ.Sequence[_PlonkCandidate],
) -> None:
    """Log candidate selection counts for completed git-plonk cleanup."""
    _LOGGER.info(
        "Selected git-plonk completion candidates",
        extra={
            "mode": mode.value,
            "candidate_count": len(candidates),
            "completed_count": len(completed_candidates),
            "excluded_invocation_count": len(completed_candidates)
            - len(removable_candidates),
        },
    )


def _log_planned_step(
    candidate: _PlonkCandidate,
    mode: _PlonkMode,
    *,
    dry_run: bool,
    removing_worktree: bool,
) -> None:
    """Log one planned or taken step against a completed candidate.

    The two steps the cleanup takes against a candidate — removing its
    worktree and deleting its branch — are logged by the same shape, so the
    fields every such line carries are assembled in one place. Only the step
    that removes the worktree names the path it removes.
    """
    subject = "worktree" if removing_worktree else "branch"
    if dry_run:
        message = (
            f"Planning completed git-plonk {subject} "
            f"{'removal' if removing_worktree else 'deletion'}"
        )
    else:
        message = (
            f"{'Removing' if removing_worktree else 'Deleting'} completed "
            f"git-plonk {subject}"
        )
    fields: dict[str, object] = {
        "mode": mode.value,
        "operation": "remove_worktree" if removing_worktree else "delete_branch",
        "dry_run": dry_run,
        "branch": candidate.branch_name,
        "marker": candidate.marker,
    }
    if removing_worktree:
        fields["worktree"] = candidate.worktree_path.as_posix()
    _LOGGER.info(message, extra=fields)


def _log_skipped_candidate(
    candidate: _PlonkCandidate,
    reason: _SkipReason,
    mode: _PlonkMode,
) -> None:
    """Log a completed candidate that git-plonk left in place."""
    _LOGGER.info(
        "Skipping git-plonk candidate",
        extra={
            "mode": mode.value,
            "operation": "skip_worktree",
            "branch": candidate.branch_name,
            "marker": candidate.marker,
            "worktree": candidate.worktree_path.as_posix(),
            "reason": reason.value,
        },
    )


def _record_step(
    operation: observability.Operation,
    outcome: observability.Outcome,
    mode: _PlonkMode,
    *,
    skip_reason: observability.SkipReasonLabel | None = None,
) -> None:
    """Record one bounded cleanup step, translating ``mode`` to its label."""
    observability.get_recorder().record(
        observability.Observation(
            operation=operation,
            outcome=outcome,
            mode=_MODE_LABELS[mode],
            skip_reason=skip_reason,
        )
    )


def _record_failure(operation: observability.Operation, mode: _PlonkMode) -> None:
    """Record a step where Git refused an action git-plonk asked for."""
    observability.get_recorder().record(
        observability.Observation(
            operation=operation,
            outcome="failure",
            mode=_MODE_LABELS[mode],
            error_kind="git_command_error",
        )
    )


def _configured_expiry(records: stack_store.StackRecordWriter) -> str:
    """Return the retention window tombstones are pruned by.

    The window is resolved before the run touches anything, because Git reads
    a date expression it cannot parse as *now*, and a window of no length
    prunes every tombstone in the repository. A typo must stop the run rather
    than empty it.

    Parameters
    ----------
    records : stack_store.StackRecordWriter
        Store holding the repository the window is configured in.

    Returns
    -------
    str
        The configured window, or the documented default when unset.

    """
    try:
        return records.expiry()
    except ValueError as exc:
        helpers._die(
            GIT_PLONK_PREFIX,
            f"the stack record retention window is unusable: {exc}",
            2,
        )


def _sweep_orphans(
    records: stack_store.StackRecordWriter,
    mode: _PlonkMode,
    *,
    dry_run: bool,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Clear the records of branches that have gone.

    A sweep repairs INV-9 for every orphan, but only an orphan whose record
    still parses yields a tombstone: when the branch was deleted through Git
    alone, the configuration went with it and there is no tip left to
    preserve. The two outcomes are reported apart, because a summary that
    named them alike would claim a rescue that did not happen.

    Parameters
    ----------
    records : stack_store.StackRecordWriter
        Store holding the records and the tombstones.
    mode : _PlonkMode
        Cleanup mode, carried into the observability record.
    dry_run : bool
        When true, report what a sweep would preserve without writing it.

    Returns
    -------
    tuple[tuple[str, ...], tuple[str, ...]]
        The orphans whose tip is preserved, and the orphans cleared with no
        tip to preserve.

    Raises
    ------
    SystemExit
        If a record cannot be cleared. The sweep is repository-wide, so a
        failure stops the run before any worktree has been removed rather than
        leaving the cleanup half done.

    """
    orphans = records.orphans()
    if not orphans:
        return (), ()
    if dry_run:
        swept = records.rescuable(orphans)
    else:
        try:
            swept = records.sweep(orphans)
        except stack_store.StackRecordError as exc:
            _record_failure("stack_record_sweep", mode)
            helpers._die(GIT_PLONK_PREFIX, f"the stack record sweep failed: {exc}", 1)
    preserved = set(swept)
    unrescuable = tuple(branch for branch in orphans if branch not in preserved)
    _LOGGER.info(
        "Swept git-plonk stack records",
        extra={
            "mode": mode.value,
            "operation": "stack_record_sweep",
            "dry_run": dry_run,
            "orphan_count": len(orphans),
            "preserved_count": len(swept),
        },
    )
    if not dry_run:
        _record_step("stack_record_sweep", "success", mode)
    return tuple(swept), unrescuable


def _prune_tombstones(
    records: stack_store.StackRecordWriter,
    expire: str,
    mode: _PlonkMode,
    *,
    dry_run: bool,
) -> tuple[str, ...]:
    """Delete the tombstones written before ``expire``.

    Parameters
    ----------
    records : stack_store.StackRecordWriter
        Store holding the tombstones.
    expire : str
        Retention window, already validated as a past instant.
    mode : _PlonkMode
        Cleanup mode, carried into the observability record.
    dry_run : bool
        When true, report the tombstones a prune would delete.

    Returns
    -------
    tuple[str, ...]
        The tombstones that were deleted, or would be.

    Raises
    ------
    SystemExit
        If a tombstone cannot be deleted, for the same reason the sweep stops
        the run: the record lifecycle is repository-wide.

    """
    try:
        pruned = tuple(records.expired(expire) if dry_run else records.prune(expire))
    except (stack_store.StackRecordError, ValueError) as exc:
        _record_failure("stack_record_prune", mode)
        helpers._die(GIT_PLONK_PREFIX, f"pruning tombstones failed: {exc}", 1)
    if not pruned:
        return ()
    _LOGGER.info(
        "Pruned git-plonk tombstones",
        extra={
            "mode": mode.value,
            "operation": "stack_record_prune",
            "dry_run": dry_run,
            "expire": expire,
            "pruned_count": len(pruned),
        },
    )
    if not dry_run:
        _record_step("stack_record_prune", "success", mode)
    return pruned


@dataclasses.dataclass(frozen=True, slots=True)
class _CleanupSurfaces:
    """The two Git surfaces a completed run cleans through.

    A run holds the adapter it removes worktrees and deletes branches with, and
    the store it reads and writes stack records through. Holding them together
    lets a per-candidate step take the pair as one argument, and lets a run be
    driven over doubles without either surface reaching for the repository.
    """

    adapter: _GitWorktreeAdapter
    records: stack_store.StackRecordWriter


def _entomb_branch(
    candidate: _PlonkCandidate,
    tip: str,
    records: stack_store.StackRecordWriter,
    mode: _PlonkMode,
) -> bool:
    """Preserve ``candidate``'s tip as a tombstone, before the branch goes.

    The tombstone is written while the branch still exists, because the
    ``git branch -D`` that follows is forced: the reverse order would take the
    only record of that commit with it.

    Parameters
    ----------
    candidate : _PlonkCandidate
        Completed candidate whose branch is about to be deleted.
    tip : str
        Commit the branch names now, read before this call.
    records : stack_store.StackRecordWriter
        Store the tombstone is written through.
    mode : _PlonkMode
        Cleanup mode, carried into the observability record.

    Returns
    -------
    bool
        ``True`` when a tombstone now names the tip, otherwise ``False``.

    """
    _LOGGER.info(
        "Entombing completed git-plonk branch",
        extra={
            "mode": mode.value,
            "operation": "stack_record_entomb",
            "branch": candidate.branch_name,
            "marker": candidate.marker,
        },
    )
    try:
        records.entomb(candidate.branch_name, tip)
    except stack_store.StackRecordError as exc:
        _LOGGER.exception(
            "Failed to entomb git-plonk branch",
            extra={
                "operation": "stack_record_entomb",
                "branch": candidate.branch_name,
            },
        )
        helpers._eprint(
            f"{GIT_PLONK_PREFIX}: failed to preserve the tip of "
            f"'{candidate.branch_name}': {exc}"
        )
        return False
    return True


def _delete_completed_branch(
    candidate: _PlonkCandidate,
    surfaces: _CleanupSurfaces,
    mode: _PlonkMode,
    *,
    dry_run: bool,
) -> _CandidateOutcome:
    """Delete a completed candidate's branch, preserving its tip first.

    The tip is preserved as a tombstone while the branch still names it, so a
    parent a surviving child depends on stays reachable after the deletion. A
    branch Git refuses to delete is reported separately, because that
    candidate's worktree really did go, and a branch whose tip could not be
    preserved is not deleted at all.

    Parameters
    ----------
    candidate : _PlonkCandidate
        Candidate whose branch is deleted.
    surfaces : _CleanupSurfaces
        Git surfaces the run cleans through.
    mode : _PlonkMode
        Cleanup mode, carried into the observability record.
    dry_run : bool
        When true, report the deletion without making it.

    Returns
    -------
    _CandidateOutcome
        The entombment and the deletion, or the reason neither happened.

    """
    adapter, records = surfaces.adapter, surfaces.records
    _log_planned_step(candidate, mode, dry_run=dry_run, removing_worktree=False)
    if dry_run:
        return _CandidateOutcome(entombed=True)

    tip = records.branch_tip(candidate.branch_name)
    if tip is None:
        # The branch has already gone, so there is no tip to preserve and
        # nothing here to delete; the run reports the deletion it could not do.
        _record_failure("branch_deletion", mode)
        return _CandidateOutcome(branch_deletion_failed=True)
    if not _entomb_branch(candidate, tip, records, mode):
        _record_failure("stack_record_entomb", mode)
        return _CandidateOutcome(entomb_failed=True)
    _record_step("stack_record_entomb", "success", mode)
    if not adapter.delete_branch(candidate.branch_name):
        _record_failure("branch_deletion", mode)
        return _CandidateOutcome(branch_deletion_failed=True, entombed=True)
    _record_step("branch_deletion", "success", mode)
    return _CandidateOutcome(entombed=True)


def _clean_completed_candidate(
    candidate: _PlonkCandidate,
    surfaces: _CleanupSurfaces,
    mode: _PlonkMode,
    *,
    dry_run: bool,
) -> _CandidateOutcome:
    """Clean one completed candidate, reporting what held it back instead.

    A candidate is only touched when Git would discard it unprompted, so a
    completed worktree holding uncommitted or untracked work survives the run
    and is reported. Its local branch survives with it, even in hard mode: the
    branch is only safe to delete once its worktree is gone, and hard mode
    preserves the branch's tip before it goes.

    Parameters
    ----------
    candidate : _PlonkCandidate
        Completed candidate to clean.
    surfaces : _CleanupSurfaces
        Git surfaces the run cleans through.
    mode : _PlonkMode
        Cleanup mode; hard mode also deletes the local branch.
    dry_run : bool
        When true, plan the work without removing anything.

    Returns
    -------
    _CandidateOutcome
        What the candidate contributed: a skip reason, an entombment, a
        branch-deletion failure, or none of them when it was cleaned as
        planned.

    """
    adapter = surfaces.adapter
    reason = adapter.skip_reason(candidate.worktree_path)
    if reason is not None:
        _log_skipped_candidate(candidate, reason, mode)
        _record_step(
            "worktree_preflight",
            "skipped",
            mode,
            skip_reason=_SKIP_REASON_LABELS[reason],
        )
        return _CandidateOutcome(skip_reason=reason)

    _record_step("worktree_preflight", "success", mode)
    _log_planned_step(candidate, mode, dry_run=dry_run, removing_worktree=True)
    if not dry_run:
        if not adapter.remove_worktree(candidate.worktree_path):
            _log_skipped_candidate(candidate, _SkipReason.REMOVAL_FAILED, mode)
            _record_failure("worktree_removal", mode)
            return _CandidateOutcome(skip_reason=_SkipReason.REMOVAL_FAILED)
        _record_step("worktree_removal", "success", mode)

    if mode is not _PlonkMode.HARD:
        return _CandidateOutcome()
    return _delete_completed_branch(candidate, surfaces, mode, dry_run=dry_run)


def _clean_candidates(
    candidates: typ.Iterable[_PlonkCandidate],
    surfaces: _CleanupSurfaces,
    mode: _PlonkMode,
    *,
    dry_run: bool,
) -> _CleanupTally:
    """Clean ``candidates``, returning what each of them contributed.

    Parameters
    ----------
    candidates : collections.abc.Iterable[_PlonkCandidate]
        Completed candidates, already known to be removable.
    surfaces : _CleanupSurfaces
        Git surfaces the run cleans through.
    mode : _PlonkMode
        Cleanup mode; hard mode also deletes the local branches.
    dry_run : bool
        When true, plan the work without removing anything.

    Returns
    -------
    _CleanupTally
        What the run removed, skipped, and preserved.

    """
    tally = _CleanupTally()
    for candidate in candidates:
        outcome = _clean_completed_candidate(candidate, surfaces, mode, dry_run=dry_run)
        tally.add(candidate, outcome, mode)
    return tally


@dataclasses.dataclass(frozen=True, slots=True)
class _RecordSweep:
    """What the record lifecycle did before the first worktree was touched.

    Attributes
    ----------
    swept : tuple[str, ...]
        Names of the records the sweep cleared.
    unrescuable : tuple[str, ...]
        Names of the records that could not be rescued.
    pruned : tuple[str, ...]
        Names of the tombstones the prune deleted.
    expire : str
        The retention window the run resolved, which the prune read.

    """

    swept: tuple[str, ...]
    unrescuable: tuple[str, ...]
    pruned: tuple[str, ...]
    expire: str


def _default_surfaces(context: _PlonkContext) -> _CleanupSurfaces:
    """Return the Git surfaces a run given none of its own cleans through."""
    return _CleanupSurfaces(
        adapter=_GitWorktreeAdapter(context.repo_home),
        records=stack_store.GitStackRecordWriter(context.repo_home),
    )


def _sweep_records(
    surfaces: _CleanupSurfaces,
    mode: _PlonkMode,
    *,
    dry_run: bool,
) -> _RecordSweep:
    """Deal with the repository's records before any worktree is touched.

    The retention window is resolved first, because Git reads a date
    expression it cannot parse as *now*, so a window that cannot be read stops
    the run while the candidates are still whole. Sweeping here is also what
    keeps the removals that follow from orphaning anything, since each of them
    entombs its branch before deleting it.

    Parameters
    ----------
    surfaces : _CleanupSurfaces
        Git surfaces the run cleans through.
    mode : _PlonkMode
        Cleanup mode, carried into the observability record.
    dry_run : bool
        When true, report what the sweep would do without doing it.

    Returns
    -------
    _RecordSweep
        What the sweep cleared, what it could not rescue, what the prune
        deleted, and the window it pruned by.

    """
    expire = _configured_expiry(surfaces.records)
    swept, unrescuable = _sweep_orphans(surfaces.records, mode, dry_run=dry_run)
    pruned = _prune_tombstones(surfaces.records, expire, mode, dry_run=dry_run)
    return _RecordSweep(swept, unrescuable, pruned, expire)


def _removable_candidates(
    context: _PlonkContext,
    surfaces: _CleanupSurfaces,
    mode: _PlonkMode,
) -> tuple[int, list[_PlonkCandidate]]:
    """Return how many worktrees were inspected and which may be removed.

    The worktree the run was invoked from holds the branch under inspection
    and survives, whatever the mode; the rest of the completed candidates are
    the ones the run may remove.

    Parameters
    ----------
    context : _PlonkContext
        Resolved repository state for the run.
    surfaces : _CleanupSurfaces
        Git surfaces the run cleans through.
    mode : _PlonkMode
        Cleanup mode, carried into the observability record.

    Returns
    -------
    tuple[int, list[_PlonkCandidate]]
        The number of candidate worktrees seen, and the completed ones that
        may be removed.

    """
    candidates = _donkey_worktree_candidates(context.stanzas, context.worktrees_root)
    completed = plonk_policy.completed_candidates(
        candidates,
        surfaces.adapter.history_messages(context.trunk_ref),
    )
    removable = [
        candidate
        for candidate in completed
        if candidate.worktree_path != context.invoking_worktree
    ]
    _log_plonk_candidates_selected(mode, candidates, completed, removable)
    return len(candidates), removable


def _run_completed_cleanup(
    context: _PlonkContext,
    mode: _PlonkMode,
    surfaces: _CleanupSurfaces | None = None,
    *,
    dry_run: bool = False,
) -> _PlonkResult:
    """Remove completed worktrees and optionally their local branches.

    The record lifecycle is repository-wide and runs first, so a retention
    window that cannot be read stops the run while the candidates are still
    whole. See :func:`_sweep_records` for what that phase does and why it runs
    before the first worktree is touched.

    Parameters
    ----------
    context : _PlonkContext
        Resolved repository state for the run.
    mode : _PlonkMode
        Cleanup mode; hard mode also deletes the local branches.
    surfaces : _CleanupSurfaces | None, optional
        Git surfaces for the removals and the records, defaulting to the
        repository's own.
    dry_run : bool, optional
        When true, report what the run would do without doing it.

    Returns
    -------
    _PlonkResult
        What the run removed or planned, plus what it preserved.

    """
    surfaces = surfaces or _default_surfaces(context)
    _log_plonk_cleanup_start(mode, context)
    sweep = _sweep_records(surfaces, mode, dry_run=dry_run)
    inspected, removable = _removable_candidates(context, surfaces, mode)
    tally = _clean_candidates(removable, surfaces, mode, dry_run=dry_run)

    return _PlonkResult(
        mode=mode,
        is_dry_run=dry_run,
        inspected_worktrees=inspected,
        removed_worktrees=tuple(tally.removed_worktrees),
        removed_branches=tuple(tally.removed_branches),
        skipped_worktrees=tuple(tally.skipped_worktrees),
        failed_branch_deletions=tuple(tally.failed_branch_deletions),
        entombed_branches=tuple(tally.entombed_branches),
        failed_entombments=tuple(tally.failed_entombments),
        swept_records=sweep.swept,
        unrescuable_records=sweep.unrescuable,
        pruned_tombstones=sweep.pruned,
        tombstone_expire=sweep.expire,
    )
