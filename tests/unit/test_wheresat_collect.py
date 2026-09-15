"""The rungs that answer for the pull request a run identified as the parent.

Two of them report what the parent pull request says rather than what the
repository holds. The shared record is the claim the child's own author pasted
into its body — a parent and a boundary named by the same deliberate act that
writes a stack record — and the ladder reads that claim once and hands the
reading in. So this rung asks no forge, and it is silent for a body nobody read
and for the two readings the ladder refused, which are reported where the
ladder found them.

The head a run fetched is the one piece of evidence the ladder takes from
outside the repository, and it is the rung the case this command exists for is
answered by: a parent squash-merged into the trunk by a pull request nobody
recorded a stack record for. It answers only for a head the run fetched itself.
A head the ladder recovered from a tombstone or a remote-tracking ref arrives
with the ref it was read from, and the rungs that answer for a ref report it
under their own kinds; proposing it here as well would report one repository
fact under two kinds and let the corroboration count read one witness as two.
So the same commit is offered when the run fetched it and withheld when the
ladder recovered it, which is the pair the two head tests below differ by and
nothing else.

A label is checked because a report prints it beside the kind: an operator
reading "shared-record" or "pull-request-head" needs the pull request named, or
the run has said which *sort* of fact the boundary rests on without saying which
one.

Usage
-----
Run this module directly with pytest::

    python -m pytest -k test_wheresat_collect -q
"""

from __future__ import annotations

import typing as typ

import pytest

from git_donkey import (
    observability,
    stack_store,
    wheresat_collect,
    wheresat_graph,
    wheresat_shared_record,
)
from git_donkey.wheresat_heads import ParentHead
from git_donkey.wheresat_records import (
    AttestedCandidate,
    BoundaryRequest,
    Candidate,
    EvidenceKind,
    EvidenceTier,
)
from tests import git_repo_helpers
from tests.unit.wheresat_helpers import (
    DEFAULT_WINDOW,
    PR_IDENTITY,
    PR_REPOSITORY,
    parent_pull_request,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    from git import Repo

    from git_donkey.wheresat_records import ParentPullRequest
    from tests.observability_helpers import RecordingRecorder

_COLLECTION: typ.Final[observability.Operation] = "evidence_collection"
"""Operation every rung's observation is recorded under."""

_ATTESTED: typ.Final = EvidenceTier.ATTESTED.value
"""Tier label the three rungs that read a deliberately stated fact share."""

_SOURCE: typ.Final = f"the head of {PR_REPOSITORY}#{PR_IDENTITY.number}"
"""Label the fetched head is proposed under, as a report prints it."""

_SHARED_SOURCE: typ.Final = (
    f"the shared record in the body of {PR_REPOSITORY}#{PR_IDENTITY.number}"
)
"""Label a claim from the child's own body is proposed under."""

_BOUNDARY: typ.Final = "c" * 40
"""Boundary the claimed record names, written as a full object ID."""

_OTHER_BOUNDARY: typ.Final = "e" * 40
"""Second boundary, so a body can be read as two disagreeing records."""

_NO_BODY: typ.Final = wheresat_shared_record.SharedRecordAbsent()
"""The claim of a context whose run read no body, or read one claiming nothing."""

_REFUSED: typ.Final[tuple[wheresat_shared_record.SharedRecordResult, ...]] = (
    wheresat_shared_record.SharedRecordMalformed(reason="the parent did not read"),
    wheresat_shared_record.SharedRecordAmbiguous(
        records=(
            wheresat_shared_record.SharedRecord(parent=PR_IDENTITY, boundary=_BOUNDARY),
            wheresat_shared_record.SharedRecord(
                parent=PR_IDENTITY, boundary=_OTHER_BOUNDARY
            ),
        )
    ),
)
"""The two readings of a body the ladder refuses rather than resolves."""

_CHILD: typ.Final = "child"
"""Branch the run is about, which the repository never carries."""


def _head(repo: Repo, *, ref: str | None = None) -> ParentHead:
    """Return the parent's tip as the run has it, read from ``ref`` or fetched.

    Parameters
    ----------
    repo : Repo
        Repository whose tip names the head, which is all this module needs of
        it: the rung under test reads the context and not the repository.
    ref : str | None, optional
        Ref the head was read from, for the case that must arrive with one.

    Returns
    -------
    ParentHead
        The parent's head, in hand either way and read differently.

    """
    return ParentHead(repo.head.commit.hexsha, ref)


def _context(
    repo: Repo,
    *,
    parent: ParentPullRequest | None,
    head: ParentHead | None,
    shared_record: wheresat_shared_record.SharedRecordResult = _NO_BODY,
) -> wheresat_collect.CollectionContext:
    """Return the context a run of this shape would hand its rungs.

    The child and the target are the repository's own tip, so the rungs beside
    the one under test answer rather than fault: what they find is not this
    module's subject, and a fault raised here would be noise in the report the
    assertions read.

    Parameters
    ----------
    repo : Repo
        Repository the run is about.
    parent : ParentPullRequest | None
        The parent pull request the run identified, when it identified one.
    head : ParentHead | None
        The parent's head, when the run has one.
    shared_record : wheresat_shared_record.SharedRecordResult, optional
        What the ladder read in the child's own body, which most runs never
        read: an absent reading is what a run with no body to read hands in.

    Returns
    -------
    CollectionContext
        The question, the ports, and the three answers about the parent.

    """
    tip = repo.head.commit.hexsha
    return wheresat_collect.CollectionContext(
        request=BoundaryRequest(
            branch=_CHILD,
            child_tip=tip,
            target=tip,
            parent=parent.identity if parent is not None else None,
            deep=False,
            heuristic_window=DEFAULT_WINDOW,
            offline=False,
        ),
        target_ref=None,
        parent=parent,
        graph=wheresat_graph.GitWheresatGraph(repo),
        records=stack_store.GitStackRecordReader(repo),
        parent_head=head,
        shared_record=shared_record,
    )


def _proposed(collected: wheresat_collect.CollectedEvidence) -> tuple[Candidate, ...]:
    """Return the candidates the run offers as the parent pull request's head."""
    return tuple(
        candidate
        for candidate in collected.candidates
        if candidate.kind is EvidenceKind.PULL_REQUEST_HEAD
    )


def _claimed(collected: wheresat_collect.CollectedEvidence) -> tuple[Candidate, ...]:
    """Return the candidates the run offers from the child's own body."""
    return tuple(
        candidate
        for candidate in collected.candidates
        if candidate.kind is EvidenceKind.SHARED_RECORD
    )


def _attested(recorder: RecordingRecorder) -> list[observability.Outcome]:
    """Return what each rung that read attested evidence answered, in order."""
    return [
        observation.outcome
        for observation in recorder.observations
        if observation.operation == _COLLECTION
        and observation.evidence_tier == _ATTESTED
    ]


def test_a_fetched_head_is_proposed_as_the_pull_requests_own_evidence(
    tmp_path: Path, recording_recorder: RecordingRecorder
) -> None:
    """A head the run fetched carries a boundary on its own, under its own kind.

    The candidate is attested rather than derived, which is the whole of why
    this rung exists: one attested candidate establishes a boundary where two
    derived ones would be needed, so a parent merged by a pull request nobody
    recorded a stack record for can still be answered for. The repository has
    no record at all and the run read no body, so the two rungs above find
    nothing and the head rung is the only attested rung that answered — and it
    is asked third, which is the precedence the ladder fixes.
    """
    repo = git_repo_helpers.seed_repo(tmp_path / "repo")
    head = _head(repo)
    context = _context(repo, parent=parent_pull_request(), head=head)

    proposed = _proposed(wheresat_collect.collect_evidence(context))

    assert len(proposed) == 1, "a fetched head is one candidate, not none and not two"
    candidate = proposed[0]
    assert isinstance(candidate, AttestedCandidate), (
        "the pull request names the commit deliberately, so it is attested"
    )
    assert candidate.commit == head.commit, "the candidate names the commit fetched"
    assert candidate.kind is EvidenceKind.PULL_REQUEST_HEAD, (
        "the candidate is reported under the kind of the rung that read it"
    )
    assert candidate.source == _SOURCE, (
        f"the candidate should name the pull request; it says {candidate.source!r}"
    )
    assert _attested(recording_recorder) == ["empty", "empty", "success"], (
        "the two rungs above found nothing and the head rung answered, in that order"
    )


def test_a_head_the_ladder_recovered_is_left_to_the_rungs_that_read_refs(
    tmp_path: Path, recording_recorder: RecordingRecorder
) -> None:
    """The same commit is withheld when a ref, rather than the run, named it.

    A head read from a tombstone or a remote-tracking ref is one repository
    fact, and the fork-point rung answers for it under a derived kind. Offering
    it here as well would enter one fact twice in a corroboration count that
    reads two derived sources as support. The head is still reported — under
    the kinds of the questions that read it — which is what tells this apart
    from a run that lost it.
    """
    repo = git_repo_helpers.seed_repo(tmp_path / "repo")
    branch = parent_pull_request().head_ref
    repo.create_head(branch, repo.head.commit)
    head = _head(repo, ref=f"refs/heads/{branch}")
    context = _context(repo, parent=parent_pull_request(), head=head)

    collected = wheresat_collect.collect_evidence(context)

    assert not _proposed(collected), "a head read from a ref is not this rung's offer"
    assert _attested(recording_recorder) == ["empty", "empty", "empty"], (
        "the head rung answered with nothing rather than going unanswered"
    )
    assert any(candidate.commit == head.commit for candidate in collected.candidates), (
        "the head should still be reported by the rungs that read the ref"
    )


def test_a_run_that_fetched_no_head_proposes_nothing(
    tmp_path: Path, recording_recorder: RecordingRecorder
) -> None:
    """A parent the run could not fetch a head for has no commit to propose.

    This is what ``--no-fetch``, an unreachable forge, and a head no ref at the
    remote holds all leave behind: the pull request is identified and named, and
    the commit it names is not in hand.
    """
    repo = git_repo_helpers.seed_repo(tmp_path / "repo")
    context = _context(repo, parent=parent_pull_request(), head=None)

    proposed = _proposed(wheresat_collect.collect_evidence(context))

    assert not proposed, "a run with no head has nothing to propose"
    assert _attested(recording_recorder) == ["empty", "empty", "empty"], (
        "the head rung answered with nothing rather than going unanswered"
    )


def test_a_head_with_no_parent_to_attribute_it_to_is_not_proposed(
    tmp_path: Path,
) -> None:
    """A head nothing attributes to a pull request cannot be labelled or attested.

    The pipeline does not build this pair — a run identifies no parent fetches
    no head — so it is asserted here rather than through a run: the guard is
    what keeps the rung from reading an identity off no parent at all, and a
    candidate whose source named nothing would be a report saying which sort of
    fact the boundary rests on and not which one.
    """
    repo = git_repo_helpers.seed_repo(tmp_path / "repo")
    context = _context(repo, parent=None, head=_head(repo))

    proposed = _proposed(wheresat_collect.collect_evidence(context))

    assert not proposed, "a head with no pull request reports nothing"


def test_a_body_the_child_wrote_is_proposed_as_its_own_evidence(
    tmp_path: Path, recording_recorder: RecordingRecorder
) -> None:
    """A boundary the child's author claimed in its body is attested evidence.

    The claim is the deliberate act a record's writer performs, written where a
    person can read it, so a boundary it names needs no corroboration: one
    attested candidate establishes it, and a child whose record never travelled
    with the clone can still be answered for. The repository carries no stack
    record, so this rung answers after the record rung found nothing and before
    the head rung, which is the precedence the ladder fixes — what the child
    says about its own stack is read ahead of what the parent's pull request
    names.
    """
    repo = git_repo_helpers.seed_repo(tmp_path / "repo")
    context = _context(
        repo,
        parent=parent_pull_request(),
        head=_head(repo),
        shared_record=wheresat_shared_record.SharedRecord(
            parent=PR_IDENTITY, boundary=_BOUNDARY
        ),
    )

    claimed = _claimed(wheresat_collect.collect_evidence(context))

    assert len(claimed) == 1, "a claim is one candidate, not none and not two"
    candidate = claimed[0]
    assert isinstance(candidate, AttestedCandidate), (
        "the body names the boundary deliberately, so it is attested"
    )
    assert candidate.commit == _BOUNDARY, "the candidate names the claimed boundary"
    assert candidate.source == _SHARED_SOURCE, (
        f"the candidate should name the body's PR; it says {candidate.source!r}"
    )
    assert _attested(recording_recorder) == ["empty", "success", "success"], (
        "the shared record answered, and it answered before the head rung did"
    )


def test_a_run_that_read_no_body_leaves_the_shared_rung_silent(
    tmp_path: Path, recording_recorder: RecordingRecorder
) -> None:
    """A rung with no reading to hand in answers nothing, and faults nothing.

    Most runs hand in no reading at all: ``--parent`` names the parent outright,
    and ``--offline`` may not ask the forge and so reads no body, both decided
    before the ladder walks a rung. A run that faulted for a question it never
    put would fail for a reason no operator could act on, and one that stayed
    out of the rung list would hide that the rung was asked and had nothing.
    """
    repo = git_repo_helpers.seed_repo(tmp_path / "repo")
    context = _context(repo, parent=parent_pull_request(), head=_head(repo))

    collected = wheresat_collect.collect_evidence(context)

    assert not _claimed(collected), "a run that read no body claims no boundary"
    assert not collected.faults, "a question the run never put is not a fault"
    assert _attested(recording_recorder) == ["empty", "empty", "success"], (
        "the rung was asked and answered nothing, rather than going unasked"
    )


@pytest.mark.parametrize("refusal", _REFUSED)
def test_a_claim_the_ladder_refused_is_not_reported_twice(
    tmp_path: Path, refusal: wheresat_shared_record.SharedRecordResult
) -> None:
    """A body the ladder could not act on is silent here, not faulted again.

    The ladder reads the body while it walks the association search, and a
    malformed or ambiguous claim stops it there with the reason it found. This
    rung is handed the same reading, so a fault of its own would report one
    unusable claim under two operations and send an operator looking for a
    second fault that does not exist.
    """
    repo = git_repo_helpers.seed_repo(tmp_path / "repo")
    quiet = _context(repo, parent=parent_pull_request(), head=None)
    refused = _context(
        repo, parent=parent_pull_request(), head=None, shared_record=refusal
    )

    collected = wheresat_collect.collect_evidence(refused)

    assert not _claimed(collected), "a refused claim names no boundary to propose"
    assert collected.faults == wheresat_collect.collect_evidence(quiet).faults, (
        "the refusal is reported where the ladder found it, not a second time here"
    )
