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
    Fault,
    ask,
)
from git_donkey.wheresat_records import (
    Ancestry,
    CommitRange,
    GraphFacts,
    is_record_kind,
    range_key,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc


@dataclasses.dataclass(frozen=True, slots=True)
class CollectedFacts:
    """The graph answers the gates read, and the questions that failed."""

    facts: GraphFacts
    faults: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class _AncestryAnswers:
    """The ancestry answers read, and the questions that could not be put."""

    answers: cabc.Mapping[tuple[str, str], Ancestry]
    faults: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class _RangeAnswers:
    """The replay ranges read, and the listings that could not be made.

    ``without_parent`` is empty when the subtraction was not asked for, which is
    what a run that recovered no parent head or no integration commit gets: a
    mapping of nothing is how this module says "that question was not put",
    because a range listed as empty would be a different answer.
    """

    contents: cabc.Mapping[str, CommitRange]
    without_parent: cabc.Mapping[str, CommitRange]
    faults: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class _PatchAnswers:
    """The cumulative patch identifiers read, and those that could not be read."""

    identifiers: cabc.Mapping[str, str | None]
    landed: str | None
    faults: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class _TreeAnswers:
    """The commits in each replay range carrying the landed commit's tree.

    Keyed by the range key gate 7 reads, with an empty tuple for a range that
    was compared and holds no such commit. A range that was never listed, or
    one whose comparison could not be finished, has no key — the same
    distinction the other answers draw, where a mapping of nothing is "that
    question was not put" and an empty listing is an answer.
    """

    twins: cabc.Mapping[str, tuple[str, ...]]
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
    trees = _tree_answers(context, evidence, ranges.contents)
    return CollectedFacts(
        facts=GraphFacts(
            parent_head=evidence.parent_head.commit if evidence.parent_head else None,
            landed=_landed(context),
            ancestry=ancestry.answers,
            range_contents=ranges.contents,
            range_minus_parent=ranges.without_parent,
            landed_twins=trees.twins,
            child_history=history,
            cumulative_patch=patches.identifiers,
            landed_patch=patches.landed,
            record_recorded_from=evidence.recorded_from,
        ),
        faults=ancestry.faults + ranges.faults + patches.faults + trees.faults,
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
        answer, fault = ask(
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
        listed, reason = _range_of(context, commit, child_tip)
        if reason is not None:
            faults.append(reason)
            continue
        contents[key] = listed
        if subtract is None:
            continue
        listed, reason = _range_of(context, commit, child_tip, without=subtract)
        if reason is not None:
            faults.append(reason)
        else:
            without_parent[key] = listed
    return _RangeAnswers(
        contents=contents, without_parent=without_parent, faults=tuple(faults)
    )


def _range_of(
    context: CollectionContext,
    commit: str,
    child_tip: str,
    *,
    without: str | None = None,
) -> tuple[CommitRange, str | None]:
    """Return the commits one range holds, or why the listing was not made.

    Parameters
    ----------
    context : CollectionContext
        The run's inputs, whose graph lists the range.
    commit : str
        Boundary the range is listed above, exclusive.
    child_tip : str
        Child tip the range is listed down to.
    without : str | None, optional
        History to subtract from the listing, which gate 7 asks for and the
        other readers of a range do not.

    Returns
    -------
    tuple[CommitRange, str | None]
        The range as listed and no reason, or no commits and the reason the
        repository would not list them.

    """
    key = range_key(commit, child_tip)
    listed = functools.partial(context.graph.commits_in_range, commit, child_tip)
    question = f"cannot list the commits {key}"
    if without is not None:
        question = f"{question} without {without}"
        listed = functools.partial(listed, not_reachable_from=without)
    answer, fault = ask(question, listed)
    if fault is not None:
        return CommitRange(()), fault.reason
    return CommitRange(tuple(answer or ())), None


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
        answer, fault = ask(
            f"cannot identify the cumulative patch {range_key(commit, child_tip)}",
            functools.partial(
                context.graph.cumulative_patch_identifier, commit, child_tip
            ),
        )
        if fault is not None:
            faults.append(fault.reason)
        else:
            identifiers[commit] = answer
    patch, fault = ask(
        f"cannot identify the patch {landed} introduces",
        functools.partial(
            context.graph.cumulative_patch_identifier, f"{landed}^", landed
        ),
    )
    if fault is not None:
        faults.append(fault.reason)
    return _PatchAnswers(identifiers=identifiers, landed=patch, faults=tuple(faults))


def _tree_answers(
    context: CollectionContext,
    evidence: CollectedEvidence,
    contents: cabc.Mapping[str, CommitRange],
) -> _TreeAnswers:
    """Return the replay-range commits carrying the landed commit's tree.

    Gate 7 reads this beside the two listings it already has: a range holding
    a commit that applies the landed commit's tree carries content the target
    has already taken, and replaying it onto the target would apply that
    content a second time. The question is asked from the landed commit's side
    rather than from the parent head's, so it survives the parent being
    rewritten: an amend leaves the content where it was and changes which
    commit holds it. It is asked of the commits that apply a change, because a
    commit that changes nothing is dropped by a replay rather than applied,
    and one sits at the boundary's own tree without applying anything — a
    squash-merged parent leaves its content on the trunk, so the child's first
    commit above the boundary sits at that content whenever it carries none of
    its own.

    The comparison is only worth making when gate 7 can apply, and only over
    the ranges that were listed. Trees are read once per commit per run even
    though the ranges nest, because a listing is a set of commits and the same
    commit is above every candidate below it.

    Parameters
    ----------
    context : CollectionContext
        The run's inputs, whose ports put the questions.
    evidence : CollectedEvidence
        What the rungs produced, which decides whether any question exists.
    contents : cabc.Mapping[str, CommitRange]
        The replay ranges as listed, keyed by the pair they were listed for.

    Returns
    -------
    _TreeAnswers
        The matching commits per range key, and a reason for every tree that
        could not be read.

    """
    landed = _landed(context)
    if landed is None or evidence.parent_head is None:
        return _TreeAnswers(twins={})
    trees: dict[str, str] = {}
    tree, reason = _tree_of(context, landed, trees)
    if reason is not None:
        return _TreeAnswers(twins={}, faults=(reason,))
    twins: dict[str, tuple[str, ...]] = {}
    faults: list[str] = []
    for key, listed in contents.items():
        compared, reason = _twins(context, tree, listed.commits, trees)
        if reason is not None:
            faults.append(reason)
        else:
            twins[key] = compared
    return _TreeAnswers(twins=twins, faults=tuple(faults))


def _twins(
    context: CollectionContext,
    tree: str,
    commits: tuple[str, ...],
    trees: dict[str, str],
) -> tuple[tuple[str, ...], str | None]:
    """Return the commits that apply ``tree``, or why the comparison stopped.

    Sitting at a tree is not the same as applying it. A commit whose tree is
    its parent's as well changes nothing, so a replay drops it rather than
    applying it, and the content it happens to sit at is not applied a second
    time by it: a range that holds landed content only in such a commit does
    not hold work the target has already taken. The second question is put
    only about the commits that sit at the tree, so a range holding none of
    them costs no lookup beyond the trees it already reads.

    The first tree the repository will not read abandons the whole range: a
    comparison that stopped part way is not a comparison that found nothing,
    so the range is left without an answer rather than answered from the half
    of it that was read.

    Returns
    -------
    tuple[tuple[str, ...], str | None]
        The commits applying the tree, oldest first, and no reason — or no
        commits and the reason the comparison stopped.

    """
    found: list[str] = []
    for commit in commits:
        answer, reason = _tree_of(context, commit, trees)
        if reason is not None:
            return (), reason
        if answer != tree:
            continue
        applied, reason = _applies_a_change(context, commit, trees)
        if reason is not None:
            return (), reason
        if applied:
            found.append(commit)
    return tuple(found), None


def _applies_a_change(
    context: CollectionContext,
    commit: str,
    trees: dict[str, str],
) -> tuple[bool, str | None]:
    """Return whether ``commit`` changes anything against its first parent.

    The question is Git's own notion of what a commit applies: the difference
    between its tree and its first parent's, which is the patch a replay of it
    would apply. A commit whose parent is a root asks about a revision the
    repository will not resolve, and that is a fault rather than an answer:
    the comparison is left unmade rather than read as a commit that applies
    nothing, because a question that could not be put is not a clean answer.

    Returns
    -------
    tuple[bool, str | None]
        Whether the commit applies a change, and no reason — or no answer and
        the reason the repository would not give one.

    """
    mine, reason = _tree_of(context, commit, trees)
    if reason is not None:
        return False, reason
    theirs, reason = _tree_of(context, f"{commit}^", trees)
    if reason is not None:
        return False, reason
    return mine != theirs, None


def _tree_of(
    context: CollectionContext,
    commit: str,
    trees: dict[str, str],
) -> tuple[str, str | None]:
    """Return one commit's tree, reading it at most once per run.

    The revision names a commit, and may name one commit's first parent
    instead: that is how a comparison asks what a commit changes, and the
    answer is memoised against the revision it was asked for either way.

    A tree is forty hexadecimal characters, so an empty one is no tree at all,
    and the reason beside it always says why the repository named none. The
    alternative — an optional tree and a fault that the caller has to narrow
    against — leaves the caller able to compare a commit against a tree that
    was never read, which is the comparison this module exists not to make.

    Returns
    -------
    tuple[str, str | None]
        The commit's tree and no reason, or no tree and the reason the
        repository would not name one.

    """
    if commit in trees:
        return trees[commit], None
    question = f"cannot read the tree of {commit}"
    answer, fault = ask(question, functools.partial(context.graph.tree_of, commit))
    if answer is None:
        return "", fault.reason if fault is not None else question
    trees[commit] = answer
    return answer, None


def _child_history(context: CollectionContext) -> tuple[CommitRange, Fault | None]:
    """Return the child's whole history, oldest first, or why it was not listed."""
    answer, fault = ask(
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
        landed_twins={},
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
