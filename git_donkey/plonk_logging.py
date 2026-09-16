"""The lines a completed-cleanup run logs and the observations it records.

A ``git-plonk`` cleanup run reports the same step through two channels: a
structured log line carrying the fields an operator greps for, and a bounded
:mod:`git_donkey.observability` observation that says the step was reached and
how it ended. Both are vocabulary rather than workflow — the message shapes,
the ``extra`` fields, and the operation names live here so that the run's
workflow holds only the decisions about when a step is taken, and so that a
module emitting these lines never reaches upward into the workflow for them.

The log lines are emitted on this module's own logger, which is a child of the
package logger like every other one here: a run that configures logging at the
package level collects them unchanged.

"""

from __future__ import annotations

import logging
import typing as typ

from git_donkey import observability
from git_donkey.plonk_records import _MODE_LABELS, _PlonkMode

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from git_donkey.plonk_records import _PlonkCandidate, _PlonkContext, _SkipReason

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
    candidates: cabc.Sequence[_PlonkCandidate],
    completed_candidates: cabc.Sequence[_PlonkCandidate],
    removable_candidates: cabc.Sequence[_PlonkCandidate],
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
