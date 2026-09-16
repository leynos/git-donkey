"""Where the parent's tip is, in the order the recovery procedure fixes.

:func:`git_donkey.wheresat_parents.parent` answers *which* pull request the
child is stacked on. This module answers the question that follows it: which
commit that parent's tip was, and which ref says so. The answer is what the
gates ask their ancestry questions about — whether the boundary is an ancestor
of the target, whether the candidate is an ancestor of the parent, whether the
child's commits are reachable from the parent head — so a run that finds no
head has gates it cannot apply and a boundary it cannot judge.

Three rungs, strongest first. A head the run already has wins: it was fetched
from the pull request the run identified, and seeking a tombstone below it
would be asking a weaker question after a stronger one was answered. Then the
tombstone ``git plonk`` wrote for the parent branch, which is the artefact that
survives the branch being deleted — the degradation this command exists for.
Then that branch's remote-tracking ref, which a parent that was never plonked
and a child whose record names no parent pull request usually still have,
because the branch was pushed somewhere before the child was stacked on it.

A rung that finds nothing falls through to the next one. A rung that hits a
fault stops the ladder and returns the reason, because "no tombstone" and "the
tombstone would not open" are different answers (INV-5). And no head with no
reason is the honest answer for a run whose child names no parent branch, whose
parent pull request named no head, and whose parent branch left neither a
tombstone nor a remote-tracking ref: absence is an answer, and only a question
that could not be put is a fault.

Nothing here decides whether the head it found is acceptable, and nothing here
reads or writes anything but Git and the stack record.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from git_donkey import stack_records, stack_store
from git_donkey.wheresat_errors import WheresatGraphError

if typ.TYPE_CHECKING:
    from git_donkey.wheresat_graph import WheresatGraph


@dataclasses.dataclass(frozen=True, slots=True)
class ParentHead:
    """The parent's tip, and the ref the run read it from.

    Parameters
    ----------
    commit : str
        The parent's head commit, which is what the gates ask their ancestry
        questions about.
    ref : str | None
        The ref it was read from, when it was read from one. A fork point is
        read out of a reflog, so a head that arrived as a bare object ID has no
        fork-point question to ask about it.

    """

    commit: str
    ref: str | None


def parent_head(
    graph: WheresatGraph,
    records: stack_store.StackRecordReader,
    record: stack_records.RecordResult,
    fetched: ParentHead | None = None,
) -> tuple[ParentHead | None, str | None]:
    """Return the parent's tip, sought in the order the procedure fixes.

    Parameters
    ----------
    graph : WheresatGraph
        Read-only history questions.
    records : stack_store.StackRecordReader
        Read-only access to the tombstones ``git plonk`` left behind.
    record : stack_records.RecordResult
        The child's record, which names the parent branch the lower rungs are
        sought through.
    fetched : ParentHead | None, optional
        The head the run already has, which is the first rung when there is
        one.

    Returns
    -------
    tuple[ParentHead | None, str | None]
        The parent's head and the ref it was read from, or no head and the
        reason it could not be recovered.

    """
    if fetched is not None:
        return fetched, None
    branch = parent_branch(record)
    if branch is None:
        return None, None
    head, reason = _tombstoned_head(records, branch)
    if head is not None or reason is not None:
        return head, reason
    return _remote_tracked_head(graph, branch)


def _tombstoned_head(
    records: stack_store.StackRecordReader, branch: str
) -> tuple[ParentHead | None, str | None]:
    """Return the parent's tip as the tombstone ``git plonk`` left behind names it.

    Parameters
    ----------
    records : stack_store.StackRecordReader
        Read-only access to the tombstones.
    branch : str
        Parent branch the child's record names.

    Returns
    -------
    tuple[ParentHead | None, str | None]
        The head the tombstone names and the ref it was read from, or no head
        and the reason the tombstone could not be read. No head and no reason
        says the parent branch has no tombstone, which is an absence and not a
        fault: the ladder has a rung below this one to try.

    """
    try:
        commit = records.tombstone(branch)
    except (stack_store.StackRecordError, WheresatGraphError, ValueError) as exc:
        return None, f"the tombstone for {branch} could not be read: {exc}"
    if commit is None:
        return None, None
    return ParentHead(commit, stack_records.tombstone_ref_path(branch)), None


def _remote_tracked_head(
    graph: WheresatGraph, branch: str
) -> tuple[ParentHead | None, str | None]:
    """Return the parent's tip as the branch's remote-tracking ref names it.

    Parameters
    ----------
    graph : WheresatGraph
        Read-only history questions.
    branch : str
        Parent branch the child's record names.

    Returns
    -------
    tuple[ParentHead | None, str | None]
        The head the remote-tracking ref resolves to and the ref it was read
        from, or no head and the reason the ref could not be read. No head and
        no reason says the branch has no remote-tracking ref — a parent branch
        that was never pushed, or pushed from a checkout that never fetched it
        back.

    """
    try:
        ref = graph.remote_tracking_ref(branch)
        if ref is None:
            return None, None
        commit = graph.resolve(ref)
    except WheresatGraphError as exc:
        return None, f"the remote-tracking ref for {branch} could not be read: {exc}"
    return ParentHead(commit, ref), None


def parent_branch(record: stack_records.RecordResult) -> str | None:
    """Return the parent branch the child's record names, when it names one.

    A record written after the parent was opened names a pull request instead,
    and a pull request is not a ref: nothing local says where that parent's tip
    was, so such a run has no parent head to offer the gates.

    Parameters
    ----------
    record : stack_records.RecordResult
        The child's record, as it was read.

    Returns
    -------
    str | None
        The parent branch name, or ``None`` when the record names none.

    """
    match record:
        case stack_records.StackRecord(parent=stack_records.StackParent(branch=branch)):
            return branch
    return None
