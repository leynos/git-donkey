"""What the ``git wheresat`` gates' results mean for a boundary.

The gates are asked in :mod:`git_donkey.wheresat_gates`, which answers each named
question with one of three results. This module is what reads them into a
verdict, and it is pure: no Git, no filesystem, no network, and no process, so
an assessment is a function of values and a property test can hand it any
corpus of answers it likes.

Reading the results is where INV-2, INV-2b, and INV-4 are decided. A candidate
serves as the boundary when every applicable gate passed *and*
:func:`may_establish` permits its support — one deliberate statement, or two
independent derived sources. Of the commits that could serve, the ones at the
strongest rank present are the only candidates left: a commit a deliberate
statement names outranks one computed from surviving history, which is the
precedence ADR-005 ranks the evidence sources by and not a preference for the
first candidate read. One commit left is the answer; two at the same rank are an
ambiguity, which is a refusal rather than a coin toss; and none is a refusal
when every candidate was answered and a "could not tell" when one that could
have established was not, which is INV-5 read from the other end.

A record whose attested claim a later integration superseded is not thrown away.
Gate 8 refuses the claim, and the record is read as derived evidence: it may
still support the commit it names once another source agrees with it. That
demotion, and the reading of the gates an established boundary reports as the
conjunction that carried it, is the one place the assessment restates what the
gates said — the gates themselves stay a truth table over questions asked.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from git_donkey.wheresat_gates import (
    _not_applicable,
    _range,
    _short,
    gate_results,
)
from git_donkey.wheresat_records import (
    Assessment,
    AttestedCandidate,
    BoundaryRequest,
    Candidate,
    CommitRange,
    Established,
    Establishing,
    GateName,
    GateOutcome,
    GateResult,
    GraphFacts,
    Indeterminate,
    InferredCandidate,
    ParentPullRequest,
    Unresolved,
    demoted,
)

_DEMOTED: typ.Final = (
    "the record's attested claim was superseded, so its evidence is read as derived"
)
"""Why gate 8 no longer applies to a record a later integration demoted."""

_REQUIRED_SOURCES: typ.Final = 2
"""How many independent derived sources corroborate a boundary between them."""

_ATTESTED_RANK: typ.Final = 0
"""Rank of a commit carried by a deliberate statement naming it."""

_DERIVED_RANK: typ.Final = 1
"""Rank of a commit carried by evidence computed from surviving history."""


def may_establish(support: typ.Sequence[Candidate]) -> bool:
    """Return whether this support may establish a boundary.

    One attested candidate is enough: a stack record or a pull request head
    names the boundary by a deliberate act. Derived evidence is not, so two
    candidates naming one commit establish it only when they come from
    independent sources — recorded reflogs expire, and a fork point and a merge
    base over the same history can share one mistake. Inferred evidence never
    establishes, whatever corroborates it, and a boundary's support never holds
    it.

    Parameters
    ----------
    support : collections.abc.Sequence[Candidate]
        Candidates that passed every applicable gate and name one commit.

    Returns
    -------
    bool
        Whether the set may serve as the boundary's support.

    """
    establishing = [
        candidate
        for candidate in support
        if not isinstance(candidate, InferredCandidate)
    ]
    if not establishing:
        return False
    if any(isinstance(candidate, AttestedCandidate) for candidate in establishing):
        return True
    sources = {candidate.source for candidate in establishing}
    return len(sources) >= _REQUIRED_SOURCES


@dataclasses.dataclass(frozen=True, slots=True)
class _Checked:
    """One candidate with its gate results and what those results allow.

    ``support`` is the candidate as it may establish a boundary: ``None`` for
    inferred evidence, and the candidate demoted to derived evidence when gate 8
    refused a record's attested claim. It is therefore not ``original``, which
    is kept so a report can name the evidence the assessment was handed.
    """

    original: Candidate
    support: Establishing | None
    gates: tuple[GateResult, ...]
    refused: bool
    pending: bool


def _check(
    request: BoundaryRequest,
    candidate: Candidate,
    facts: GraphFacts,
    parent: ParentPullRequest | None,
) -> _Checked:
    """Evaluate one candidate and classify what its gates allow."""
    gates = gate_results(request, candidate, facts, parent)
    deciding = [
        gate
        for gate in gates
        if gate.applicable and gate.outcome is not GateOutcome.PASSED
    ]
    return _Checked(
        original=candidate,
        support=_establishing_from(candidate, superseded=_superseded(gates)),
        gates=gates,
        refused=_refused(deciding),
        pending=any(gate.outcome is GateOutcome.INDETERMINATE for gate in deciding),
    )


def _refused(deciding: typ.Sequence[GateResult]) -> bool:
    """Return whether a gate refused the candidate's claim altogether.

    Gate 8 is the one exception: a superseded record's claim is demoted rather
    than refused, so gate 8 answering against it is classified by
    :func:`_establishing_from` instead.

    Returns
    -------
    bool
        Whether an applicable gate other than gate 8 answered against the
        candidate.

    """
    return any(
        gate.outcome is GateOutcome.FAILED
        and gate.name is not GateName.RECORD_NOT_SUPERSEDED
        for gate in deciding
    )


def _superseded(gates: tuple[GateResult, ...]) -> bool:
    """Return whether gate 8 answered against the record's attested claim."""
    return any(
        gate.name is GateName.RECORD_NOT_SUPERSEDED
        and gate.outcome is GateOutcome.FAILED
        for gate in gates
    )


def _establishing_from(
    candidate: Candidate,
    *,
    superseded: bool,
) -> Establishing | None:
    """Return the candidate as it may establish a boundary, if it may at all."""
    match candidate:
        case InferredCandidate():
            return None
        case AttestedCandidate():
            return demoted(candidate) if superseded else candidate
        case _:
            return candidate


def _ordered(checked: _Checked) -> tuple[str, str, str, tuple[str, ...]]:
    """Return the sort key that makes every report independent of input order."""
    candidate = checked.original
    return (
        candidate.commit,
        candidate.kind.value,
        candidate.source,
        candidate.supporting,
    )


def _judged(
    request: BoundaryRequest,
    candidates: tuple[Candidate, ...],
    facts: GraphFacts,
    parent: ParentPullRequest | None,
) -> tuple[_Checked, ...]:
    """Return every candidate with its gates, in canonical order."""
    return tuple(
        sorted(
            (_check(request, one, facts, parent) for one in candidates),
            key=_ordered,
        )
    )


def _cleared(checked: _Checked) -> bool:
    """Return whether the candidate's gates allow it to serve as the boundary."""
    return checked.support is not None and not checked.refused and not checked.pending


def _supports(supporters: typ.Sequence[_Checked]) -> tuple[Establishing, ...]:
    """Return the support each cleared candidate of a commit carries."""
    return tuple(one.support for one in supporters if one.support is not None)


def _serving(
    checked: typ.Sequence[_Checked],
) -> typ.Mapping[str, tuple[_Checked, ...]]:
    """Return the commits that could serve, by the support each of them has."""
    cleared = [one for one in checked if _cleared(one)]
    serving = {}
    for commit in sorted({one.original.commit for one in cleared}):
        supporters = tuple(one for one in cleared if one.original.commit == commit)
        if may_establish(_supports(supporters)):
            serving[commit] = supporters
    return serving


def _rank(supporters: typ.Sequence[_Checked]) -> int:
    """Return how strongly the evidence names the commit its supporters cover.

    A commit one deliberate statement names outranks a commit computed from
    surviving history, which is the precedence ADR-005 orders the evidence
    sources by: the record was written at branch birth, while a merge base is
    computed from history that may since have been rewritten. A record whose
    attested claim gate 8 demoted carries its commit as derived evidence, and so
    is ranked with the computations rather than above them.

    The rank is read from what the gates left, never from where the candidate
    appeared among the others, so no verdict can depend on the order the
    evidence arrived in (INV-3).

    Returns
    -------
    int
        ``_ATTESTED_RANK`` when a declaration carries the commit, and
        ``_DERIVED_RANK`` when only computations do.

    """
    if any(isinstance(one.support, AttestedCandidate) for one in supporters):
        return _ATTESTED_RANK
    return _DERIVED_RANK


def _preferred(serving: typ.Mapping[str, tuple[_Checked, ...]]) -> tuple[str, ...]:
    """Return the commits that could serve at the strongest rank present.

    A lower-ranked commit is not a rival to be chosen between: the evidence that
    carries it is exactly the kind the higher-ranked candidate is preferred to,
    so it stops being an answer as soon as one at a stronger rank exists. Two
    commits at the same rank remain rivals, which is what the ambiguity refusal
    is for.

    Returns
    -------
    tuple[str, ...]
        The commits still in the running, in canonical commit order.

    """
    if not serving:
        return ()
    ranks = {commit: _rank(supporters) for commit, supporters in serving.items()}
    strongest = min(ranks.values())
    return tuple(commit for commit, rank in ranks.items() if rank == strongest)


def _carrier(supporters: typ.Sequence[_Checked]) -> tuple[GateResult, ...]:
    """Return the gates an established boundary reports as its acceptance.

    A record whose claim gate 8 demoted still supports its commit once another
    source corroborates it, but gate 8 answered against that claim. The gates an
    established result reports must be the ones that carried it, so they are
    read from the first supporter whose every applicable gate passed: the
    demoted supporter's refusal remains visible in the refusals a run reports
    when nothing establishes, and cannot turn the established result into a
    false account of the conjunction that produced it.

    When every supporter of the commit was demoted, the corroboration is between
    the demoted evidence itself — INV-2b's rule for derived evidence, which is
    what the demotion leaves — and the first in canonical order carries the
    report. There is no supporter whose every applicable gate passed to read
    from, and :func:`_demoted_gates` says what the demotion reads as instead.

    Returns
    -------
    tuple[GateResult, ...]
        The gates an established boundary reports as the conjunction that
        carried it.

    """
    carrier = next(
        (one for one in supporters if not _has_refusal(one)),
        supporters[0],
    )
    return _demoted_gates(carrier.gates)


def _demoted_gates(gates: tuple[GateResult, ...]) -> tuple[GateResult, ...]:
    """Return gates in which a refusal by demotion reads as not applying.

    Gate 8 is the gate of an *attested* claim, and a record it refused is read
    as derived evidence: the gate did not vanish, it stopped being about the
    evidence that stands. Reporting it as failed would print a refusal beside a
    boundary that holds, and reporting it as passed would say the record's claim
    was accepted; so it is reported as not applying to the evidence the demotion
    left. Nothing is hidden by that: the boundary's ``support`` names the
    demoted candidate, whose class is what says its evidence was read as
    derived, and a run that establishes nothing reads these same gates before
    they are read this way.

    Returns
    -------
    tuple[GateResult, ...]
        The gates, with gate 8's refusal read as not applying to the evidence
        the demotion left.

    """
    return tuple(
        _not_applicable(gate.name, _DEMOTED)
        if gate.name is GateName.RECORD_NOT_SUPERSEDED
        and gate.outcome is GateOutcome.FAILED
        else gate
        for gate in gates
    )


def _has_refusal(checked: _Checked) -> bool:
    """Return whether any applicable gate answered against the candidate."""
    return any(
        gate.applicable and gate.outcome is GateOutcome.FAILED for gate in checked.gates
    )


def _included(facts: GraphFacts, commit: str, child_tip: str) -> CommitRange:
    """Return the child's own commits above the boundary, as the run listed them.

    Gate 5 passed for the boundary being reported, so the run did list this
    range and listed it as non-empty; a range that was never listed is reported
    as covering nothing rather than as a partition of the child's history.

    Returns
    -------
    CommitRange
        The listed range, or an empty range when the run never listed it.

    """
    listed = _range(facts, commit, child_tip)
    return listed if listed is not None else CommitRange(())


def _established(
    request: BoundaryRequest,
    facts: GraphFacts,
    supporters: typ.Sequence[_Checked],
    commit: str,
) -> Established:
    """Return the result for a boundary its supporters carry."""
    included = _included(facts, commit, request.child_tip)
    replayed = set(included.commits)
    return Established(
        old_base=commit,
        support=_supports(supporters),
        included=included.commits,
        excluded=tuple(
            one for one in facts.child_history.commits if one not in replayed
        ),
        included_truncated=included.truncated,
        excluded_truncated=facts.child_history.truncated,
        gates=_carrier(supporters),
        durable_ref=None,
    )


def _gate_summary(gates: typ.Iterable[GateResult]) -> str:
    """Return the gates that did not pass, with what each of them saw."""
    deciding = [
        gate
        for gate in gates
        if gate.applicable and gate.outcome is not GateOutcome.PASSED
    ]
    return "; ".join(
        f"{gate.name.value} {gate.outcome.value}: {gate.detail}" for gate in deciding
    )


def _candidate_reason(checked: _Checked) -> str:
    """Return why one candidate could not serve as the boundary."""
    candidate = checked.original
    commit = _short(candidate.commit)
    support = checked.support
    if support is None:
        return (
            f"{commit} is {candidate.kind.value} evidence from {candidate.source}, "
            "which cannot establish a boundary"
        )
    if checked.refused:
        return f"{commit} is refused by {_gate_summary(checked.gates)}"
    if checked.pending:
        return f"{commit} awaits an answer: {_gate_summary(checked.gates)}"
    if support is not candidate:
        return (
            f"{commit} was demoted by {_gate_summary(checked.gates)}, leaving "
            "derived evidence that needs corroboration"
        )
    return (
        f"{commit} rests on one derived candidate from {support.source}, which "
        "needs corroboration"
    )


def _originals(checked: typ.Sequence[_Checked]) -> tuple[Candidate, ...]:
    """Return the candidates as the sources handed them to the assessment."""
    return tuple(one.original for one in checked)


def _gates(checked: typ.Sequence[_Checked]) -> tuple[GateResult, ...]:
    """Return every candidate's gates, in the canonical candidate order."""
    return tuple(gate for one in checked for gate in one.gates)


def _ambiguous(
    reported: tuple[Candidate, ...],
    gates: tuple[GateResult, ...],
    commits: tuple[str, ...],
) -> Unresolved:
    """Return the refusal for a corpus that leaves more than one boundary.

    The commits are the ones still in the running after precedence was applied,
    so the refusal names rivals the evidence really does not choose between
    rather than every commit that cleared its gates.

    Returns
    -------
    Unresolved
        The refusal, naming the rivals the evidence leaves in the running.

    """
    names = ", ".join(_short(one) for one in commits)
    reason = (
        f"{len(commits)} commits could serve as the boundary ({names}); the "
        "evidence does not choose between them"
    )
    return Unresolved(candidates=reported, gates=gates, reasons=(reason,))


def _refusal(
    reported: tuple[Candidate, ...],
    gates: tuple[GateResult, ...],
    checked: typ.Sequence[_Checked],
) -> Unresolved | Indeterminate:
    """Return why no boundary was established, and how sure the run can be.

    An inferred candidate that went unanswered does not make the run
    indeterminate: its support could never have established the boundary, so the
    question it could not answer is one the verdict never rested on.

    Returns
    -------
    Unresolved | Indeterminate
        Why no boundary was established, as a refusal, or as an indeterminate
        result when a candidate that could have established went unanswered.

    """
    reasons = tuple(_candidate_reason(one) for one in checked)
    stated = reasons or ("no boundary candidate was found",)
    if any(one.pending for one in checked if one.support is not None):
        return Indeterminate(candidates=reported, gates=gates, reasons=stated)
    return Unresolved(candidates=reported, gates=gates, reasons=stated)


def assess(
    request: BoundaryRequest,
    candidates: tuple[Candidate, ...],
    facts: GraphFacts,
    parent: ParentPullRequest | None,
) -> Assessment:
    """Return the boundary the evidence establishes, or why none was.

    The verdict is read from the candidates that may serve, grouped by the
    commit each names: a candidate serves when every applicable gate passed and
    :func:`may_establish` permits its support. Of the commits that serve, only
    those at the strongest rank present are left in the running, so a commit a
    deliberate statement names answers over one that history was computed for.
    One commit left is the answer; two at the same rank are an ambiguity, which
    is a refusal rather than a coin toss; none is a refusal when every candidate
    was answered, and an indeterminate result when one of them was not.

    An empty ``candidates`` is the answer "nothing here names a boundary". It is
    not how a fault is reported: a source that could not answer reaches the
    caller as a fault, which the caller applies through
    :func:`apply_collection_faults`, because a refusal for a question the
    repository never answered is a wrong answer while "could not tell" is not.

    Parameters
    ----------
    request : BoundaryRequest
        What the run set out to answer.
    candidates : tuple[Candidate, ...]
        Every candidate the evidence sources produced, in any order.
    facts : GraphFacts
        The graph answers the gates read.
    parent : ParentPullRequest | None
        The parent pull request, when one was resolved.

    Returns
    -------
    Assessment
        The established boundary, a refusal, or an indeterminate result, with
        the gates and the reasons the report renders.

    """
    checked = _judged(request, candidates, facts, parent)
    serving = _serving(checked)
    commits = _preferred(serving)
    match commits:
        case (commit,):
            return _established(request, facts, serving[commit], commit)
        case (_, *_):
            return _ambiguous(_originals(checked), _gates(checked), commits)
        case _:
            return _refusal(_originals(checked), _gates(checked), checked)


def apply_collection_faults(
    assessment: Assessment, faults: typ.Sequence[str]
) -> Assessment:
    """Return the assessment a collection fault forces, if it forces one.

    A fault means the evidence set is not known to be complete, so a refusal
    cannot be read as "nothing in this repository names a boundary" and the run
    answers that it could not tell instead. An established boundary survives: its
    candidate passed every gate it needed, and a fault in a source that neither
    checked nor unchecked it cannot make the answer wrong.

    Parameters
    ----------
    assessment : Assessment
        What the assessment made of the evidence that was collected.
    faults : collections.abc.Sequence[str]
        What each source that could not answer reported.

    Returns
    -------
    Assessment
        The assessment, or the indeterminate result the faults force.

    """
    if not faults or isinstance(assessment, Established):
        return assessment
    return Indeterminate(
        candidates=assessment.candidates,
        gates=assessment.gates,
        reasons=tuple(faults) + assessment.reasons,
    )
