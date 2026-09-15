"""Property tests for the ``git wheresat`` boundary assessment.

``test_wheresat_policy.py`` pins the gates and the verdicts with readable cases;
the invariants themselves are about *every* corpus of evidence, so these tests
generalise them over generated ones. Each example draws a child history, places
candidates on it, and answers each graph question at random — including leaving
questions unasked — and the invariants are then read off whatever the assessment
makes of that:

* INV-2 and INV-2b, that inferred evidence never establishes a boundary and that
  derived evidence needs more than one source to;
* INV-3, that the verdict is a property of the evidence rather than of the order
  it arrived in;
* INV-4, that an established boundary passed every gate that applied to it;
* INV-5, that a refusal never hides an unanswered question;
* INV-6, that the boundary partitions the child's history.

The generated corpus is deliberately incoherent: most examples are not scenarios
any repository could present, which is the point. An invariant that holds only
for coherent repositories is an invariant the command does not have, and what is
under test is what the assessment does with whatever answers it is handed.
"""

from __future__ import annotations

import typing as typ

from hypothesis import given
from hypothesis import strategies as st

from git_donkey.wheresat_records import (
    EXIT_CODES,
    GATE_NAMES,
    Ancestry,
    Assessment,
    AttestedCandidate,
    BoundaryRequest,
    CommitRange,
    Established,
    EvidenceKind,
    GateOutcome,
    GateResult,
    GraphFacts,
    Indeterminate,
    InferredCandidate,
    ParentPullRequest,
    Unresolved,
    candidate_for,
    range_key,
)
from tests.unit.wheresat_helpers import (
    FOREIGN_REPOSITORY,
    LANDED_PATCH,
    OLD_BASE,
    PR_IDENTITY,
    PR_REPOSITORY,
    REQUIRED_SOURCES,
    _Case,
    assessment_of,
    inferred,
    parent_pull_request,
)

# The child history an example is built on: two commits, so a boundary can sit
# below the tip, and at most six, so each example stays cheap to read.
_HISTORY_MIN = 2
_HISTORY_MAX = 6
_OBJECT_IDS = st.lists(
    st.integers(min_value=1, max_value=1_000_000),
    min_size=_HISTORY_MIN,
    max_size=_HISTORY_MAX,
    unique=True,
)

# At most this many candidates are placed, so one example can mix tiers and
# sources without becoming a corpus nobody can read.
_CANDIDATE_MAX = 4

# Every candidate is judged by every gate, in GATE_NAMES order, so a
# candidate's results are a slice of the assessment's gates of this length.
_GATES_PER_CANDIDATE = len(GATE_NAMES)

_SOURCES = st.sampled_from((
    "stack record",
    "shared record",
    "merge base",
    "fork point",
    "tree identity",
    "patch identity",
))
_ANSWERS = st.sampled_from((Ancestry.ANCESTOR, Ancestry.NOT_ANCESTOR))
_PATCH_IDENTIFIERS = st.one_of(st.none(), st.just(""), st.just(LANDED_PATCH))
_MERGED_AT = st.one_of(st.none(), st.just("2026-09-01T09:30:00Z"))
_FETCHED_FROM = st.one_of(
    st.none(), st.just(PR_REPOSITORY), st.just(FOREIGN_REPOSITORY)
)


def _object_id(number: int) -> str:
    """Return a forty-hex object ID for ``number``.

    Parameters
    ----------
    number : int
        Value to render, which the generator draws uniquely.

    Returns
    -------
    str
        An object ID naming a commit no other drawn number names.

    """
    return f"{number:040x}"


def _replayed(commits: tuple[str, ...], boundary: str) -> tuple[str, ...]:
    """Return the commits a replay range from ``boundary`` holds.

    Parameters
    ----------
    commits : tuple[str, ...]
        The child's history, tip first.
    boundary : str
        Commit the range excludes.

    Returns
    -------
    tuple[str, ...]
        The child's own work above the boundary.

    """
    return commits[: commits.index(boundary)]


def _ancestry(
    commits: tuple[str, ...],
    draw: st.DrawFn,
) -> typ.Mapping[tuple[str, str], Ancestry]:
    """Draw an answer, or no answer at all, for some ordered commit pairs."""
    answers = {}
    for left in commits:
        for right in commits:
            if left == right:
                continue
            answer = draw(st.one_of(st.none(), _ANSWERS))
            if answer is not None:
                answers[left, right] = answer
    return answers


def _without_some(
    contents: typ.Mapping[str, CommitRange],
    draw: st.DrawFn,
) -> typ.Mapping[str, CommitRange]:
    """Draw the same ranges as the parent's history leaves them.

    A commit the parent's head reaches is missing from the listing, which is the
    half of gate 7 that the suffix clause reads.

    Returns
    -------
    typ.Mapping[str, CommitRange]
        The same ranges, with the commits the parent's head reaches left out.

    """
    kept = {}
    for key, listed in contents.items():
        chosen = draw(
            st.lists(
                st.booleans(),
                min_size=len(listed.commits),
                max_size=len(listed.commits),
            )
        )
        selected = zip(listed.commits, chosen, strict=True)
        kept[key] = CommitRange(
            tuple(one for one, keep in selected if keep),
            listed.truncated,
        )
    return kept


def _request(
    child_tip: str,
    commits: tuple[str, ...],
    draw: st.DrawFn,
) -> BoundaryRequest:
    """Draw what the run was asked, with the child's tip fixed."""
    return BoundaryRequest(
        branch="child",
        child_tip=child_tip,
        target=draw(st.sampled_from(commits)),
        parent=draw(st.one_of(st.none(), st.just(PR_IDENTITY))),
        deep=draw(st.booleans()),
        offline=draw(st.booleans()),
    )


def _facts(
    commits: tuple[str, ...],
    contents: typ.Mapping[str, CommitRange],
    placed: tuple[str, ...],
    draw: st.DrawFn,
) -> GraphFacts:
    """Draw the graph answers an example is judged against."""
    return GraphFacts(
        parent_head=draw(st.one_of(st.none(), st.sampled_from(commits))),
        landed=draw(st.one_of(st.none(), st.sampled_from(commits))),
        ancestry=_ancestry(commits, draw),
        range_contents=contents,
        range_minus_parent=_without_some(contents, draw),
        child_history=CommitRange(commits),
        cumulative_patch={commit: draw(_PATCH_IDENTIFIERS) for commit in placed},
        landed_patch=draw(st.one_of(st.none(), st.just(LANDED_PATCH))),
        record_recorded_from=draw(st.one_of(st.none(), st.sampled_from(commits))),
    )


def _parent(*, resolved: bool, draw: st.DrawFn) -> ParentPullRequest | None:
    """Draw a parent pull request, or nothing when none was read."""
    if not resolved:
        return None
    return parent_pull_request(
        merged=draw(st.booleans()),
        merged_at=draw(_MERGED_AT),
        head_fetched_from=draw(_FETCHED_FROM),
    )


@st.composite
def _cases(draw: st.DrawFn) -> _Case:
    """Draw a child history, some boundary candidates, and the answers.

    Parameters
    ----------
    draw : hypothesis.strategies.DrawFn
        Hypothesis draw function.

    Returns
    -------
    _Case
        The run's question, the candidates, the answers, and the parent.

    """
    commits = tuple(_object_id(one) for one in draw(_OBJECT_IDS))
    placed = tuple(draw(st.lists(st.sampled_from(commits), max_size=_CANDIDATE_MAX)))
    candidates = tuple(
        candidate_for(
            commit, draw(st.sampled_from(EvidenceKind)), source=draw(_SOURCES)
        )
        for commit in placed
    )
    contents = {
        range_key(commit, commits[0]): CommitRange(_replayed(commits, commit))
        for commit in dict.fromkeys(placed)
    }
    return _Case(
        request=_request(commits[0], commits, draw),
        candidates=candidates,
        facts=_facts(commits, contents, placed, draw),
        parent=_parent(resolved=draw(st.booleans()), draw=draw),
    )


def _applicable(assessment: Assessment) -> tuple[GateResult, ...]:
    """Return the gates of ``assessment`` that applied to a candidate."""
    return tuple(gate for gate in assessment.gates if gate.applicable)


def _by_candidate(gates: tuple[GateResult, ...]) -> tuple[tuple[GateResult, ...], ...]:
    """Return the gates of an assessment grouped by the candidate they judged."""
    return tuple(
        gates[start : start + _GATES_PER_CANDIDATE]
        for start in range(0, len(gates), _GATES_PER_CANDIDATE)
    )


def _unanswered(assessment: Unresolved) -> list[str]:
    """Return the unanswered gates of the candidates that could have established.

    A candidate whose support could never establish a boundary is left out, and
    so are the gates about a subject the run never set out to use: neither is a
    question the verdict rested on, so neither keeps a refusal from being a
    claim that the evidence is complete.

    Returns
    -------
    list[str]
        The names of the gates that went unanswered and could have kept the
        boundary from being refused.

    """
    paired = zip(assessment.candidates, _by_candidate(assessment.gates), strict=True)
    return [
        gate.name.value
        for candidate, judged in paired
        if not isinstance(candidate, InferredCandidate)
        for gate in judged
        if gate.applicable and gate.outcome is GateOutcome.INDETERMINATE
    ]


@given(_cases())
def test_only_evidence_that_may_establish_ever_does(case: _Case) -> None:
    """INV-2 and INV-2b: the support a boundary reports could carry it."""
    assessment = assessment_of(case)

    if not isinstance(assessment, Established):
        return
    support = assessment.support
    assert not any(isinstance(one, InferredCandidate) for one in support), (
        "no content comparison can carry an answer, whatever corroborates it"
    )
    assert any(isinstance(one, AttestedCandidate) for one in support) or (
        len({one.source for one in support}) >= REQUIRED_SOURCES
    ), (
        f"a boundary established on {support!r} rests on either one deliberate "
        "statement or two independent derived sources"
    )


@given(_cases())
def test_the_verdict_does_not_depend_on_the_order_of_the_evidence(case: _Case) -> None:
    """INV-3: the same evidence in any order is the same verdict."""
    reordered = _Case(
        request=case.request,
        candidates=tuple(reversed(case.candidates)),
        facts=case.facts,
        parent=case.parent,
    )

    assert assessment_of(reordered) == assessment_of(case), (
        "sorting the same evidence differently cannot change the verdict, the "
        "gates it reports, or the reasons it gives"
    )


@given(_cases())
def test_an_established_boundary_passed_every_gate_it_applied(case: _Case) -> None:
    """INV-4: the gates are a conjunction, and the verdict is what it says."""
    assessment = assessment_of(case)

    assert EXIT_CODES[type(assessment)] in set(EXIT_CODES.values()), (
        "every verdict has an exit status of its own"
    )
    if not isinstance(assessment, Established):
        return
    refused = [
        gate.detail
        for gate in _applicable(assessment)
        if gate.outcome is not GateOutcome.PASSED
    ]
    assert refused == [], (
        f"the boundary was established while these gates said otherwise: {refused}"
    )
    assert assessment.old_base in {one.commit for one in assessment.support}, (
        "the boundary is one of the commits its support names"
    )


@given(_cases())
def test_a_run_that_could_not_tell_names_what_it_could_not_tell(case: _Case) -> None:
    """INV-5: "could not tell" comes from an unanswered question, not a refusal."""
    assessment = assessment_of(case)

    match assessment:
        case Indeterminate():
            assert any(
                gate.outcome is GateOutcome.INDETERMINATE
                for gate in _applicable(assessment)
            ), "a run reports that it could not tell only if a gate went unanswered"
            assert assessment.reasons, "and it says which question it could not answer"
        case Unresolved():
            unanswered = _unanswered(assessment)
            assert unanswered == [], (
                f"a refusal claims the evidence is complete, yet these went "
                f"unanswered: {unanswered}. The candidate each belongs to cannot be "
                "read as refused, so the run should have reported that it could not "
                "tell"
            )


@given(_cases())
def test_an_established_boundary_partitions_the_childs_history(case: _Case) -> None:
    """INV-6: the boundary cuts the child's history in two, losing nothing."""
    assessment = assessment_of(case)

    if not isinstance(assessment, Established):
        return
    listed = case.facts.range_contents[
        range_key(assessment.old_base, case.request.child_tip)
    ]
    included = set(assessment.included)
    excluded = set(assessment.excluded)
    assert included & excluded == set(), "a commit cannot be both included and excluded"
    assert included | excluded == set(case.facts.child_history.commits), (
        "the two halves are the whole history the run read, with nothing lost "
        "between them"
    )
    assert assessment.included == listed.commits, (
        "the included commits are the replay range the run listed"
    )
    assert assessment.included_truncated == listed.truncated, (
        "and the range's completeness is reported with it"
    )
    assert assessment.excluded_truncated == case.facts.child_history.truncated, (
        "the excluded side carries the completeness of the history it came from"
    )


@given(_cases())
def test_inferred_evidence_never_changes_the_verdict(case: _Case) -> None:
    """A deeper search may find more, and must not change what the run says.

    ``--deep`` compares trees and cumulative patches, which is worth reporting
    and worth nothing more: whatever it turns up, the verdict is the one the
    evidence that can establish a boundary already supported.
    """
    deeper = _Case(
        request=case.request,
        candidates=(*case.candidates, inferred(OLD_BASE)),
        facts=case.facts,
        parent=case.parent,
    )
    shallow = assessment_of(case)
    deep = assessment_of(deeper)

    assert type(deep) is type(shallow), (
        "inferred evidence can neither carry a boundary nor refuse one"
    )
    if isinstance(shallow, Established) or isinstance(deep, Established):
        assert isinstance(shallow, Established), (
            "the two runs reached the same verdict, so one boundary means two"
        )
        assert isinstance(deep, Established), "and the deeper run reached it too"
        assert deep.old_base == shallow.old_base, (
            "a deeper search reports the same boundary or reports nothing; it "
            "cannot choose a different one"
        )
        assert deep.support == shallow.support, (
            "and the same evidence carries it: what the deeper search turned up "
            "is reported beside the boundary, never among its support"
        )
        return
    assert len(deep.candidates) == len(shallow.candidates) + 1, (
        "the deeper candidate is reported, whatever the verdict"
    )
