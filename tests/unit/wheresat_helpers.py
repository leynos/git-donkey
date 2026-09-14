"""Builders shared by the ``git wheresat`` boundary-assessment suites.

The assessment is a function of four values — what the run was asked, which
candidates the evidence sources produced, what the graph answered, and what the
parent pull request reported — so both suites build those four as data and
neither opens a repository. ``permissive`` is the case in which a stack record's
boundary clears every gate, and every other case is that one with a single named
answer changed, which is what makes INV-4's truth table a table: one row per
gate, each row changing only the answer that gate reads.

``spoiled`` builds a row by gate name, so the table is indexed by the same
``GateName`` values the policy dispatches on. A gate the procedure gains is a
gate the suite reports as having no row, and a gate that is renamed cannot leave
the table silently pointing at nothing.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from git_donkey import stack_records
from git_donkey import wheresat_policy as policy
from git_donkey.wheresat_records import (
    COMMIT_ABBREVIATION,
    Ancestry,
    Assessment,
    AttestedCandidate,
    BoundaryRequest,
    Candidate,
    CommitRange,
    DerivedCandidate,
    EvidenceKind,
    GateName,
    GateOutcome,
    GraphFacts,
    InferredCandidate,
    ParentPullRequest,
    range_key,
)

# One repeated digit per commit, so a detail line a failing example prints names
# the commit the example meant at a glance. Every boundary in these suites is
# either the old base, which the record attests, or the alternative that makes
# the answer ambiguous.
CHILD_TIP = "1" * 40
CHILD_BELOW = "2" * 40
INHERITED = "3" * 40
TRUNK = "4" * 40
PARENT_HEAD = "5" * 40
LANDED = "6" * 40
TARGET = "7" * 40
OLD_BASE = "8" * 40
OTHER_BASE = "9" * 40

# The child's own work, the commit the record was written from, and the history
# below it: the partition an established result has to reproduce.
CHILD_WORK = (CHILD_TIP, CHILD_BELOW)
CHILD_HISTORY = (CHILD_TIP, CHILD_BELOW, INHERITED, TRUNK)

# Two patch identifiers that differ, so the cumulative-patch clause of gate 7
# passes by default and one spoiler can make the two agree.
BOUNDARY_PATCH = "a" * 40
LANDED_PATCH = "b" * 40

REPLAY_RANGE = range_key(OLD_BASE, CHILD_TIP)
OTHER_RANGE = range_key(OTHER_BASE, CHILD_TIP)

# The pull request a run is told to consult, and the repository it heads.
PR_IDENTITY = stack_records.PullRequestIdentity(repository="acme/widget", number=41)
PR_REPOSITORY = "acme/widget"

# Source names as the collector reports them: corroboration counts distinct
# sources, and the two record kinds are one record read at two moments — by the
# branch configuration and by the anchor ref that outlives it.
RECORD_SOURCE = "stack record"
ANCHOR_SOURCE = "stack-base anchor"
SHARED_SOURCE = "shared record"
MERGE_BASE_SOURCE = "merge base"
FORK_POINT_SOURCE = "fork point"
TREE_SOURCE = "tree identity"
PATCH_SOURCE = "patch identity"

# Where the parent head was fetched from when it did not come from the pull
# request's own repository, which is the disagreement gate 1 refuses.
FOREIGN_REPOSITORY = "somebody/fork"

# The number of independent derived sources a derived boundary needs, and the
# number of gates a parent-less run leaves unapplicable.
REQUIRED_SOURCES = 2
PARENT_GATES = 2


@dataclasses.dataclass(frozen=True, slots=True)
class _Case:
    """One assessment input: the run's question and every answer it reads."""

    request: BoundaryRequest
    candidates: tuple[Candidate, ...]
    facts: GraphFacts
    parent: ParentPullRequest | None


def permissive_facts() -> GraphFacts:
    """Return facts in which every gate passes for the boundary at ``OLD_BASE``.

    The answers cover ``OTHER_BASE`` as well, so an example can place its
    candidate at either boundary without rebuilding the graph, and the record's
    ``record_recorded_from`` names the child tip the record was written from.

    Returns
    -------
    GraphFacts
        Facts in which nothing answers against the boundary.

    """
    return GraphFacts(
        parent_head=PARENT_HEAD,
        landed=LANDED,
        ancestry={
            (OLD_BASE, CHILD_TIP): Ancestry.ANCESTOR,
            (OTHER_BASE, CHILD_TIP): Ancestry.ANCESTOR,
            (OLD_BASE, PARENT_HEAD): Ancestry.ANCESTOR,
            (OTHER_BASE, PARENT_HEAD): Ancestry.ANCESTOR,
            (LANDED, TARGET): Ancestry.ANCESTOR,
            (PARENT_HEAD, CHILD_TIP): Ancestry.NOT_ANCESTOR,
            (OLD_BASE, LANDED): Ancestry.ANCESTOR,
        },
        range_contents={
            REPLAY_RANGE: CommitRange(CHILD_WORK),
            OTHER_RANGE: CommitRange(CHILD_WORK),
        },
        range_minus_parent={
            REPLAY_RANGE: CommitRange(CHILD_WORK),
            OTHER_RANGE: CommitRange(CHILD_WORK),
        },
        child_history=CommitRange(CHILD_HISTORY),
        cumulative_patch={OLD_BASE: BOUNDARY_PATCH, OTHER_BASE: BOUNDARY_PATCH},
        landed_patch=LANDED_PATCH,
        record_recorded_from=CHILD_TIP,
    )


def permissive() -> _Case:
    """Return the case in which a stack record's boundary clears every gate.

    Returns
    -------
    _Case
        The run's question, one attested candidate at ``OLD_BASE``, and the
        facts that pass every gate it applies to.

    """
    return _Case(
        request=BoundaryRequest(
            branch="child",
            child_tip=CHILD_TIP,
            target=TARGET,
            parent=None,
            deep=False,
            offline=False,
        ),
        candidates=(attested(OLD_BASE, EvidenceKind.STACK_RECORD_BIRTH),),
        facts=permissive_facts(),
        parent=None,
    )


def parented() -> _Case:
    """Return the case in which the run named a parent pull request.

    Returns
    -------
    _Case
        The permissive case with the parent resolved, so the gates about a
        parent pull request apply and pass.

    """
    case = permissive()
    resolved = parent_pull_request()
    return dataclasses.replace(
        case,
        request=dataclasses.replace(case.request, parent=PR_IDENTITY),
        parent=resolved,
    )


def parent_pull_request(**overrides: object) -> ParentPullRequest:
    """Return a merged parent pull request whose head came from its own repo.

    Parameters
    ----------
    **overrides : object
        Fields to replace, for the examples that spoil one of them.

    Returns
    -------
    ParentPullRequest
        The parent pull request, as an example configures it.

    """
    base = ParentPullRequest(
        identity=PR_IDENTITY,
        merged=True,
        merged_at="2026-09-01T09:30:00Z",
        head_sha=PARENT_HEAD,
        head_ref="parent",
        head_repository=PR_REPOSITORY,
        head_fetched_from=PR_REPOSITORY,
        base_ref="main",
        base_repository=PR_REPOSITORY,
        landed=LANDED,
        stacked=False,
    )
    return dataclasses.replace(base, **typ.cast("typ.Any", overrides))


def _candidate[C: (AttestedCandidate, DerivedCandidate, InferredCandidate)](
    candidate_type: type[C],
    commit: str,
    kind: EvidenceKind,
    source: str,
) -> C:
    """Return a candidate of ``candidate_type`` naming ``commit``."""
    return candidate_type(commit=commit, kind=kind, source=source)


def attested(
    commit: str,
    kind: EvidenceKind = EvidenceKind.STACK_RECORD_BIRTH,
    *,
    source: str = RECORD_SOURCE,
) -> AttestedCandidate:
    """Return an attested candidate naming ``commit``.

    Attested evidence is what a record written at birth or a review approval
    carries: a deliberate statement, which is the only kind of support that
    can establish a boundary on its own.

    Parameters
    ----------
    commit : str
        Commit the candidate offers as the boundary.
    kind : EvidenceKind, optional
        A kind ``TIERS`` classes as attested; the birth record by default.
    source : str, optional
        Which producer stated it; the birth record by default.

    Returns
    -------
    AttestedCandidate
        A candidate that can establish a boundary on its own.

    """
    return _candidate(AttestedCandidate, commit, kind, source)


def derived(
    commit: str,
    kind: EvidenceKind = EvidenceKind.MERGE_BASE,
    *,
    source: str = MERGE_BASE_SOURCE,
) -> DerivedCandidate:
    """Return a derived candidate naming ``commit``.

    Derived evidence is computed from the repository rather than stated, so
    it carries a boundary only when two independent sources agree on it.

    Parameters
    ----------
    commit : str
        Commit the candidate computes as the boundary.
    kind : EvidenceKind, optional
        A kind ``TIERS`` classes as derived; the merge base by default.
    source : str, optional
        The computation that produced it; the merge base by default.

    Returns
    -------
    DerivedCandidate
        A candidate that supports a boundary only alongside another.

    """
    return _candidate(DerivedCandidate, commit, kind, source)


def inferred(
    commit: str,
    kind: EvidenceKind = EvidenceKind.TREE_IDENTITY,
    *,
    source: str = TREE_SOURCE,
) -> InferredCandidate:
    """Return an inferred candidate naming ``commit``.

    Inferred evidence compares content rather than history, which is why a
    ``--deep`` search can report it beside a boundary but never as support for
    one.

    Parameters
    ----------
    commit : str
        Commit whose content resembles the boundary.
    kind : EvidenceKind, optional
        A kind ``TIERS`` classes as inferred; the tree identity by default.
    source : str, optional
        The comparison that produced it; the tree identity by default.

    Returns
    -------
    InferredCandidate
        A candidate that can never serve as support, only as a lead.

    """
    return _candidate(InferredCandidate, commit, kind, source)


def assessment_of(case: _Case) -> Assessment:
    """Return the assessment ``case`` produces.

    Parameters
    ----------
    case : _Case
        The run's question and every answer it reads.

    Returns
    -------
    Assessment
        What the policy makes of the case.

    """
    return policy.assess(case.request, case.candidates, case.facts, case.parent)


def failed_gates(assessment: Assessment) -> tuple[GateName, ...]:
    """Return the names of the gates that ruled against a candidate.

    Parameters
    ----------
    assessment : Assessment
        Verdict whose refusals are wanted.

    Returns
    -------
    tuple[GateName, ...]
        The gates that answered ``FAILED``, in the order the run asked them;
        a gate that did not apply is left out.

    """
    return _gates_with(assessment, GateOutcome.FAILED)


def undecided_gates(assessment: Assessment) -> tuple[GateName, ...]:
    """Return the names of the gates that could not rule at all.

    Parameters
    ----------
    assessment : Assessment
        Verdict to read the unanswered gates from.

    Returns
    -------
    tuple[GateName, ...]
        The gates that came back ``INDETERMINATE``, in the order the run
        asked them; a gate that did not apply is left out.

    """
    return _gates_with(assessment, GateOutcome.INDETERMINATE)


def _gates_with(assessment: Assessment, outcome: GateOutcome) -> tuple[GateName, ...]:
    """Return the applicable gates of ``assessment`` with ``outcome``."""
    return tuple(
        gate.name
        for gate in assessment.gates
        if gate.applicable and gate.outcome is outcome
    )


def _with_ancestry(key: tuple[str, str], answer: Ancestry | None) -> GraphFacts:
    """Return the permissive facts with one ancestry answer set or unasked."""
    facts = permissive_facts()
    answers = dict(facts.ancestry)
    if answer is None:
        del answers[key]
    else:
        answers[key] = answer
    return dataclasses.replace(facts, ancestry=answers)


def _with_range_contents(key: str, contents: CommitRange | None) -> GraphFacts:
    """Return the permissive facts with one listed range set or unlisted."""
    facts = permissive_facts()
    listed = dict(facts.range_contents)
    if contents is None:
        del listed[key]
    else:
        listed[key] = contents
    return dataclasses.replace(facts, range_contents=listed)


def _with_range_minus_parent(key: str, contents: CommitRange | None) -> GraphFacts:
    """Return the permissive facts with one parent-free range set or unlisted."""
    facts = permissive_facts()
    listed = dict(facts.range_minus_parent)
    if contents is None:
        del listed[key]
    else:
        listed[key] = contents
    return dataclasses.replace(facts, range_minus_parent=listed)


def _with_patch(commit: str, identifier: str | None) -> GraphFacts:
    """Return the permissive facts with one cumulative patch identifier set."""
    facts = permissive_facts()
    identifiers = dict(facts.cumulative_patch)
    identifiers[commit] = identifier
    return dataclasses.replace(facts, cumulative_patch=identifiers)


def _with_recorded_from(
    commit: str | None, answer: Ancestry | None = None
) -> GraphFacts:
    """Return the permissive facts with the record's birth tip set or absent.

    Parameters
    ----------
    commit : str | None
        The child tip the record was written from, or ``None`` when the record
        names no tip at all.
    answer : Ancestry | None, optional
        Whether that tip is still on the child's history, when the run asked.

    Returns
    -------
    GraphFacts
        The facts, with the record's own history question answered or unasked.

    """
    facts = permissive_facts()
    if commit is not None and answer is not None:
        facts = _with_ancestry((commit, CHILD_TIP), answer)
    return dataclasses.replace(facts, record_recorded_from=commit)


def _with_parent_head(commit: str | None) -> GraphFacts:
    """Return the permissive facts with the recovered parent head set or absent."""
    return dataclasses.replace(permissive_facts(), parent_head=commit)


def _with_landed(commit: str | None) -> GraphFacts:
    """Return the permissive facts with the integration commit set or absent."""
    return dataclasses.replace(permissive_facts(), landed=commit)


def _with_child_history(contents: CommitRange) -> GraphFacts:
    """Return the permissive facts with the child's own history relisted."""
    return dataclasses.replace(permissive_facts(), child_history=contents)


def _changed(facts: GraphFacts) -> _Case:
    """Return the permissive case judged against ``facts`` instead."""
    return dataclasses.replace(permissive(), facts=facts)


def _spoil_parent_identity(*, failed: bool) -> _Case:
    """Return the case in which gate 1 alone answers against the candidate."""
    fetched_from = FOREIGN_REPOSITORY if failed else None
    case = parented()
    return dataclasses.replace(
        case,
        parent=parent_pull_request(head_fetched_from=fetched_from),
    )


def _spoil_parent_merged(*, failed: bool) -> _Case:
    """Return the case in which gate 2 alone answers against the candidate."""
    case = parented()
    if failed:
        return dataclasses.replace(case, parent=parent_pull_request(merged=False))
    return dataclasses.replace(case, parent=parent_pull_request(merged_at=None))


def _spoil_landed_reachable(*, failed: bool) -> _Case:
    """Return the case in which gate 3 alone answers against the candidate.

    The case consults a parent pull request, because that is what makes gate 3
    applicable at all: a parentless run leaves the integration commit out of its
    conjunction rather than judging it.

    Returns
    -------
    _Case
        The case in which gate 3 alone answers against the candidate, or alone
        goes unanswered.

    """
    answer = Ancestry.NOT_ANCESTOR if failed else None
    case = parented()
    return dataclasses.replace(case, facts=_with_ancestry((LANDED, TARGET), answer))


def _spoil_boundary_ancestor(*, failed: bool) -> _Case:
    """Return the case in which gate 4 alone answers against the candidate."""
    answer = Ancestry.NOT_ANCESTOR if failed else None
    return _changed(_with_ancestry((OLD_BASE, CHILD_TIP), answer))


def _spoil_replay_range(*, failed: bool) -> _Case:
    """Return the case in which gate 5 alone answers against the candidate."""
    listed = CommitRange(()) if failed else None
    return _changed(_with_range_contents(REPLAY_RANGE, listed))


def _spoil_parent_history(*, failed: bool) -> _Case:
    """Return the case in which gate 6 alone answers against the candidate."""
    answer = Ancestry.NOT_ANCESTOR if failed else None
    return _changed(_with_ancestry((OLD_BASE, PARENT_HEAD), answer))


def _spoil_excludes_landed(*, failed: bool) -> _Case:
    """Return the case in which gate 7 alone answers against the candidate."""
    if failed:
        return _changed(_with_patch(OLD_BASE, LANDED_PATCH))
    return _changed(_with_range_minus_parent(REPLAY_RANGE, None))


def _spoil_record_superseded(*, failed: bool) -> _Case:
    """Return the case in which gate 8 alone answers against the candidate."""
    if failed:
        return _changed(
            _with_recorded_from(CHILD_BELOW, Ancestry.NOT_ANCESTOR),
        )
    return _changed(_with_recorded_from(None))


# One spoiler per gate, each taking whether the gate is to fail rather than go
# unanswered. Indexing them by name is what makes the truth table complete: a
# gate with no spoiler raises here rather than being skipped.
type _Spoiler = typ.Callable[..., _Case]

_SPOILERS: typ.Mapping[GateName, _Spoiler] = {
    GateName.PARENT_IDENTITY_MATCHES: _spoil_parent_identity,
    GateName.PARENT_MERGED: _spoil_parent_merged,
    GateName.LANDED_REACHABLE_FROM_TARGET: _spoil_landed_reachable,
    GateName.BOUNDARY_IS_ANCESTOR_OF_CHILD: _spoil_boundary_ancestor,
    GateName.REPLAY_RANGE_NON_EMPTY: _spoil_replay_range,
    GateName.PARENT_HISTORY_INTACT: _spoil_parent_history,
    GateName.REPLAY_RANGE_EXCLUDES_LANDED_WORK: _spoil_excludes_landed,
    GateName.RECORD_NOT_SUPERSEDED: _spoil_record_superseded,
}


def spoiled(gate: GateName, outcome: GateOutcome) -> _Case:
    """Return the case in which ``gate`` alone takes ``outcome``.

    Parameters
    ----------
    gate : GateName
        Gate the case is built around.
    outcome : GateOutcome
        ``FAILED`` for a gate that answers against the candidate, or
        ``INDETERMINATE`` for one left without an answer.

    Returns
    -------
    _Case
        The permissive case with the one answer ``gate`` reads changed, so
        every other gate passes and the gate under test is the only one that
        did not.

    """
    return _SPOILERS[gate](failed=outcome is GateOutcome.FAILED)


def parented_without_head() -> _Case:
    """Return the parent-consulting case with no parent head recovered.

    Returns
    -------
    _Case
        The case whose gates about the parent apply — the run set out to consult
        one — and whose questions about its history have nothing to read.

    """
    return dataclasses.replace(parented(), facts=_with_parent_head(None))


def parented_without_landed() -> _Case:
    """Return the parent-consulting case with no integration commit resolved.

    Returns
    -------
    _Case
        The case whose gates about the parent apply and whose landed-work
        questions cannot be answered, which is what a parent merged by a
        strategy the run cannot resolve looks like.

    """
    return dataclasses.replace(parented(), facts=_with_landed(None))


def without_parent_head() -> _Case:
    """Return the parentless case with no parent head recovered.

    Returns
    -------
    _Case
        The case in which no gate about a parent's history applies, which is
        what a local run whose parent left no tombstone and no remote-tracking
        ref looks like.

    """
    return _changed(_with_parent_head(None))


def without_landed() -> _Case:
    """Return the parentless case with no integration commit resolved.

    Returns
    -------
    _Case
        The case in which the parent's history is still judged — its head was
        recovered — and nothing about an integration is.

    """
    return _changed(_with_landed(None))


def short(commit: str) -> str:
    """Return ``commit`` as a reason or a report abbreviates it.

    Parameters
    ----------
    commit : str
        Full object ID.

    Returns
    -------
    str
        The abbreviation a report's reader is expected to paste into Git.

    """
    return commit[:COMMIT_ABBREVIATION]


def unconsulted_parent() -> _Case:
    """Return the case whose run named a parent pull request it could not read.

    Returns
    -------
    _Case
        The case whose gates about the parent apply — the run set out to
        consult one — and go unanswered, since nothing resolved it.

    """
    return dataclasses.replace(parented(), parent=None)


def with_candidates(case: _Case, *candidates: Candidate) -> _Case:
    """Return ``case`` judged against ``candidates`` instead of its own.

    Parameters
    ----------
    case : _Case
        The run's question and every answer it reads.
    *candidates : Candidate
        Candidates the evidence sources produced, in any order.

    Returns
    -------
    _Case
        The case, with the candidates the assessment is to choose between.

    """
    return dataclasses.replace(case, candidates=tuple(candidates))


def record_superseded() -> _Case:
    """Return the permissive case whose record a later integration superseded.

    Returns
    -------
    _Case
        The case whose record was written from a commit the child has since
        discarded, which is the one gate 8 exists to refuse.

    """
    return _changed(_with_recorded_from(CHILD_BELOW, Ancestry.NOT_ANCESTOR))


def blank_patch_identifier() -> _Case:
    """Return the permissive case whose cumulative patch identifier is blank.

    Returns
    -------
    _Case
        The case whose patch pipeline produced no identifier, which is what a
        configured external diff driver makes it do for every range.

    """
    return _changed(_with_patch(OLD_BASE, ""))


def truncated_replay_range() -> _Case:
    """Return the permissive case whose replay range was cut short.

    Returns
    -------
    _Case
        The case whose established result has to report that the history it
        partitioned does not span the whole range.

    """
    listed = CommitRange(CHILD_WORK, truncated=True)
    return _changed(_with_range_contents(REPLAY_RANGE, listed))


def truncated_history() -> _Case:
    """Return the permissive case whose child history was cut short.

    Returns
    -------
    _Case
        The case whose excluded commits are only the ones the run listed.

    """
    return _changed(_with_child_history(CommitRange(CHILD_HISTORY, truncated=True)))


def long_replay_range(count: int) -> _Case:
    """Return the permissive case whose ranges hold ``count`` commits.

    Parameters
    ----------
    count : int
        How many commits the run listed above the boundary.

    Returns
    -------
    _Case
        The case whose partition is longer than a report lists in full, so the
        listing has to say how many commits it withheld. The ranges are complete
        — nothing here was cut short — which is what tells this case apart from
        the two above: the commits are missing from the *rendering*, not from the
        run's answer.

    """
    # The leading digits are the number of the commit, which is also all of it a
    # report prints, so the listing shows one distinguishable commit per line.
    # The fixed commits above are one digit repeated, so a generated commit
    # cannot abbreviate to one of them and read as though the gap a truncation
    # tail reports were a commit the report already named.
    listed = CommitRange(
        tuple(f"{index:07d}{'a' * 33}" for index in range(10, 10 + count)),
    )
    facts = _with_range_minus_parent(REPLAY_RANGE, listed)
    return _changed(
        dataclasses.replace(
            facts,
            range_contents={**facts.range_contents, REPLAY_RANGE: listed},
            child_history=listed,
        )
    )
