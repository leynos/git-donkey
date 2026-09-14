"""Every graph question the eight gates read, asked before any gate runs.

An assessment is a pure function of values, so the questions the gates ask are
put here rather than inside the gates: a gate that reached back into the
repository from inside the verdict would make the verdict unbuildable without
one, and a property test could no longer construct an arbitrary graph and ask
what the policy makes of it.

The questions are the gates' own, and :mod:`git_donkey.wheresat_policy` is their
only reader, so the two modules are read together whenever a gate grows a
question. Nothing is asked speculatively: a run that recovered no parent head
pays for no question about one, and a pair the gates never read is never asked
about. Each question is put once per distinct operand pair, because the same
pair recurs across candidates and across gates.

A question the repository cannot answer becomes a fault and not a negative
answer, and a failed listing of the child's own history is fatal to the whole
set: every reported boundary is a partition of that history, and a partition
that cannot be shown must not be reported.
"""

from __future__ import annotations

import dataclasses
import functools
import typing as typ

from git_donkey.wheresat_collect import (
    CollectedEvidence,
    CollectionContext,
    _asked,
    _Fault,
)
from git_donkey.wheresat_records import (
    Ancestry,
    CommitRange,
    GraphFacts,
    is_record_kind,
    range_key,
)


@dataclasses.dataclass(frozen=True, slots=True)
class CollectedFacts:
    """The graph answers the gates read, and the questions that failed."""

    facts: GraphFacts
    faults: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class _AncestryAnswers:
    """The ancestry answers read, and the questions that could not be put."""

    answers: typ.Mapping[tuple[str, str], Ancestry]
    faults: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class _RangeAnswers:
    """The replay ranges read, and the listings that could not be made.

    ``without_parent`` is empty when the subtraction was not asked for, which is
    what a run that recovered no parent head or no integration commit gets: a
    mapping of nothing is how this module says "that question was not put",
    because a range listed as empty would be a different answer.
    """

    contents: typ.Mapping[str, CommitRange]
    without_parent: typ.Mapping[str, CommitRange]
    faults: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class _PatchAnswers:
    """The cumulative patch identifiers read, and those that could not be read."""

    identifiers: typ.Mapping[str, str | None]
    landed: str | None
    faults: tuple[str, ...] = ()


def assemble_facts(
    context: CollectionContext, evidence: CollectedEvidence
) -> CollectedFacts:
    """Return every graph answer the eight gates will read, and the faults.

    The questions are the gates' own, and
    :mod:`git_donkey.wheresat_policy` is their only reader, so the two modules
    are read together whenever a gate grows a question. Nothing is asked
    speculatively: a run that recovered no parent head pays for no question
    about one, and a pair the gates never read is never asked about.

    Parameters
    ----------
    context : CollectionContext
        The run's inputs, whose ports put the questions.
    evidence : CollectedEvidence
        What the rungs produced, which decides which questions exist.

    Returns
    -------
    CollectedFacts
        The answers, and a reason for every question that could not be put. A
        failed listing of the child's own history is fatal to the whole set,
        because every reported boundary is a partition of that history and a
        partition that cannot be shown must not be reported.

    """
    history, fault = _child_history(context)
    if fault is not None:
        return CollectedFacts(_empty_facts(context, evidence), (fault.reason,))

    ancestry = _ancestry_answers(context, evidence)
    ranges = _replay_ranges(context, evidence)
    patches = _patch_answers(context, evidence)
    return CollectedFacts(
        facts=GraphFacts(
            parent_head=evidence.parent_head.commit if evidence.parent_head else None,
            landed=_landed(context),
            ancestry=ancestry.answers,
            range_contents=ranges.contents,
            range_minus_parent=ranges.without_parent,
            child_history=history,
            cumulative_patch=patches.identifiers,
            landed_patch=patches.landed,
            record_recorded_from=evidence.recorded_from,
        ),
        faults=ancestry.faults + ranges.faults + patches.faults,
    )


def _ancestry_answers(
    context: CollectionContext, evidence: CollectedEvidence
) -> _AncestryAnswers:
    """Return every ancestry answer the gates read, by the pair asked about.

    Gate 4 asks whether the candidate is an ancestor of the child, gate 6
    whether it is an ancestor of the parent head, gate 3 whether the parent's
    integration commit is on the target, and gate 8 whether the recorded-from
    commit is below the child and whether the candidate is below the
    integration. Nothing else is asked.

    Returns
    -------
    _AncestryAnswers
        The answers, keyed by the ordered pair asked about, and a reason for
        every pair the repository could not answer. A pair that is absent from
        the mapping reads as unknown, never as a negative answer.

    """
    answers: dict[tuple[str, str], Ancestry] = {}
    faults: list[str] = []
    for left, right in _ancestry_pairs(context, evidence):
        answer, fault = _asked(
            f"cannot tell whether {left} is an ancestor of {right}",
            functools.partial(context.graph.is_ancestor, left, right),
        )
        if fault is not None:
            faults.append(fault.reason)
        elif answer is not None:
            answers[left, right] = answer
    return _AncestryAnswers(answers=answers, faults=tuple(faults))


def _replay_ranges(
    context: CollectionContext, evidence: CollectedEvidence
) -> _RangeAnswers:
    """Return the two range listings the gates read, and the listings that failed.

    Gates 5 and 7 read the child's own commits above the candidate boundary, and
    gate 7 reads the same range with the parent head's history subtracted. The
    subtraction is only asked for when gate 7 can apply, and gate 7 is only ever
    applicable with both a parent head and an integration commit, so a run that
    recovered neither pays for neither question.

    Returns
    -------
    _RangeAnswers
        The ranges as listed, the ranges without the parent head's history, and
        a reason for every listing that could not be made.

    """
    child_tip = context.request.child_tip
    subtract = _subtraction(context, evidence)
    contents: dict[str, CommitRange] = {}
    without_parent: dict[str, CommitRange] = {}
    faults: list[str] = []
    for commit in _commits(evidence):
        key = range_key(commit, child_tip)
        answer, fault = _asked(
            f"cannot list the commits {key}",
            functools.partial(context.graph.commits_in_range, commit, child_tip),
        )
        if fault is not None:
            faults.append(fault.reason)
            continue
        contents[key] = CommitRange(tuple(answer or ()))
        if subtract is None:
            continue
        answer, fault = _asked(
            f"cannot list the commits {key} without {subtract}",
            functools.partial(
                context.graph.commits_in_range,
                commit,
                child_tip,
                not_reachable_from=subtract,
            ),
        )
        if fault is not None:
            faults.append(fault.reason)
        else:
            without_parent[key] = CommitRange(tuple(answer or ()))
    return _RangeAnswers(
        contents=contents, without_parent=without_parent, faults=tuple(faults)
    )


def _patch_answers(
    context: CollectionContext, evidence: CollectedEvidence
) -> _PatchAnswers:
    """Return the cumulative patch identifiers gate 7 compares.

    The candidate's side is the cumulative change from the child's tip to the
    candidate, and the other side is the change the integration commit itself
    introduces against its first parent. Both are asked for only when gate 7
    can apply, because patch identification is the most expensive question the
    run can put.

    Returns
    -------
    _PatchAnswers
        The cumulative identifier per candidate commit, the identifier of the
        patch the integration commit introduces, and a reason for every
        identifier that could not be computed.

    """
    landed = _landed(context)
    if landed is None or evidence.parent_head is None:
        return _PatchAnswers(identifiers={}, landed=None)
    child_tip = context.request.child_tip
    identifiers: dict[str, str | None] = {}
    faults: list[str] = []
    for commit in _commits(evidence):
        answer, fault = _asked(
            f"cannot identify the cumulative patch {range_key(commit, child_tip)}",
            functools.partial(
                context.graph.cumulative_patch_identifier, commit, child_tip
            ),
        )
        if fault is not None:
            faults.append(fault.reason)
        else:
            identifiers[commit] = answer
    patch, fault = _asked(
        f"cannot identify the patch {landed} introduces",
        functools.partial(
            context.graph.cumulative_patch_identifier, f"{landed}^", landed
        ),
    )
    if fault is not None:
        faults.append(fault.reason)
    return _PatchAnswers(identifiers=identifiers, landed=patch, faults=tuple(faults))


def _child_history(context: CollectionContext) -> tuple[CommitRange, _Fault | None]:
    """Return the child's whole history, oldest first, or why it was not listed."""
    answer, fault = _asked(
        f"cannot list the history of {context.request.child_tip}",
        functools.partial(context.graph.history, context.request.child_tip),
    )
    return CommitRange(tuple(answer or ())), fault


def _empty_facts(context: CollectionContext, evidence: CollectedEvidence) -> GraphFacts:
    """Return the fact set of a run whose child history could not be listed.

    Every question that could have been asked is left unanswered rather than
    answered against a history the run could not read, so no gate can pass on a
    partition the report would then have to describe from nothing.

    Returns
    -------
    GraphFacts
        The facts a run whose child history is unknown can honestly claim: the
        parent head, the integration commit, and the recorded-from commit, with
        every answer that depends on the child's history left absent.

    """
    return GraphFacts(
        parent_head=evidence.parent_head.commit if evidence.parent_head else None,
        landed=_landed(context),
        ancestry={},
        range_contents={},
        range_minus_parent={},
        child_history=CommitRange(()),
        cumulative_patch={},
        landed_patch=None,
        record_recorded_from=evidence.recorded_from,
    )


def _ancestry_pairs(
    context: CollectionContext, evidence: CollectedEvidence
) -> tuple[tuple[str, str], ...]:
    """Return every ordered ancestry question the gates will read.

    One pair per candidate is asked against the child tip, because every
    candidate must lie on the child's history. The parent head and the
    integration commit are asked about only when the run recovered them, and
    the integration commit is only asked about a candidate a stack record named,
    because gate 8's second clause is about a record's claim.

    Returns
    -------
    tuple[tuple[str, str], ...]
        Ordered ``(ancestor, descendant)`` pairs, deduplicated with the first
        occurrence kept so the report's reasons read in the order they arose.

    """
    child_tip = context.request.child_tip
    commits = _commits(evidence)
    landed = _landed(context)
    pairs = [(commit, child_tip) for commit in commits]
    if evidence.parent_head is not None:
        head = evidence.parent_head.commit
        pairs.extend((commit, head) for commit in commits)
    if landed is not None:
        pairs.append((landed, context.request.target))
        pairs.extend(
            (one.commit, landed)
            for one in evidence.candidates
            if is_record_kind(one.kind)
        )
    if evidence.recorded_from is not None and evidence.recorded_from != child_tip:
        pairs.append((evidence.recorded_from, child_tip))
    return tuple(dict.fromkeys(pairs))


def _commits(evidence: CollectedEvidence) -> tuple[str, ...]:
    """Return the distinct commits the candidates name, in evidence order."""
    return tuple(dict.fromkeys(one.commit for one in evidence.candidates))


def _subtraction(context: CollectionContext, evidence: CollectedEvidence) -> str | None:
    """Return the parent head to subtract from a replay range, when there is one.

    Gate 7 compares the replay range against the parent's history, so the
    subtraction is only worth asking for when the run recovered both a parent
    head and the integration commit the child was cut from.

    Returns
    -------
    str | None
        The commit to subtract, or ``None`` when gate 7 has no question to ask.

    """
    if evidence.parent_head is None or _landed(context) is None:
        return None
    return evidence.parent_head.commit


def _landed(context: CollectionContext) -> str | None:
    """Return the parent's integration commit, when one was resolved."""
    return context.parent.landed if context.parent is not None else None
