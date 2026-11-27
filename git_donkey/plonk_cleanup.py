"""Perform the removals one completed git-plonk sweep planned.

Every candidate that reaches this module already has completion evidence. The
work here is the irreversible half: run the cleanliness preflight, remove the
worktree without ``--force``, and, in hard mode only, delete the local branch
once its worktree is gone. A refusal is reported and skipped rather than
raised, so one stubborn worktree cannot abandon the candidates behind it.

The result is a tally rather than a summary: what was removed, what was
skipped, and which branch deletions failed. Rendering belongs to
:mod:`git_donkey.plonk_summary`.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from git_donkey.plonk_records import (
    _SKIP_REASON_LABELS,
    _CandidateOutcome,
    _PlonkMode,
    _RemovableCandidate,
    _RemovalEvidence,
    _SkippedWorktree,
    _SkipReason,
)
from git_donkey.plonk_workflow import (
    _BRANCH_DELETION_ACTION,
    _REMOVE_WORKTREE_ACTION,
    _CandidateLeftEvent,
    _log_candidate_left,
    _log_planned_action,
    _record_failure,
    _record_signal_step,
    _record_step,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    from git_donkey.plonk_adapters import _GitWorktreeAdapter
    from git_donkey.plonk_completion import _SweepState


@dataclasses.dataclass(slots=True)
class _CleanupTally:
    """What one sweep removed, skipped, and failed to delete.

    Attributes
    ----------
    removed_worktrees : list[Path]
        Worktrees removed, or planned for removal in a dry run.
    removal_evidence : list[_RemovalEvidence]
        The evidence behind each entry in ``removed_worktrees``.
    removed_branches : list[str]
        Branches deleted, or planned for deletion in a dry run.
    skipped_worktrees : list[_SkippedWorktree]
        Completed worktrees Git would not discard, and why.
    failed_branch_deletions : list[str]
        Branches Git refused to delete after their worktree was removed.

    """

    removed_worktrees: list[Path] = dataclasses.field(default_factory=list)
    removal_evidence: list[_RemovalEvidence] = dataclasses.field(default_factory=list)
    removed_branches: list[str] = dataclasses.field(default_factory=list)
    skipped_worktrees: list[_SkippedWorktree] = dataclasses.field(default_factory=list)
    failed_branch_deletions: list[str] = dataclasses.field(default_factory=list)


def _candidate_skip_event(reason: _SkipReason) -> _CandidateLeftEvent:
    """Return the structured-log event for a skipped candidate."""
    return _CandidateLeftEvent(
        "Skipping git-plonk candidate",
        "skip_worktree",
        reason.value,
    )


def _clean_completed_candidate(
    removable: _RemovableCandidate,
    state: _SweepState,
) -> _CandidateOutcome:
    """Clean one completed candidate, reporting what held it back instead.

    A candidate is only touched when Git would discard it unprompted, so a
    completed worktree holding uncommitted or untracked work survives the run
    and is reported. Its local branch survives with it, even in hard mode: the
    branch is only safe to delete once its worktree is gone. A branch Git then
    refuses to delete is reported separately, because that candidate's worktree
    really did go.

    Parameters
    ----------
    removable : _RemovableCandidate
        Completed candidate and the evidence that completed it.
    state : _SweepState
        Adapter, mode, and options the sweep runs with.

    Returns
    -------
    _CandidateOutcome
        What the candidate contributed: a skip reason, a branch-deletion
        failure, or neither when it was cleaned as planned.

    """
    candidate = removable.candidate
    adapter: _GitWorktreeAdapter = state.adapter
    mode = state.mode
    reason = adapter.skip_reason(candidate.worktree_path)
    if reason is not None:
        _log_candidate_left(candidate, mode, _candidate_skip_event(reason))
        _record_step(
            "worktree_preflight",
            "skipped",
            mode,
            skip_reason=_SKIP_REASON_LABELS[reason],
        )
        return _CandidateOutcome(skip_reason=reason)

    _record_step("worktree_preflight", "success", mode)
    _log_planned_action(
        removable, mode, _REMOVE_WORKTREE_ACTION, dry_run=state.options.dry_run
    )
    if not state.options.dry_run:
        if not adapter.remove_worktree(candidate.worktree_path):
            _log_candidate_left(
                candidate, mode, _candidate_skip_event(_SkipReason.REMOVAL_FAILED)
            )
            _record_failure("worktree_removal", mode)
            return _CandidateOutcome(skip_reason=_SkipReason.REMOVAL_FAILED)
        _record_signal_step(
            "worktree_removal",
            "success",
            mode,
            removable.evidence,
        )

    if mode is not _PlonkMode.HARD:
        return _CandidateOutcome()

    _log_planned_action(
        removable, mode, _BRANCH_DELETION_ACTION, dry_run=state.options.dry_run
    )
    if state.options.dry_run:
        return _CandidateOutcome()
    if not adapter.delete_branch(candidate.branch_name):
        _record_failure("branch_deletion", mode)
        return _CandidateOutcome(branch_deletion_failed=True)
    _record_step("branch_deletion", "success", mode)
    return _CandidateOutcome()


def _clean_removable(
    state: _SweepState,
    removable: typ.Sequence[_RemovableCandidate],
) -> _CleanupTally:
    """Clean every removable candidate, collecting what the run did."""
    tally = _CleanupTally()
    for pair in removable:
        outcome = _clean_completed_candidate(pair, state)
        if outcome.skip_reason is not None:
            tally.skipped_worktrees.append(
                _SkippedWorktree(pair.candidate.worktree_path, outcome.skip_reason)
            )
            continue
        tally.removed_worktrees.append(pair.candidate.worktree_path)
        tally.removal_evidence.append(
            _RemovalEvidence(pair.candidate.worktree_path, pair.evidence)
        )
        if outcome.branch_deletion_failed:
            tally.failed_branch_deletions.append(pair.candidate.branch_name)
        elif state.mode is _PlonkMode.HARD:
            tally.removed_branches.append(pair.candidate.branch_name)
    return tally
