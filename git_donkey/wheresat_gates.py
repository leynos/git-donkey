"""The eight gates ``git wheresat`` weighs a candidate boundary against.

A gate is one named question with one of three answers: it passed, it went
against the candidate, or the repository could not answer it. Eight of them are
evaluated for every candidate, which is what makes INV-4 a truth table rather
than a set of scenarios: a boundary holds only while every applicable gate
passed, and the assessment reads that conjunction off these results.

INV-5 is decided here, once, by :func:`ancestry_outcome`: an ancestry question
Git could not answer is ``Ancestry.UNKNOWN``, which yields ``INDETERMINATE`` and
never ``FAILED``, so no refusal can rest on a question that was never put.

Every answer a gate needs arrives in the
:class:`~git_donkey.wheresat_records.GraphFacts` it is handed, which is what
makes the table checkable; no gate reaches back into a repository, a filesystem,
or a network.

Applicability is decided from the run's inputs, never from what collection
happened to bring back. A run that named a parent pull request applies the gates
about one even when the parent could not be resolved, so a fault cannot shrink
the conjunction and a run cannot establish a boundary on a question it never
asked. The gates about a parent's history are the one place a recovered answer
widens the conjunction: a parent head is known whenever the parent's tombstone or
remote-tracking ref survives, and a run holding one is judged against it, so a
boundary that no longer lies on the parent's history is refused rather than
established out of the record alone. Gate 7 joins them only when the head and
the integration are both known, because its two clauses cannot be separated:
what the range still holds beside the parent is read from the same listing that
says what it holds.

Nothing here reaches a verdict: :func:`gate_results` returns the gates for one
candidate, and :mod:`git_donkey.wheresat_policy` weighs them into an assessment.
That assessment also reads three helpers from here — the not-applicable result,
the listed range, and the abbreviated commit — because the demotion rule and the
partition it builds are stated in the same terms the gates use.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from git_donkey.wheresat_records import (
    COMMIT_ABBREVIATION,
    GATE_NAMES,
    Ancestry,
    BoundaryRequest,
    Candidate,
    CommitRange,
    GateName,
    GateOutcome,
    GateResult,
    GraphFacts,
    ParentPullRequest,
    is_record_kind,
    range_key,
)

if typ.TYPE_CHECKING:
    from git_donkey import stack_records

_NO_PARENT: typ.Final = "the run did not set out to consult a parent pull request"
"""Why the gates about a parent pull request do not apply to a run."""

_NO_PARENT_HEAD: typ.Final = "no parent head was recovered or sought"
"""Why the parent-history gate does not apply when nothing named a head."""

_NO_LANDED_WORK: typ.Final = "no parent integration is in play"
"""Why the landed-work gate does not apply without a head and an integration."""

_UNRESOLVED_PARENT: typ.Final = "the parent pull request could not be resolved"
"""What a gate about the parent reports when there is no parent to report on."""

_NO_HEAD_ORIGIN: typ.Final = "no parent head ref was fetched, so its origin is unknown"
"""What gate 1 reports when the run never fetched the head it read."""

_SEVERITY: typ.Final[typ.Mapping[GateOutcome, int]] = {
    GateOutcome.PASSED: 0,
    GateOutcome.INDETERMINATE: 1,
    GateOutcome.FAILED: 2,
}
"""Which of two answered halves decides their gate: the worse answer wins."""

_POLARITY_WORDS: typ.Final[typ.Mapping[Ancestry, str]] = {
    Ancestry.ANCESTOR: "is",
    Ancestry.NOT_ANCESTOR: "is not",
}
"""How the two decidable ancestry answers read in a gate's detail."""

type _Evaluator = typ.Callable[[_GateInputs], GateResult]


def ancestry_outcome(observed: Ancestry, *, expect: Ancestry) -> GateOutcome:
    """Map an ancestry answer to a gate outcome for the expected polarity.

    This is the one place where "an error is not a negative answer" is decided:
    an ancestry question Git could not answer yields ``INDETERMINATE``, never
    ``FAILED``, so no refutation can rest on a question that was never put.

    Parameters
    ----------
    observed : Ancestry
        Answer the run recorded for one ordered ancestry question.
    expect : Ancestry
        Answer that satisfies the gate.

    Returns
    -------
    GateOutcome
        ``PASSED`` when the answer is the expected one, ``FAILED`` when it is
        the opposite one, and ``INDETERMINATE`` when there is no answer.

    Examples
    --------
    >>> ancestry_outcome(Ancestry.UNKNOWN, expect=Ancestry.ANCESTOR).value
    'indeterminate'

    """
    if observed is Ancestry.UNKNOWN:
        return GateOutcome.INDETERMINATE
    return GateOutcome.PASSED if observed is expect else GateOutcome.FAILED


@dataclasses.dataclass(frozen=True, slots=True)
class _Clause:
    """One half of a two-clause gate: what it decided and what it saw."""

    outcome: GateOutcome
    detail: str


def _deciding(first: _Clause, second: _Clause) -> _Clause:
    """Return the clause that decides their gate, which is the worse answer."""
    return max((first, second), key=_severity)


def _severity(clause: _Clause) -> int:
    """Return how much weight one clause's outcome carries."""
    return _SEVERITY[clause.outcome]


@dataclasses.dataclass(frozen=True, slots=True)
class _GateInputs:
    """One candidate, with every answer the gates read and their scope.

    ``parent_gates`` says whether the gates about a parent pull request apply.
    It is resolved once per candidate from the run's inputs, so no gate decides
    applicability for itself and quietly narrows the set of questions asked.
    """

    request: BoundaryRequest
    candidate: Candidate
    facts: GraphFacts
    parent: ParentPullRequest | None
    parent_gates: bool


def _ancestry(facts: GraphFacts, left: str, right: str) -> Ancestry:
    """Return the recorded answer to one ordered ancestry question."""
    return facts.ancestry.get((left, right), Ancestry.UNKNOWN)


def _range(facts: GraphFacts, base: str, tip: str) -> CommitRange | None:
    """Return the listed contents of ``base..tip``, if the run listed them."""
    return facts.range_contents.get(range_key(base, tip))


def _range_excluding_parent(
    facts: GraphFacts, base: str, tip: str
) -> CommitRange | None:
    """Return the listed ``base..tip`` contents without the parent's history."""
    return facts.range_minus_parent.get(range_key(base, tip))


def _patch_identifier(value: str | None) -> str | None:
    """Return a patch identifier, or ``None`` when there is none to compare.

    An empty identifier is not evidence of a difference and not evidence of an
    agreement: ``git patch-id`` prints nothing for a diff that produces no
    patch, and a configured external diff driver makes it print nothing for
    every diff. Normalising the blank here is what keeps those two cases from
    reading as a comparison that was made.

    Returns
    -------
    str | None
        The identifier without surrounding whitespace, or ``None`` when there
        is none to compare.

    """
    if value is None:
        return None
    text = value.strip()
    return text or None


def _short(commit: str) -> str:
    """Return the abbreviated commit a gate's detail or a reason names.

    The abbreviation is the same one the report prints, so a reader can match a
    gate's detail to a line of the report by eye.

    Returns
    -------
    str
        The commit abbreviated the way every rendered commit is.

    """
    return commit[:COMMIT_ABBREVIATION]


def _ancestry_detail(observed: Ancestry, *, left: str, right: str) -> str:
    """Return how one ancestry answer reads in a gate's detail line."""
    near, far = _short(left), _short(right)
    if observed is Ancestry.UNKNOWN:
        return f"Git could not answer whether {near} is an ancestor of {far}"
    return f"{near} {_POLARITY_WORDS[observed]} an ancestor of {far}"


def _ancestor_gate(
    name: GateName, facts: GraphFacts, *, left: str, right: str
) -> GateResult:
    """Return the gate result for "``left`` is an ancestor of ``right``"."""
    observed = _ancestry(facts, left, right)
    return GateResult(
        name,
        ancestry_outcome(observed, expect=Ancestry.ANCESTOR),
        _ancestry_detail(observed, left=left, right=right),
    )


def _not_applicable(name: GateName, why: str) -> GateResult:
    """Return the result for a gate whose subject the run never set out to use."""
    return GateResult(name, GateOutcome.INDETERMINATE, why, applicable=False)


def _identity(identity: stack_records.PullRequestIdentity) -> str:
    """Return a pull request identity as a report spells it."""
    return f"{identity.repository}#{identity.number}"


def _parent_identity_gate(inputs: _GateInputs) -> GateResult:
    """Gate 1: the parent consulted is the one requested, from its own repo."""
    name = GateName.PARENT_IDENTITY_MATCHES
    parent = inputs.parent
    if not inputs.parent_gates:
        return _not_applicable(name, _NO_PARENT)
    if parent is None:
        return GateResult(name, GateOutcome.INDETERMINATE, _UNRESOLVED_PARENT)
    requested = inputs.request.parent
    if requested is not None and requested != parent.identity:
        found = _identity(parent.identity)
        return GateResult(
            name,
            GateOutcome.FAILED,
            f"the parent resolved to {found}, not the requested {_identity(requested)}",
        )
    if parent.head_fetched_from is None:
        return GateResult(name, GateOutcome.INDETERMINATE, _NO_HEAD_ORIGIN)
    if parent.head_fetched_from != parent.head_repository:
        return GateResult(
            name,
            GateOutcome.FAILED,
            f"the parent head was fetched from {parent.head_fetched_from} rather "
            f"than from its own {parent.head_repository}",
        )
    return GateResult(
        name,
        GateOutcome.PASSED,
        f"the parent head came from its own {parent.head_repository}",
    )


def _parent_merged_gate(inputs: _GateInputs) -> GateResult:
    """Gate 2: the parent pull request reports itself merged."""
    name = GateName.PARENT_MERGED
    if not inputs.parent_gates:
        return _not_applicable(name, _NO_PARENT)
    parent = inputs.parent
    if parent is None:
        return GateResult(name, GateOutcome.INDETERMINATE, _UNRESOLVED_PARENT)
    if not parent.merged:
        return GateResult(
            name,
            GateOutcome.FAILED,
            "the parent pull request is not merged, so its work has not landed",
        )
    if parent.merged_at is None:
        return GateResult(
            name,
            GateOutcome.INDETERMINATE,
            "the parent pull request reports itself merged and names no instant",
        )
    return GateResult(
        name, GateOutcome.PASSED, f"the parent merged at {parent.merged_at}"
    )


def _landed_reachable_gate(inputs: _GateInputs) -> GateResult:
    """Gate 3: the parent's integration commit is on the replay target."""
    name = GateName.LANDED_REACHABLE_FROM_TARGET
    landed = inputs.facts.landed
    if not inputs.parent_gates:
        return _not_applicable(name, _NO_PARENT)
    if landed is None:
        return GateResult(
            name,
            GateOutcome.INDETERMINATE,
            "no integration commit was resolved for the parent pull request",
        )
    return _ancestor_gate(
        name,
        inputs.facts,
        left=landed,
        right=inputs.request.target,
    )


def _boundary_is_ancestor_gate(inputs: _GateInputs) -> GateResult:
    """Gate 4: the candidate boundary lies on the child's own history."""
    return _ancestor_gate(
        GateName.BOUNDARY_IS_ANCESTOR_OF_CHILD,
        inputs.facts,
        left=inputs.candidate.commit,
        right=inputs.request.child_tip,
    )


def _replay_range_gate(inputs: _GateInputs) -> GateResult:
    """Gate 5: the child has work to replay from the candidate boundary."""
    name = GateName.REPLAY_RANGE_NON_EMPTY
    commit = inputs.candidate.commit
    child_tip = inputs.request.child_tip
    contents = _range(inputs.facts, commit, child_tip)
    if contents is None:
        return GateResult(
            name,
            GateOutcome.INDETERMINATE,
            f"the replay range {range_key(commit, child_tip)} was not listed",
        )
    if not contents.commits:
        return GateResult(
            name,
            GateOutcome.FAILED,
            f"the replay range {range_key(commit, child_tip)} is empty, so the "
            "child has no work to replay",
        )
    return GateResult(
        name,
        GateOutcome.PASSED,
        f"the replay range {range_key(commit, child_tip)} holds "
        f"{len(contents.commits)} commits",
    )


def _parent_history_gate(inputs: _GateInputs) -> GateResult:
    """Gate 6: the candidate lies on the parent's own history."""
    name = GateName.PARENT_HISTORY_INTACT
    parent_head = inputs.facts.parent_head
    if not (inputs.parent_gates or parent_head is not None):
        return _not_applicable(name, _NO_PARENT_HEAD)
    if parent_head is None:
        return GateResult(
            name,
            GateOutcome.INDETERMINATE,
            "the parent head could not be recovered",
        )
    return _ancestor_gate(
        name,
        inputs.facts,
        left=inputs.candidate.commit,
        right=parent_head,
    )


def _missing_parent_answer(facts: GraphFacts) -> str | None:
    """Return why the parent-side answers are missing, if they are."""
    if facts.parent_head is None:
        return "the parent head could not be recovered"
    if facts.landed is None:
        return "no integration commit was resolved for the parent pull request"
    return None


def _suffix_clause(
    commit: str,
    child_tip: str,
    contents: CommitRange,
    without_parent: CommitRange,
) -> _Clause:
    """Whether the replay range holds commits the parent's history reaches."""
    key = range_key(commit, child_tip)
    reachable = set(without_parent.commits)
    landed_work = [one for one in contents.commits if one not in reachable]
    if landed_work:
        return _Clause(
            GateOutcome.FAILED,
            f"{key} still holds {len(landed_work)} commit(s) the parent head reaches",
        )
    return _Clause(
        GateOutcome.PASSED,
        f"{key} holds no commit the parent head reaches",
    )


def _patch_clause(commit: str, facts: GraphFacts) -> _Clause:
    """Whether the range's cumulative patch is the patch the parent landed."""
    mine = _patch_identifier(facts.cumulative_patch.get(commit))
    theirs = _patch_identifier(facts.landed_patch)
    if mine is None or theirs is None:
        return _Clause(
            GateOutcome.INDETERMINATE,
            "no cumulative patch identifier could be compared with the landed commit's",
        )
    if mine == theirs:
        return _Clause(
            GateOutcome.FAILED,
            "the replay range applies exactly the patch the landed commit does",
        )
    return _Clause(
        GateOutcome.PASSED,
        "the replay range's cumulative patch differs from the landed commit's",
    )


def _landed_work_is_in_scope(inputs: _GateInputs) -> bool:
    """Whether the run can tell which work the parent already landed.

    The gate applies either because the run asked the parent's pull request
    about it, or because the graph named both the parent's head and the commit
    it landed — and the second is only half an answer until both are known.

    Parameters
    ----------
    inputs : _GateInputs
        What the gate sees.

    Returns
    -------
    bool
        Whether the gate has a subject to judge.

    """
    facts = inputs.facts
    return inputs.parent_gates or (
        facts.parent_head is not None and facts.landed is not None
    )


def _replay_range_excludes_landed_gate(inputs: _GateInputs) -> GateResult:
    """Gate 7: the replay range holds no work the parent already landed."""
    name = GateName.REPLAY_RANGE_EXCLUDES_LANDED_WORK
    if not _landed_work_is_in_scope(inputs):
        return _not_applicable(name, _NO_LANDED_WORK)
    facts = inputs.facts
    missing = _missing_parent_answer(facts)
    if missing is not None:
        return GateResult(name, GateOutcome.INDETERMINATE, missing)
    commit = inputs.candidate.commit
    child_tip = inputs.request.child_tip
    contents = _range(facts, commit, child_tip)
    without_parent = _range_excluding_parent(facts, commit, child_tip)
    if contents is None or without_parent is None:
        return GateResult(
            name,
            GateOutcome.INDETERMINATE,
            f"the replay range {range_key(commit, child_tip)} was not listed",
        )
    clause = _deciding(
        _suffix_clause(commit, child_tip, contents, without_parent),
        _patch_clause(commit, facts),
    )
    return GateResult(name, clause.outcome, clause.detail)


def _record_superseded_gate(inputs: _GateInputs) -> GateResult:
    """Gate 8: a stack record has not been overtaken by a later integration.

    A record whose birth tip is no longer the child's tip was written at a
    moment the branch has since moved past, so its claim is read as derived
    evidence rather than thrown away: the commit it names may still be the
    boundary, and another source agreeing with it says so.

    Returns
    -------
    GateResult
        The gate's result for the record, or a not-applicable one when the
        candidate is not a stack record.

    """
    name = GateName.RECORD_NOT_SUPERSEDED
    if not is_record_kind(inputs.candidate.kind):
        return _not_applicable(name, "the candidate is not a stack record")
    recorded_from = inputs.facts.record_recorded_from
    if recorded_from is None:
        return GateResult(
            name,
            GateOutcome.INDETERMINATE,
            "the record does not name the child tip it was written from",
        )
    commit = inputs.candidate.commit
    child_tip = inputs.request.child_tip
    clause = _deciding(
        _recorded_from_clause(inputs.facts, child_tip, recorded_from),
        _landed_since_record_clause(inputs.facts, commit),
    )
    return GateResult(name, clause.outcome, clause.detail)


def _recorded_from_clause(
    facts: GraphFacts,
    child_tip: str,
    recorded_from: str,
) -> _Clause:
    """Whether the tip the record was written from is still on the child's history."""
    if recorded_from == child_tip:
        return _Clause(
            GateOutcome.PASSED,
            "the record was written from the child's current tip",
        )
    observed = _ancestry(facts, recorded_from, child_tip)
    if observed is Ancestry.ANCESTOR:
        return _Clause(
            GateOutcome.PASSED,
            f"the record was written from {_short(recorded_from)}, which the "
            "child has since built on",
        )
    return _Clause(
        ancestry_outcome(observed, expect=Ancestry.ANCESTOR),
        _ancestry_detail(observed, left=recorded_from, right=child_tip),
    )


def _landed_since_record_clause(
    facts: GraphFacts,
    commit: str,
) -> _Clause:
    """Whether a parent integration newer than the record is visible.

    An integration is newer than the record when the parent's landed history no
    longer holds the boundary the record names: the record was written against a
    parent history that has since been rewritten, so the boundary it attests is
    not on the history the child would be replayed onto. A run that resolved no
    integration has found nothing that supersedes the record, which is why this
    clause passes rather than withholding an answer — it is the run's plan, and
    not the record's age, that decides whether a newer integration exists.

    Returns
    -------
    _Clause
        The clause deciding whether the parent's integration still holds the
        recorded boundary, which passes when no integration was resolved.

    """
    landed = facts.landed
    if landed is None:
        return _Clause(
            GateOutcome.PASSED,
            "no parent integration is visible to supersede the record",
        )
    observed = _ancestry(facts, commit, landed)
    if observed is Ancestry.ANCESTOR:
        return _Clause(
            GateOutcome.PASSED,
            f"the parent's integration {_short(landed)} still holds the recorded "
            "boundary",
        )
    return _Clause(
        ancestry_outcome(observed, expect=Ancestry.ANCESTOR),
        f"the parent's integration {_short(landed)} does not hold the recorded "
        "boundary, so the record has been overtaken",
    )


_GATE_EVALUATORS: typ.Final[typ.Mapping[GateName, _Evaluator]] = {
    GateName.PARENT_IDENTITY_MATCHES: _parent_identity_gate,
    GateName.PARENT_MERGED: _parent_merged_gate,
    GateName.LANDED_REACHABLE_FROM_TARGET: _landed_reachable_gate,
    GateName.BOUNDARY_IS_ANCESTOR_OF_CHILD: _boundary_is_ancestor_gate,
    GateName.REPLAY_RANGE_NON_EMPTY: _replay_range_gate,
    GateName.PARENT_HISTORY_INTACT: _parent_history_gate,
    GateName.REPLAY_RANGE_EXCLUDES_LANDED_WORK: _replay_range_excludes_landed_gate,
    GateName.RECORD_NOT_SUPERSEDED: _record_superseded_gate,
}
"""The evaluator of each gate, so the order and the coverage come from one list."""


def gate_results(
    request: BoundaryRequest,
    candidate: Candidate,
    facts: GraphFacts,
    parent: ParentPullRequest | None,
) -> tuple[GateResult, ...]:
    """Evaluate every gate against one candidate boundary.

    Every gate is evaluated and returned, whether or not it applies, in
    ``GATE_NAMES`` order: the report's table, the count of gates that applied,
    and the conjunction the assessment reads are then three readings of one
    sequence. A gate that does not apply is returned with ``applicable=False``
    and no claim either way.

    Parameters
    ----------
    request : BoundaryRequest
        What the run set out to answer.
    candidate : Candidate
        Boundary the gates are asked about.
    facts : GraphFacts
        The graph answers the gates read.
    parent : ParentPullRequest | None
        The parent pull request, when one was resolved.

    Returns
    -------
    tuple[GateResult, ...]
        One result per gate, in ``GATE_NAMES`` order.

    """
    inputs = _GateInputs(
        request=request,
        candidate=candidate,
        facts=facts,
        parent=parent,
        parent_gates=_parent_required(request, parent),
    )
    return tuple(_GATE_EVALUATORS[name](inputs) for name in GATE_NAMES)


def _parent_required(
    request: BoundaryRequest,
    parent: ParentPullRequest | None,
) -> bool:
    """Return whether the run set out to consult a parent pull request.

    This is the run's plan, not what collection produced: a run that was asked
    for a parent pull request set out to consult one even when the request
    failed, and a run that was not has no parent in play to be judged against.
    A run that went looking and came back empty therefore refuses instead of
    establishing a boundary no gate about the parent examined.

    Returns
    -------
    bool
        Whether the gates about a parent pull request apply to this run.

    """
    return request.parent is not None or parent is not None
