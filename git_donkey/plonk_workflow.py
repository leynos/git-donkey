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

from git_donkey import observability, plonk_github
from git_donkey.plonk_records import (
    _MODE_LABELS,
    _SIGNAL_LABELS,
    _UNVERIFIED_REASON_LABELS,
    _CompletionEvidence,
    _PlonkCandidate,
    _PlonkContext,
    _PlonkMode,
    _RemovableCandidate,
    _SkipReason,
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
    github_lookup : plonk_github.MergedPullRequestLookup | None
        Injected merged-pull-request lookup. ``None`` uses the real ``gh`` tool
        when the fallback is enabled.

    """

    dry_run: bool = False
    no_github: bool = False
    upstream_gone: bool = False
    github_lookup: plonk_github.MergedPullRequestLookup | None = None


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


def _log_planned_candidate(
    removable: _RemovableCandidate,
    mode: _PlonkMode,
    *,
    dry_run: bool,
) -> None:
    """Log the planned removal of one clean completed candidate."""
    candidate = removable.candidate
    _LOGGER.info(
        "Planning completed git-plonk worktree removal"
        if dry_run
        else "Removing completed git-plonk worktree",
        extra={
            "mode": mode.value,
            "operation": "remove_worktree",
            "dry_run": dry_run,
            "signal": removable.evidence.signal,
            "detail": removable.evidence.detail,
            **_candidate_fields(candidate),
        },
    )


def _log_planned_branch_deletion(
    removable: _RemovableCandidate,
    mode: _PlonkMode,
    *,
    dry_run: bool,
) -> None:
    """Log the planned branch deletion for one removed candidate."""
    candidate = removable.candidate
    _LOGGER.info(
        "Planning completed git-plonk branch deletion"
        if dry_run
        else "Deleting completed git-plonk branch",
        extra={
            "mode": mode.value,
            "operation": "delete_branch",
            "dry_run": dry_run,
            "signal": removable.evidence.signal,
            **_branch_fields(candidate),
        },
    )


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
            "reason": reason.value,
            **_candidate_fields(candidate),
        },
    )


def _log_unverified_candidate(
    candidate: _PlonkCandidate,
    reason: _UnverifiedReason,
    mode: _PlonkMode,
) -> None:
    """Log a candidate no completion tier could resolve."""
    _LOGGER.info(
        "Leaving git-plonk candidate unverified",
        extra={
            "mode": mode.value,
            "operation": "completion_resolution",
            "reason": reason.value,
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
