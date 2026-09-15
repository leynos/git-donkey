"""The rung that proposes the parent pull request's own head.

The head a run fetched is the one piece of evidence the ladder takes from
outside the repository, and it is the rung the case this command exists for is
answered by: a parent squash-merged into the trunk by a pull request nobody
recorded a stack record for. What that rung proposes, and the heads it declines
to propose, are what this module pins.

It answers only for a head the run fetched itself. A head the ladder recovered
from a tombstone or a remote-tracking ref arrives with the ref it was read from,
and the rungs that answer for a ref report it under their own kinds; proposing
it here as well would report one repository fact under two kinds and let the
corroboration count read one witness as two. So the same commit is offered when
the run fetched it and withheld when the ladder recovered it, which is the pair
the first two tests below differ by and nothing else.

The label is checked because a report prints it beside the kind: an operator
reading "pull-request-head" needs the pull request named, or the run has said
which *sort* of fact the boundary rests on without saying which one.

Usage
-----
Run this module directly with pytest::

    python -m pytest -k test_wheresat_collect -q
"""

from __future__ import annotations

import typing as typ

from git_donkey import observability, stack_store, wheresat_collect, wheresat_graph
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
"""Tier label the record rung and the pull request head rung share."""

_SOURCE: typ.Final = f"the head of {PR_REPOSITORY}#{PR_IDENTITY.number}"
"""Label the fetched head is proposed under, as a report prints it."""

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

    Returns
    -------
    CollectionContext
        The question, the ports, and the two answers about the parent.

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
    )


def _proposed(collected: wheresat_collect.CollectedEvidence) -> tuple[Candidate, ...]:
    """Return the candidates the run offers as the parent pull request's head."""
    return tuple(
        candidate
        for candidate in collected.candidates
        if candidate.kind is EvidenceKind.PULL_REQUEST_HEAD
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
    no record at all, so the record rung finds nothing and the head rung is the
    only attested rung that answered — and it is asked second, which is the
    precedence the ladder fixes.
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
    assert _attested(recording_recorder) == ["empty", "success"], (
        "the record rung found nothing and the head rung answered, in that order"
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
    assert _attested(recording_recorder) == ["empty", "empty"], (
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
    assert _attested(recording_recorder) == ["empty", "empty"], (
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
