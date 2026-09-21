"""Property tests for the ``git-plonk`` completed-cleanup batch rules.

``test_plonk_cleanup.py`` and ``test_plonk_record_lifecycle.py`` pin the cleanup
contract with readable examples; the rules themselves are about a *batch*, so
these tests generalise them over generated batches: every candidate is
classified on its own, a skip or a refusal never shortens the sweep or reorders
the rest, hard mode deletes a branch only once its worktree is gone, a refused
deletion is neither a skip nor a removed branch, and a dry run reports the plan
without mutating anything.

Each example draws one state per candidate, feeds the matching doubles, and
compares the run against a reference model of what those states should produce:
both the result the workflow reports and the calls it makes on Git.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from hypothesis import given
from hypothesis import strategies as st

from git_donkey import plonk, plonk_records
from tests.unit.plonk_cleanup_helpers import (
    RecordingGitAdapter,
    RecordingStackStore,
    candidate,
    cleanup_surfaces,
    marker_for,
    run_cleanup,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

# The state each candidate is generated in. ``CLEAN`` and ``DELETION_FAILURE``
# differ only in what Git does to the branch; ``INCOMPLETE`` is a worktree whose
# marker never reaches trunk history, so cleanup never considers it.
_CLEAN = "clean"
_DIRTY = "dirty"
_UNAVAILABLE = "unavailable"
_INCOMPLETE = "incomplete"
_REMOVAL_FAILURE = "removal-failure"
_DELETION_FAILURE = "deletion-failure"

# States the preflight refuses, and the reason it reports for each.
_PREFLIGHT_SKIPS = {
    _DIRTY: plonk_records._SkipReason.DIRTY,
    _UNAVAILABLE: plonk_records._SkipReason.UNAVAILABLE,
}

_ALL_STATES = (
    _CLEAN,
    _DIRTY,
    _UNAVAILABLE,
    _INCOMPLETE,
    _REMOVAL_FAILURE,
    _DELETION_FAILURE,
)

# Candidate count is bounded so each example stays a cheap in-memory sweep while
# still mixing several states in one batch.
_BATCH = st.lists(st.sampled_from(_ALL_STATES), max_size=5)
_MODES = st.sampled_from((plonk._PlonkMode.DEFAULT, plonk._PlonkMode.HARD))


@dataclasses.dataclass(frozen=True, slots=True)
class _Case:
    """One generated candidate and the state it is in."""

    state: str
    candidate: plonk_records._PlonkCandidate


@dataclasses.dataclass(frozen=True, slots=True)
class _Model:
    """What the reference model expects a batch of states to produce."""

    result: plonk._PlonkResult
    removed: list[Path]
    deleted: list[str]


@dataclasses.dataclass(frozen=True, slots=True)
class _BranchPlan:
    """What the run's branch stage does with one candidate's branch."""

    entombed: bool
    removed: bool
    refused: bool


def _cases(states: cabc.Sequence[str]) -> list[_Case]:
    """Return one candidate per state, with unique branch names and paths.

    Parameters
    ----------
    states : collections.abc.Sequence[str]
        State to generate each candidate in.

    Returns
    -------
    list[_Case]
        The candidates, in the order the states were drawn.

    """
    return [
        _Case(
            state=state,
            candidate=candidate(f"issue-{1000 + index}-candidate", 1000 + index),
        )
        for index, state in enumerate(states)
    ]


def _adapter(cases: cabc.Iterable[_Case]) -> RecordingGitAdapter:
    """Return an adapter double matching the states in ``cases``.

    Trunk history carries every marker but the incomplete candidate's, and each
    state's Git refusal is configured on the matching worktree or branch.

    Parameters
    ----------
    cases : collections.abc.Iterable[_Case]
        Candidates and the states they are in.

    Returns
    -------
    RecordingGitAdapter
        A double that answers the preflight and Git calls the states require.

    """
    case_list = list(cases)
    return RecordingGitAdapter(
        [
            marker_for(case.candidate)
            for case in case_list
            if case.state is not _INCOMPLETE
        ],
        skip_reasons={
            case.candidate.worktree_path: _PREFLIGHT_SKIPS[case.state]
            for case in case_list
            if case.state in _PREFLIGHT_SKIPS
        },
        removal_failures=[
            case.candidate.worktree_path
            for case in case_list
            if case.state is _REMOVAL_FAILURE
        ],
        deletion_failures=[
            case.candidate.branch_name
            for case in case_list
            if case.state is _DELETION_FAILURE
        ],
    )


def _skip_reason_for(case: _Case, *, dry_run: bool) -> plonk_records._SkipReason | None:
    """Return the reason the sweep leaves ``case``'s worktree in place.

    Parameters
    ----------
    case : _Case
        Candidate and the state it is in.
    dry_run : bool
        Whether the sweep plans the work without mutating Git. A dry run never
        asks Git to remove a worktree, so no removal can be refused.

    Returns
    -------
    plonk_records._SkipReason | None
        The reason the preflight refused the worktree, the reason a refused
        removal reports, or ``None`` when the worktree is removed.

    """
    if case.state in _PREFLIGHT_SKIPS:
        return _PREFLIGHT_SKIPS[case.state]
    if case.state is _REMOVAL_FAILURE and not dry_run:
        return plonk_records._SkipReason.REMOVAL_FAILED
    return None


def _plans_branch_removal(
    case: _Case,
    *,
    mode: plonk._PlonkMode,
    dry_run: bool,
) -> bool:
    """Return whether the sweep reports ``case``'s branch as removed.

    Parameters
    ----------
    case : _Case
        Candidate and the state it is in.
    mode : plonk._PlonkMode
        Cleanup mode the sweep runs in; only hard mode deletes branches.
    dry_run : bool
        Whether the sweep plans the work without mutating Git, and so asks for
        the deletion without hearing a refusal.

    Returns
    -------
    bool
        ``True`` when the branch joins the removed branches: always in a hard
        dry run, and in a real hard run only when Git accepts the deletion.

    """
    if mode is not plonk._PlonkMode.HARD:
        return False
    if case.state is _DELETION_FAILURE:
        return dry_run
    return True


def _refuses_branch_deletion(
    case: _Case,
    *,
    mode: plonk._PlonkMode,
    dry_run: bool,
) -> bool:
    """Return whether Git refuses the branch deletion the sweep asks for.

    Parameters
    ----------
    case : _Case
        Candidate and the state it is in.
    mode : plonk._PlonkMode
        Cleanup mode the sweep runs in; only hard mode deletes branches.
    dry_run : bool
        Whether the sweep plans the work without mutating Git, and so makes no
        call Git could refuse.

    Returns
    -------
    bool
        ``True`` when a real hard run asks Git to delete ``case``'s branch and
        Git says no, leaving the branch in place and the failure reported.

    """
    return (
        mode is plonk._PlonkMode.HARD
        and case.state is _DELETION_FAILURE
        and not dry_run
    )


def _branch_plan(
    case: _Case,
    *,
    mode: plonk._PlonkMode,
    dry_run: bool,
) -> _BranchPlan:
    """Return what the run's branch stage does with ``case``.

    Parameters
    ----------
    case : _Case
        Candidate and the state it is in.
    mode : plonk._PlonkMode
        Cleanup mode the sweep runs in; only hard mode reaches the branch.
    dry_run : bool
        Whether the sweep plans the work without mutating Git.

    Returns
    -------
    _BranchPlan
        Whether the branch is entombed, reported as removed, or reported as a
        deletion Git refused.

    """
    if mode is not plonk._PlonkMode.HARD:
        return _BranchPlan(entombed=False, removed=False, refused=False)
    return _BranchPlan(
        entombed=True,
        removed=_plans_branch_removal(case, mode=mode, dry_run=dry_run),
        refused=_refuses_branch_deletion(case, mode=mode, dry_run=dry_run),
    )


def _model(
    cases: cabc.Sequence[_Case],
    *,
    mode: plonk._PlonkMode,
    dry_run: bool,
    expire: str,
) -> _Model:
    """Return what the cleanup workflow should report and ask Git to do.

    Every branch a hard run reaches is entombed before it is deleted, and the
    double's ``branch_tip`` always finds one, so a branch that reaches the
    branch stage of the model is a branch whose tip was preserved.

    Parameters
    ----------
    cases : collections.abc.Sequence[_Case]
        Candidates and their states, in the order cleanup receives them.
    mode : plonk._PlonkMode
        Cleanup mode the sweep runs in.
    dry_run : bool
        Whether the sweep plans the work without mutating Git.
    expire : str
        Retention window the store double reports, which the run echoes back.

    Returns
    -------
    _Model
        The result the run should report, and the removals and deletions the
        adapter double should record.

    """
    removed_worktrees: list[Path] = []
    removed_branches: list[str] = []
    skipped_worktrees: list[plonk_records._SkippedWorktree] = []
    failed_branch_deletions: list[str] = []
    entombed_branches: list[str] = []

    for case in cases:
        candidate_ = case.candidate
        if case.state is _INCOMPLETE:
            continue
        skip_reason = _skip_reason_for(case, dry_run=dry_run)
        if skip_reason is not None:
            skipped_worktrees.append(
                plonk_records._SkippedWorktree(candidate_.worktree_path, skip_reason)
            )
            continue

        removed_worktrees.append(candidate_.worktree_path)
        plan = _branch_plan(case, mode=mode, dry_run=dry_run)
        if plan.entombed:
            entombed_branches.append(candidate_.branch_name)
        if plan.removed:
            removed_branches.append(candidate_.branch_name)
        elif plan.refused:
            failed_branch_deletions.append(candidate_.branch_name)

    return _Model(
        result=plonk._PlonkResult(
            mode=mode,
            is_dry_run=dry_run,
            inspected_worktrees=len(cases),
            removed_worktrees=tuple(removed_worktrees),
            removed_branches=tuple(removed_branches),
            skipped_worktrees=tuple(skipped_worktrees),
            failed_branch_deletions=tuple(failed_branch_deletions),
            entombed_branches=tuple(entombed_branches),
            tombstone_expire=expire,
        ),
        # A dry run mutates neither surface, and both recorded lists mirror the
        # reported ones: a branch is deleted exactly when it is reported removed.
        removed=[] if dry_run else list(removed_worktrees),
        deleted=[] if dry_run else list(removed_branches),
    )


@given(states=_BATCH, mode=_MODES, dry_run=st.booleans())
def test_every_candidate_is_classified_independently(
    states: list[str],
    mode: plonk._PlonkMode,
    *,
    dry_run: bool,
) -> None:
    """A batch should produce one outcome per candidate, in the order received."""
    cases = _cases(states)
    adapter = _adapter(cases)
    records = RecordingStackStore()
    expected = _model(cases, mode=mode, dry_run=dry_run, expire=records.expire)

    result = run_cleanup(
        [case.candidate for case in cases],
        cleanup_surfaces(adapter, records),
        mode,
        dry_run=dry_run,
    )

    assert result == expected.result, (
        "each candidate contributes its own outcome, and a skip or refusal "
        "leaves the candidates behind it in the batch untouched"
    )
    assert adapter.removed == expected.removed, (
        "the sweep hands Git exactly the worktrees the states allow: a dry run "
        "and a preflight or removal refusal reach no removal at all"
    )
    assert adapter.deleted == expected.deleted, (
        "hard mode deletes a branch only once its worktree is gone, so a "
        "refused deletion leaves its branch behind and the sweep continues"
    )
    written = [branch for branch, _tip in records.entombed]
    assert written == ([] if dry_run else list(expected.result.entombed_branches)), (
        "hard mode preserves the tip of every branch it deletes, and of no "
        "branch it does not reach; a dry run writes none of them"
    )
