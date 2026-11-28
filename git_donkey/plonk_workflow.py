"""Options, structured logs, and bounded observations for git-plonk.

The workflow modules share one vocabulary for what a sweep reports. This module
keeps the invocation options together and translates the records in
:mod:`git_donkey.plonk_records` into structured log fields and bounded
observations. Branch names, worktree paths, evidence details, and Git output
may appear in logs, which are local diagnostics; the observations delivered to
:mod:`git_donkey.observability` carry only values from its fixed vocabularies.
"""

from __future__ import annotations

import dataclasses
import logging
import typing as typ

from git_donkey import observability
from git_donkey.plonk_records import (
    _MODE_LABELS,
    _SIGNAL_LABELS,
    _UNVERIFIED_REASON_LABELS,
    MergedPullRequestLookup,
    _CompletionEvidence,
    _PlonkCandidate,
    _PlonkContext,
    _PlonkMode,
    _RemovableCandidate,
    _UnverifiedReason,
)

_LOGGER = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True, slots=True)
class _PlonkRunOptions:
    """Optional completion tiers enabled for one git-plonk invocation.

    Attributes
    ----------
    no_github : bool
        Disable the GitHub merged-pull-request fallback even when the principal
        remote is GitHub and ``gh`` is installed.
    upstream_gone : bool
        Treat a branch whose configured upstream no longer exists as completed.
        The check is opt-in because a deleted branch cannot distinguish a
        merged change from an abandoned one, and a remote that hides refs
        cannot prove absence.

    """

    no_github: bool = False
    upstream_gone: bool = False


_DEFAULT_RUN_OPTIONS = _PlonkRunOptions()


@dataclasses.dataclass(frozen=True, slots=True)
class _CleanupOptions:
    """Options for one completed-cleanup sweep.

    Attributes
    ----------
    dry_run : bool
        Plan the work without removing paths or branches.
    no_github : bool
        Disable the GitHub merged-pull-request fallback.
    upstream_gone : bool
        Treat a gone upstream branch as completion evidence.
    github_lookup : MergedPullRequestLookup | None
        Injected merged-pull-request lookup. ``None`` uses the real ``gh`` tool
        when the fallback is enabled.

    """

    dry_run: bool = False
    no_github: bool = False
    upstream_gone: bool = False
    github_lookup: MergedPullRequestLookup | None = None


_GITHUB_FALLBACK_NOTICE = (
    "gh is not installed; merged pull-request lookups are unavailable"
)
"""Notice the composition layer prints when the GitHub tier is unavailable."""

_DEFAULT_CLEANUP_OPTIONS = _CleanupOptions()


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


def _branch_fields(candidate: _PlonkCandidate) -> dict[str, object]:
    """Return the branch and marker fields shared by candidate logs."""
    return {
        "branch": candidate.branch_name,
        "marker": candidate.marker,
    }


def _candidate_fields(candidate: _PlonkCandidate) -> dict[str, object]:
    """Return the fields shared by candidate worktree logs."""
    return {
        **_branch_fields(candidate),
        "worktree": candidate.worktree_path.as_posix(),
    }


@dataclasses.dataclass(frozen=True, slots=True)
class _PlannedAction:
    """Structured-log event for one planned or performed removal action."""

    operation: str
    real_message: str
    planned_message: str
    include_worktree: bool = True
    include_detail: bool = False


_REMOVE_WORKTREE_ACTION = _PlannedAction(
    operation="remove_worktree",
    real_message="Removing completed git-plonk worktree",
    planned_message="Planning completed git-plonk worktree removal",
    include_detail=True,
)
_BRANCH_DELETION_ACTION = _PlannedAction(
    operation="delete_branch",
    real_message="Deleting completed git-plonk branch",
    planned_message="Planning completed git-plonk branch deletion",
    include_worktree=False,
)


@dataclasses.dataclass(frozen=True, slots=True)
class _CandidateLeftEvent:
    """Structured-log event for a skipped or unverified candidate."""

    message: str
    operation: str
    reason: str


def _log_planned_action(
    removable: _RemovableCandidate,
    mode: _PlonkMode,
    action: _PlannedAction,
    *,
    dry_run: bool,
) -> None:
    """Log one planned or performed removal action for a candidate."""
    candidate = removable.candidate
    fields: dict[str, object] = {
        "mode": mode.value,
        "operation": action.operation,
        "dry_run": dry_run,
        "signal": removable.evidence.signal,
    }
    if action.include_detail:
        fields["detail"] = removable.evidence.detail
    if action.include_worktree:
        fields.update(_candidate_fields(candidate))
    else:
        fields.update(_branch_fields(candidate))
    _LOGGER.info(
        action.planned_message if dry_run else action.real_message,
        extra=fields,
    )


def _log_candidate_left(
    candidate: _PlonkCandidate,
    mode: _PlonkMode,
    event: _CandidateLeftEvent,
) -> None:
    """Log a candidate that was left in place."""
    _LOGGER.info(
        event.message,
        extra={
            "mode": mode.value,
            "operation": event.operation,
            "reason": event.reason,
            **_candidate_fields(candidate),
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


def _record_signal_step(
    operation: observability.Operation,
    outcome: observability.Outcome,
    mode: _PlonkMode,
    signal: _CompletionEvidence,
) -> None:
    """Record a cleanup step together with the evidence that enabled it."""
    observability.get_recorder().record(
        observability.Observation(
            operation=operation,
            outcome=outcome,
            mode=_MODE_LABELS[mode],
            signal=_SIGNAL_LABELS[signal.signal],
        )
    )


def _record_unverified(
    mode: _PlonkMode,
    reason: _UnverifiedReason,
) -> None:
    """Record a candidate no completion tier could resolve."""
    observability.get_recorder().record(
        observability.Observation(
            operation="completion_resolution",
            outcome="unverified",
            mode=_MODE_LABELS[mode],
            unverified_reason=_UNVERIFIED_REASON_LABELS[reason],
        )
    )
