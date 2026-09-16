"""Builders shared by the ``git wheresat`` boundary-assessment suites.

The assessment is a function of four values — what the run was asked, which
candidates the evidence sources produced, what the graph answered, and what the
parent pull request reported — so both suites build those four as data and
neither opens a repository. ``permissive`` is the case in which a stack record's
boundary clears every gate, and every other case is that one with a single named
answer changed, which is what makes INV-4's truth table a table: one row per
gate, each row changing only the answer that gate reads.

The corpus is all this module holds. The cases built from it — one row per gate,
and the handful a report or a policy test needs by name — are in
:mod:`tests.unit.wheresat_variants`, which is where a gate's own row is added.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from git_donkey import stack_records
from git_donkey import wheresat_policy as policy
from git_donkey.wheresat_records import (
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

# Source names as the collector reports them: the label each question its rung
# asked is reported under. Corroboration is counted by *kind* rather than by
# these names, so the two merge-base questions are one source however they are
# labelled, and the two record kinds are two: a record read at birth and the
# same record read from the anchor ref that outlives it.
RECORD_SOURCE = "stack record"
ANCHOR_SOURCE = "stack-base anchor"
SHARED_SOURCE = "shared record"
MERGE_BASE_SOURCE = "merge base"
PARENT_MERGE_BASE_SOURCE = "merge base of the parent head"
FORK_POINT_SOURCE = "fork point"
TREE_SOURCE = "tree identity"
PATCH_SOURCE = "patch identity"

# Where the parent head was fetched from when it did not come from the pull
# request's own repository, which is the disagreement gate 1 refuses.
FOREIGN_REPOSITORY = "somebody/fork"

# The number of independent derived sources a derived boundary needs, where a
# source is a kind of evidence — one method of observation — and the number of
# gates a parent-less run leaves inapplicable.
REQUIRED_SOURCES = 2
PARENT_GATES = 2

# How many of the target's newest commits a case's run says it would compare
# against. The assessment reads the window for nothing — no gate asks about it —
# so a case that is about the gates states the default and is done with it.
DEFAULT_WINDOW = 200


@dataclasses.dataclass(frozen=True, slots=True)
class Case:
    """One assessment input: the run's question and every answer it reads.

    Attributes
    ----------
    request : BoundaryRequest
        What the run set out to answer, resolved before any question was put.
    candidates : tuple[Candidate, ...]
        Every candidate the evidence sources produced, in any order, which the
        gates are asked about rather than the case choosing among them.
    facts : GraphFacts
        The graph answers the gates read, built by the case rather than asked
        of a repository.
    parent : ParentPullRequest | None
        The parent pull request, when one was resolved, which is what decides
        whether the gates about a parent are applicable.

    """

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
        landed_twins={REPLAY_RANGE: (), OTHER_RANGE: ()},
        child_history=CommitRange(CHILD_HISTORY),
        cumulative_patch={OLD_BASE: BOUNDARY_PATCH, OTHER_BASE: BOUNDARY_PATCH},
        landed_patch=LANDED_PATCH,
        record_recorded_from=CHILD_TIP,
    )


def permissive() -> Case:
    """Return the case in which a stack record's boundary clears every gate.

    Returns
    -------
    Case
        The run's question, one attested candidate at ``OLD_BASE``, and the
        facts that pass every gate it applies to.

    """
    return Case(
        request=BoundaryRequest(
            branch="child",
            child_tip=CHILD_TIP,
            target=TARGET,
            parent=None,
            deep=False,
            heuristic_window=DEFAULT_WINDOW,
            offline=False,
        ),
        candidates=(attested(OLD_BASE, EvidenceKind.STACK_RECORD_BIRTH),),
        facts=permissive_facts(),
        parent=None,
    )


def parented() -> Case:
    """Return the case in which the run named a parent pull request.

    Returns
    -------
    Case
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


class ParentPullRequestOverrides(typ.TypedDict, total=False):
    """The parent pull request's fields an example may replace.

    Only the fields the examples here override are named, and every one is
    optional, so an example states just the field it is changing and the
    builder keeps its defaults for the rest. Naming them, with their types, is
    what lets a type checker reject an override that is misspelled or of the
    wrong type, which a bare ``**overrides: object`` could not.

    Attributes
    ----------
    identity : stack_records.PullRequestIdentity
        Repository slug and number the parent is named by.
    merged : bool
        Whether the parent has landed, which the merge-state gate reads.
    merged_at : str | None
        When it merged, or ``None`` for a parent that has not.
    head_sha : str
        Commit the parent's head ref named when the metadata was read.
    head_ref : str
        Branch the parent's head is on, in the repository it was opened from.
    head_repository : str
        Slug of the repository the pull request reports its head in.
    head_fetched_from : str | None
        Repository the head ref was actually read from, which gate 1 weighs
        against ``head_repository``; ``None`` when it was never fetched.
    landed : str | None
        Commit the parent's work landed as, or ``None`` when the metadata
        named none.
    stacked : bool
        Whether GitHub records the parent as part of a stack.

    """

    identity: stack_records.PullRequestIdentity
    merged: bool
    merged_at: str | None
    head_sha: str
    head_ref: str
    head_repository: str
    head_fetched_from: str | None
    landed: str | None
    stacked: bool


def parent_pull_request(
    **overrides: typ.Unpack[ParentPullRequestOverrides],
) -> ParentPullRequest:
    """Return a merged parent pull request whose head came from its own repo.

    Parameters
    ----------
    **overrides : ParentPullRequestOverrides, optional
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
    return dataclasses.replace(base, **overrides)


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


def assessment_of(case: Case) -> Assessment:
    """Return the assessment ``case`` produces.

    Parameters
    ----------
    case : Case
        The run's question and every answer it reads.

    Returns
    -------
    Assessment
        What the policy makes of the case.

    """
    return policy.assess(case.request, case.candidates, case.facts, case.parent)


def failed_gates(assessment: Assessment) -> tuple[GateName, ...]:
    """Return the gates that ruled against a candidate.

    Parameters
    ----------
    assessment : Assessment
        Verdict whose refusals are wanted.

    Returns
    -------
    tuple[GateName, ...]
        Every gate that was asked and answered ``FAILED``: the reasons a run
        can give for not establishing a boundary.

    """
    return _gates_with(assessment, GateOutcome.FAILED)


def undecided_gates(assessment: Assessment) -> tuple[GateName, ...]:
    """Return the gates that could not rule at all.

    Parameters
    ----------
    assessment : Assessment
        Verdict to read the gates that went unanswered from.

    Returns
    -------
    tuple[GateName, ...]
        Every gate that was asked and had no answer to give, which says less
        than a refusal does: the run knows no more than before it asked.

    """
    return _gates_with(assessment, GateOutcome.INDETERMINATE)


def _gates_with(assessment: Assessment, outcome: GateOutcome) -> tuple[GateName, ...]:
    """Return the gates of ``assessment`` that answered ``outcome``.

    Parameters
    ----------
    assessment : Assessment
        Verdict to sift.
    outcome : GateOutcome
        The answer a gate must have given to be returned.

    Returns
    -------
    tuple[GateName, ...]
        The applicable gates answering ``outcome``, in the order the run asked
        them, with a gate that did not apply left out.

    """
    return tuple(
        gate.name
        for gate in assessment.gates
        if gate.applicable and gate.outcome is outcome
    )
