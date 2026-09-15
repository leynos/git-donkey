"""The rungs ``git wheresat`` asks for a boundary, in the procedure's order.

Each rung of the evidence ladder is a question that names a commit: the stack
record ``git donkey`` wrote, a merge base, a surviving fork point. A rung
returns the candidates it found; a rung that could not answer at all returns a
*fault*, because "there is no evidence here" and "this question went
unanswered" are different answers and only one of them is a refusal (INV-5).

What one rung cannot answer another can. A branch with no record still has a
merge base; a parent branch that was deleted still has the tombstone ``git
plonk`` left behind, and that tombstone is where ``PARENT_HEAD`` comes from. It
proposes no boundary of its own: it feeds the rungs that need a parent head,
which is exactly the role the recovery procedure gives it.

The candidate set is bounded by :data:`MAX_CANDIDATES`, because a set that hit
the bound is not known to be complete: exceeding it is reported as a fault and
the run answers that it could not tell, rather than listing the first
candidates as though they were all of them.

The second phase — every graph question the eight gates will read, asked once
each and only those — lives in :mod:`git_donkey.wheresat_facts`, which is handed
what this module collected. Nothing here decides anything, and nothing here
writes: the graph and the record reader are read-only ports, and the assessment
that weighs what they return lives in :mod:`git_donkey.wheresat_policy`.
"""

from __future__ import annotations

import dataclasses
import functools
import typing as typ

from git_donkey import observability, stack_records, stack_store
from git_donkey.wheresat_errors import ShallowHistoryError, WheresatGraphError
from git_donkey.wheresat_records import (
    TIERS,
    BoundaryRequest,
    Candidate,
    EvidenceKind,
    ParentPullRequest,
    candidate_for,
    record_evidence_kind,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from git_donkey.wheresat_graph import WheresatGraph


MAX_CANDIDATES: typ.Final = 32
"""How many boundary candidates a run will report.

A set that reached this bound is not known to be complete, so the bound is
reported as a fault rather than applied silently: a report that lists the first
thirty-two candidates reads as though thirty-two were all there were.
"""

_COLLECTION: typ.Final[observability.Operation] = "evidence_collection"
"""The operation every rung's observation is recorded under."""

_TARGET_MERGE_BASE: typ.Final = "merge base of the target and the child"
_PARENT_MERGE_BASE: typ.Final = "merge base of the parent head and the child"


@dataclasses.dataclass(frozen=True, slots=True)
class _Fault:
    """A question the repository could not answer, and its class of failure.

    The reason is prose for the report; the class is a bounded label for the
    recorder. They are kept together so a rung that fails returns one value
    rather than two parallel lists that can drift apart.
    """

    reason: str
    error_kind: observability.ErrorKind


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


@dataclasses.dataclass(frozen=True, slots=True)
class CollectionContext:
    """Everything a rung may read, and the parts the pipeline fills.

    Parameters
    ----------
    request : BoundaryRequest
        What the run set out to answer.
    target_ref : str | None
        Full ref path the target was resolved from, when it was resolved from
        one, because a fork point is read out of a ref's reflog.
    parent : ParentPullRequest | None
        The parent pull request the run identified, as the forge reported it
        and with the head the run fetched set on it.
    graph : WheresatGraph
        Read-only history questions.
    records : stack_store.StackRecordReader
        Read-only access to the child's record and to the tombstones.
    record : stack_records.RecordResult, optional
        The child's record, read once before the first rung runs and replaced
        by the pipeline. It is a value rather than a reader so that a rung
        cannot read a different record than the one the pipeline provisioned
        the parent head from.
    parent_head : ParentHead | None, optional
        The parent's tip: the head the run fetched, or the one the tombstone
        names when it fetched none.

    Notes
    -----
    ``record`` is filled by :func:`collect_evidence`; a caller leaves it at its
    default. The other two are the run's own answers and are handed in, because
    the parent is identified and its head fetched before a rung runs: a rung
    that asked the forge would be a rung this module would have to give a
    network to. They live here rather than in a second object because a rung's
    whole input is the context, and a rung that had to be handed two records
    would be a rung whose signature changes every time the ladder grows a
    question.

    """

    request: BoundaryRequest
    target_ref: str | None
    parent: ParentPullRequest | None
    graph: WheresatGraph
    records: stack_store.StackRecordReader
    record: stack_records.RecordResult = dataclasses.field(
        default_factory=stack_records.RecordAbsent
    )
    parent_head: ParentHead | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class CollectionResult:
    """What one rung produced, or why it produced nothing.

    Parameters
    ----------
    candidates : tuple[Candidate, ...], optional
        The candidates this rung found, in the order it found them.
    indeterminate_reasons : tuple[str, ...], optional
        Why the rung could not answer. A rung that answered with no candidates
        reports nothing here: finding no evidence is an answer, and a fault is
        not.
    error_kind : observability.ErrorKind | None, optional
        The bounded class of the failure, when there was one.

    """

    candidates: tuple[Candidate, ...] = ()
    indeterminate_reasons: tuple[str, ...] = ()
    error_kind: observability.ErrorKind | None = None


type EvidenceSource = cabc.Callable[[CollectionContext], CollectionResult]
"""One rung of the evidence ladder: a question, and the candidates it found."""


@dataclasses.dataclass(frozen=True, slots=True)
class CollectedEvidence:
    """Every candidate the rungs produced, and the questions they could not put.

    ``parent_head`` and ``recorded_from`` are carried beside the candidates
    because the gates read them the same way: they are what the run managed to
    learn about the parent, and they are not any one rung's answer.
    """

    candidates: tuple[Candidate, ...]
    parent_head: ParentHead | None
    recorded_from: str | None
    faults: tuple[str, ...] = ()


def _record_evidence(context: CollectionContext) -> CollectionResult:
    """Return the boundary ``git donkey`` wrote for the child, or why none.

    This is the only rung whose candidate can establish a boundary on its own,
    because it is the only one that names the boundary by a deliberate act. A
    record that cannot be read is a fault and not an absence: a half-record, or
    one whose evidence value this version does not classify, is a version
    mismatch rather than a weak claim.

    Returns
    -------
    CollectionResult
        The candidate the record names, nothing at all when there is no record,
        or the reason a record that is there cannot be used.

    """
    match context.record:
        case stack_records.StackRecord(base=base, evidence=evidence):
            return _record_candidate(context, base, evidence)
        case stack_records.RecordMalformed(reason=reason):
            reason = (
                f"the stack record for {context.request.branch} is unusable: {reason}"
            )
            return CollectionResult(
                indeterminate_reasons=(reason,), error_kind="stack_record_malformed"
            )
    return CollectionResult()


def _merge_base_evidence(context: CollectionContext) -> CollectionResult:
    """Return the best common ancestors of the child, target, and parent head.

    Two questions are asked, because either can answer: the merge base of the
    target and the child, and the merge base of the parent's head and the
    child. The second is the one a rebase leaves intact, because it is asked
    about the commit the child really inherited rather than about where the
    trunk has since moved to. Neither answer establishes a boundary alone: both
    are derived candidates, and the policy asks two independent sources to name
    one commit before either of them may.

    Returns
    -------
    CollectionResult
        One candidate per merge base found, named by the question that found
        it, or the reason a question could not be put.

    """
    child_tip = context.request.child_tip
    lefts = [(context.request.target, _TARGET_MERGE_BASE)]
    if context.parent_head is not None:
        lefts.append((context.parent_head.commit, _PARENT_MERGE_BASE))
    candidates: list[Candidate] = []
    faults: list[_Fault] = []
    for left, source in lefts:
        answer, fault = _asked(
            f"cannot find the merge base of {left} and {child_tip}",
            functools.partial(context.graph.merge_bases, left, child_tip),
        )
        if fault is not None:
            faults.append(fault)
            continue
        candidates.extend(
            candidate_for(one, EvidenceKind.MERGE_BASE, source=source)
            for one in answer or ()
        )
    return _result(candidates, faults)


def _fork_point_evidence(context: CollectionContext) -> CollectionResult:
    """Return the commits the surviving reflogs say the child forked from.

    A fork point is read out of a reflog, so every question this rung asks is
    asked about a *ref*: the target when it was resolved from one, and the
    tombstone ref when the parent's head came from one. A ref whose reflog has
    expired yields no fork point, which is an answer and not a fault — the
    reflog's absence is precisely what makes this rung derived evidence
    requiring corroboration.

    Returns
    -------
    CollectionResult
        One candidate per reflog that named a fork point, each named by the ref
        it was read from, or the reason a question could not be put.

    """
    child_tip = context.request.child_tip
    candidates: list[Candidate] = []
    faults: list[_Fault] = []
    for ref in _fork_point_refs(context):
        answer, fault = _asked(
            f"cannot find the fork point of {child_tip} and {ref}",
            functools.partial(context.graph.fork_point, ref, child_tip),
        )
        if fault is not None:
            faults.append(fault)
            continue
        if answer:
            candidates.append(
                candidate_for(
                    answer, EvidenceKind.FORK_POINT, source=f"fork point of {ref}"
                )
            )
    return _result(candidates, faults)


SOURCES: typ.Final[tuple[tuple[EvidenceKind, EvidenceSource], ...]] = (
    (EvidenceKind.STACK_RECORD_BIRTH, _record_evidence),
    (EvidenceKind.MERGE_BASE, _merge_base_evidence),
    (EvidenceKind.FORK_POINT, _fork_point_evidence),
)
"""The rungs this version reads, in the precedence order the procedure fixes.

The kind beside each rung is what its observation is labelled with, so the
evidence tier a rung's answer is recorded under comes from one declaration
rather than from each rung's memory of what it reads.

The ladder's remaining rungs arrive with the evidence they read rather than
early and silent: the shared record and the pull request head need a forge to
read, and tree identity and cumulative patch identity compare the child against
the parent's integration commit, which is a forge fact as well. A rung that
cannot run is absent from this tuple, so the pipeline holds no branch that runs
a question it cannot answer.
"""


def collect_evidence(
    context: CollectionContext,
    sources: cabc.Sequence[tuple[EvidenceKind, EvidenceSource]] = SOURCES,
) -> CollectedEvidence:
    """Return every candidate the rungs produced, and what could not be asked.

    Parameters
    ----------
    context : CollectionContext
        What the run set out to answer, with the ports to ask it through.
    sources : collections.abc.Sequence[tuple[EvidenceKind, EvidenceSource]], optional
        Rungs to run with the kind each is labelled by, in order. Overridden by
        tests that need a rung the repository cannot supply, such as one that
        produces more candidates than the bound allows.

    Returns
    -------
    CollectedEvidence
        The candidates, the parent head and recorded-from the pipeline
        recovered, and a reason for every question that went unanswered.

    """
    prepared, faults = _prepared(context)
    candidates, rung_faults = _rung_results(prepared, sources)
    kept, cap_faults = _capped(candidates)
    return CollectedEvidence(
        candidates=kept,
        parent_head=prepared.parent_head,
        recorded_from=_recorded_from(prepared.record),
        faults=faults + rung_faults + cap_faults,
    )


def _prepared(
    context: CollectionContext,
) -> tuple[CollectionContext, tuple[str, ...]]:
    """Return the context with the record and the parent head already read.

    The record is read first because the parent head is sought through it: a
    child whose record names no parent branch has no tombstone to look for, so
    reading the record first is what keeps the second read from being a guess.

    Returns
    -------
    tuple[CollectionContext, tuple[str, ...]]
        The context every rung is handed, and the reason the parent head could
        not be recovered when it could not be.

    """
    record = _read_record(context)
    head, fault = _parent_head(dataclasses.replace(context, record=record))
    prepared = dataclasses.replace(context, record=record, parent_head=head)
    return prepared, (fault,) if fault is not None else ()


def _capped(
    candidates: tuple[Candidate, ...],
) -> tuple[tuple[Candidate, ...], tuple[str, ...]]:
    """Return the candidates within the bound, and why they are not all of them.

    Returns
    -------
    tuple[tuple[Candidate, ...], tuple[str, ...]]
        The candidates to report, and the reason the set is not known to be
        complete when the bound was reached.

    """
    if len(candidates) <= MAX_CANDIDATES:
        return candidates, ()
    reason = (
        f"more than {MAX_CANDIDATES} boundary candidates were collected, so the "
        f"evidence set is not known to be complete"
    )
    return candidates[:MAX_CANDIDATES], (reason,)


def _rung_results(
    context: CollectionContext,
    sources: cabc.Sequence[tuple[EvidenceKind, EvidenceSource]],
) -> tuple[tuple[Candidate, ...], tuple[str, ...]]:
    """Return every rung's candidates and every fault, recording as it goes.

    Parameters
    ----------
    context : CollectionContext
        The prepared context, with the record and parent head already read.
    sources : collections.abc.Sequence[tuple[EvidenceKind, EvidenceSource]]
        Rungs to run with the kind each is labelled by, in order.

    Returns
    -------
    tuple[tuple[Candidate, ...], tuple[str, ...]]
        The candidates every rung produced, and the reasons each rung could not
        answer. One bounded observation is recorded per rung either way.

    """
    candidates: list[Candidate] = []
    faults: list[str] = []
    for kind, source in sources:
        result = source(context)
        candidates.extend(result.candidates)
        faults.extend(result.indeterminate_reasons)
        _observe(kind, _outcome_of(result), error_kind=result.error_kind)
    return tuple(candidates), tuple(faults)


def _fork_point_refs(context: CollectionContext) -> tuple[str, ...]:
    """Return the refs whose reflogs hold a fork point of the child."""
    refs = [context.target_ref] if context.target_ref else []
    if context.parent_head is not None and context.parent_head.ref:
        refs.append(context.parent_head.ref)
    return tuple(refs)


def _recorded_from(record: stack_records.RecordResult) -> str | None:
    """Return the child tip a stack record was written against, if there is one."""
    if isinstance(record, stack_records.StackRecord):
        return record.recorded_from
    return None


def _record_candidate(
    context: CollectionContext, base: str, evidence: str
) -> CollectionResult:
    """Return the candidate a record's boundary and evidence kind describe.

    A record whose evidence value this version does not classify is a fault and
    not an absence: the record was written deliberately, so a value that cannot
    be read is a version mismatch rather than a weak claim.

    Returns
    -------
    CollectionResult
        The single candidate the record names, or the reason it names none.

    """
    kind = record_evidence_kind(evidence)
    if kind is None:
        reason = (
            f"the stack record for {context.request.branch} records "
            f"{evidence!r} as its evidence, and this version does not classify "
            f"that value"
        )
        return CollectionResult(
            indeterminate_reasons=(reason,), error_kind="stack_record_malformed"
        )
    source = stack_records.base_ref_path(context.request.branch)
    return CollectionResult((candidate_for(base, kind, source=source),))


def _read_record(context: CollectionContext) -> stack_records.RecordResult:
    """Return the child's record, as a value even when reading it failed.

    A read failure becomes a malformed record rather than a raised error so
    that the stack-record rung is the one place that turns it into a fault: the
    reason is then reported once, by the rung that owns it.

    Returns
    -------
    stack_records.RecordResult
        The record, or a malformed one naming the read failure.

    """
    try:
        return context.records.read(context.request.branch)
    except stack_store.StackRecordError as exc:
        return stack_records.RecordMalformed(f"the record could not be read: {exc}")


def _parent_head(
    context: CollectionContext,
) -> tuple[ParentHead | None, str | None]:
    """Return the parent's tip, sought in the order the procedure fixes.

    The order is the fetched pull request head, then the tombstone ``git plonk``
    wrote for the parent branch. A head the run already has is the procedure's
    first answer and is taken as given: it was fetched from the pull request
    the run identified, so seeking a tombstone below it would be asking a
    weaker question after a stronger one was answered. A run that reads no
    forge reaches the tombstone instead, which is the one that survives the
    parent branch being deleted — the degradation this command exists for. A
    tombstone that cannot be read is a fault and not an absence: "no tombstone"
    and "the tombstone would not open" are different answers.

    Returns
    -------
    tuple[ParentHead | None, str | None]
        The parent's head and the ref it was read from, or no head and the
        reason it could not be recovered. No head and no reason is the honest
        answer for a run whose child names no parent branch and whose parent
        pull request named no head.

    """
    if context.parent_head is not None:
        return context.parent_head, None
    branch = _parent_branch(context.record)
    if branch is None:
        return None, None
    try:
        commit = context.records.tombstone(branch)
    except (stack_store.StackRecordError, WheresatGraphError, ValueError) as exc:
        return None, f"the tombstone for {branch} could not be read: {exc}"
    if commit is None:
        return None, None
    return ParentHead(commit, stack_records.tombstone_ref_path(branch)), None


def _parent_branch(record: stack_records.RecordResult) -> str | None:
    """Return the parent branch the child's record names, when it names one.

    A record written after the parent was opened names a pull request instead,
    and a pull request is not a ref: nothing local says where that parent's tip
    was, so such a run has no parent head to offer the gates.

    Returns
    -------
    str | None
        The parent branch name, or ``None`` when the record names none.

    """
    match record:
        case stack_records.StackRecord(parent=stack_records.StackParent(branch=branch)):
            return branch
    return None


def _asked[Answer](
    question: str, call: cabc.Callable[[], Answer]
) -> tuple[Answer | None, _Fault | None]:
    """Return one graph answer, or the fault that says why there is none.

    Parameters
    ----------
    question : str
        The question in prose, so that a fault reads as one.
    call : collections.abc.Callable[[], Answer]
        The read-only question to put.

    Returns
    -------
    tuple[Answer | None, _Fault | None]
        The answer and no fault, or no answer and the fault, never both.

    """
    try:
        return call(), None
    except ShallowHistoryError as exc:
        return None, _Fault(f"{question}: {exc}", "shallow_history")
    except (WheresatGraphError, stack_store.StackRecordError) as exc:
        return None, _Fault(f"{question}: {exc}", "git_command_error")


def _result(
    candidates: typ.Sequence[Candidate], faults: typ.Sequence[_Fault]
) -> CollectionResult:
    """Return one rung's result, labelled with the class of failure it saw."""
    return CollectionResult(
        candidates=tuple(candidates),
        indeterminate_reasons=tuple(fault.reason for fault in faults),
        error_kind=faults[0].error_kind if faults else None,
    )


def _outcome_of(result: CollectionResult) -> observability.Outcome:
    """Return the bounded outcome one rung's result is recorded as."""
    if result.indeterminate_reasons:
        return "failure"
    return "success" if result.candidates else "empty"


def _tier_label(kind: EvidenceKind) -> observability.EvidenceTierLabel:
    """Return the tier of ``kind`` as the bounded record label it is read as."""
    return TIERS[kind].value


def _observe(
    kind: EvidenceKind,
    outcome: observability.Outcome,
    *,
    error_kind: observability.ErrorKind | None = None,
) -> None:
    """Record one bounded observation for the rung that reads ``kind``."""
    observability.get_recorder().record(
        observability.Observation(
            operation=_COLLECTION,
            outcome=outcome,
            error_kind=error_kind,
            evidence_tier=_tier_label(kind),
        )
    )
