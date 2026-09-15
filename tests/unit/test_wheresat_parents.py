"""The parent ladder: which rung answers, and what an unanswered question is.

Two claims are pinned here, and they are the two the ladder exists to keep
apart. The first is the rung order: a parent the user named is consulted and no
weaker question is asked, the child's own pull request is walked past rather
than reported, and a stack GitHub records answers before the association search
continues. The second is what a question that went unanswered *is* — a fault,
which the run reports as an indeterminate result, and never a quieter walk to
the next rung (ADR-005, INV-5). The credential case is the one that matters
most, because the class it raises is a usage failure everywhere else and the
ladder is the only caller for which the run's result is the evidence's rather
than the environment's.

Two things are deliberately *not* asked of a forge: a checkout that names no
GitHub repository has no question to put, and a run told ``--offline`` may not
put one. Both leave the search skipped, because a question the run never asked
is not a question the forge failed to answer.

Nothing here opens a repository or a socket. The history and the forge are
doubles, so every rung is driven without a network and the assertions can be
about what was asked as much as about what was answered.
"""

from __future__ import annotations

import dataclasses
import typing as typ

import pytest

from git_donkey import observability, stack_records, wheresat_github
from git_donkey import wheresat_parents as parents
from git_donkey.wheresat_errors import (
    ShallowHistoryError,
    WheresatCredentialError,
    WheresatGitHubError,
)
from git_donkey.wheresat_records import BoundaryRequest, ParentPullRequest
from tests.unit.wheresat_helpers import (
    CHILD_BELOW,
    CHILD_TIP,
    DEFAULT_WINDOW,
    PARENT_HEAD,
    PR_IDENTITY,
    PR_REPOSITORY,
    TARGET,
    parent_pull_request,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from git_donkey import wheresat_graph
    from tests.observability_helpers import RecordingRecorder

_BRANCH: typ.Final = "child"
"""The child branch the walk tells apart from the parent's."""

_REPOSITORY: typ.Final = "acme/widget"
"""``OWNER/REPOSITORY`` the association search is put to."""

_CHILD_IDENTITY: typ.Final = stack_records.PullRequestIdentity(
    repository=_REPOSITORY, number=17
)
"""The child's own pull request, as the association search reports it."""

_PARENT_IDENTITY: typ.Final = stack_records.PullRequestIdentity(
    repository=_REPOSITORY, number=16
)
"""The parent pull request the search or a stack may lead to."""

_DECOY_IDENTITY: typ.Final = stack_records.PullRequestIdentity(
    repository=_REPOSITORY, number=15
)
"""A pull request on an older commit, which a stronger rung should pre-empt."""

_SECOND_CHILD_IDENTITY: typ.Final = stack_records.PullRequestIdentity(
    repository=_REPOSITORY, number=18
)
"""A second pull request the child branch heads, on an older commit."""

_PARENT_IDENTIFICATION: typ.Final[observability.Operation] = "parent_identification"
"""Operation every question about the parent is recorded under."""


@dataclasses.dataclass(frozen=True, slots=True)
class _History:
    """A graph that answers history questions, and records what it was asked.

    Only ``history`` is implemented, because it is the only graph question the
    ladder puts: the double is cast to the port rather than completed, so a
    ladder that reached for another question would fail against it.

    Attributes
    ----------
    commits : tuple[str, ...]
        Commits the history holds, oldest first, as the real reader returns
        them.
    refusal : Exception | None
        Failure the read raises instead of answering.
    limits : list[int | None]
        The ``limit`` of every read, in the order the reads were made.

    """

    commits: tuple[str, ...] = ()
    refusal: Exception | None = None
    limits: list[int | None] = dataclasses.field(default_factory=list)

    def history(self, rev: str, *, limit: int | None = None) -> tuple[str, ...]:
        """Return the newest commits of the history this double holds."""
        self.limits.append(limit)
        if self.refusal is not None:
            raise self.refusal
        return self.commits if limit is None else self.commits[:limit]


@dataclasses.dataclass(frozen=True, slots=True)
class _Forge(wheresat_github.WheresatGitHub):
    """A forge whose every answer a test supplies, asked in a recorded order.

    A question the test did not provide for is a defect in the test rather than
    an answer of nothing: which questions the ladder puts is half of what these
    tests assert, so an unprovided one is raised rather than read as a
    negative.

    Attributes
    ----------
    payloads : cabc.Mapping[int, ParentPullRequest]
        What ``pull_request`` answers, keyed by pull request number.
    stacks : cabc.Mapping[int, stack_records.PullRequestIdentity | None]
        What ``stack_parent`` answers, keyed by pull request number.
    page : wheresat_github.AssociationPage
        What the association search answers.
    read : list[int]
        Every pull request read, in the order it was asked about.

    """

    payloads: cabc.Mapping[int, ParentPullRequest] = dataclasses.field(
        default_factory=dict
    )
    stacks: cabc.Mapping[int, stack_records.PullRequestIdentity | None] = (
        dataclasses.field(default_factory=dict)
    )
    page: wheresat_github.AssociationPage = dataclasses.field(
        default_factory=lambda: wheresat_github.AssociationPage(
            associations={}, commits_examined=0, truncated=False
        )
    )
    read: list[int] = dataclasses.field(default_factory=list)

    @typ.override
    def pull_request(
        self, identity: stack_records.PullRequestIdentity
    ) -> ParentPullRequest:
        """Return the payload this test supplied for ``identity``."""
        self.read.append(identity.number)
        try:
            return self.payloads[identity.number]
        except KeyError:
            msg = f"the ladder read pull request {identity.number}, unprovided"
            raise NotImplementedError(msg) from None

    @typ.override
    def pull_request_body(self, identity: stack_records.PullRequestIdentity) -> str:
        """Refuse: the ladder reads bodies nowhere."""
        msg = "the ladder asked for a pull request body"
        raise NotImplementedError(msg)

    @typ.override
    def stack_parent(
        self, identity: stack_records.PullRequestIdentity
    ) -> stack_records.PullRequestIdentity | None:
        """Return the pull request this test put below ``identity``, if any."""
        try:
            return self.stacks[identity.number]
        except KeyError:
            msg = f"the ladder asked about the stack of {identity.number}"
            raise NotImplementedError(msg) from None

    @typ.override
    def associated_pull_requests(
        self, repository: str, commits: cabc.Sequence[str]
    ) -> wheresat_github.AssociationPage:
        """Return the association page this test supplied."""
        assert repository == _REPOSITORY, (
            "the search should be put to the repository the run names"
        )
        return self.page


@dataclasses.dataclass(frozen=True, slots=True)
class _Opener:
    """What opens the forge, counting how many times the ladder asked it to.

    Attributes
    ----------
    forge : wheresat_github.WheresatGitHub | None
        Port to hand back, or ``None`` for a caller that has none either.
    refusal : Exception | None
        Failure the open raises instead of returning a port.
    opened : list[int]
        One entry per call, so a test can assert the forge was never opened.

    """

    forge: wheresat_github.WheresatGitHub | None = None
    refusal: Exception | None = None
    opened: list[int] = dataclasses.field(default_factory=list)

    def __call__(self) -> wheresat_github.WheresatGitHub:
        """Return the port, or raise the refusal this opener was built with."""
        self.opened.append(1)
        if self.refusal is not None:
            raise self.refusal
        if self.forge is None:
            msg = "this opener has no forge to offer"
            raise NotImplementedError(msg)
        return self.forge


def _request(
    *,
    parent: stack_records.PullRequestIdentity | None = None,
    offline: bool = False,
) -> BoundaryRequest:
    """Return the run's question, naming a parent and an offline flag."""
    return BoundaryRequest(
        branch=_BRANCH,
        child_tip=CHILD_TIP,
        target=TARGET,
        parent=parent,
        deep=False,
        heuristic_window=DEFAULT_WINDOW,
        offline=offline,
    )


def _bounds(
    *, limit: int = 20, repository: str | None = _REPOSITORY
) -> parents.SearchBounds:
    """Return how far the search may walk, and in which repository."""
    return parents.SearchBounds(limit=limit, repository=repository)


def _history(*commits: str) -> _History:
    """Return a graph answering with ``commits``, oldest first."""
    return _History(commits=commits)


def _ask(
    request: BoundaryRequest,
    bounds: parents.SearchBounds,
    *,
    history: _History | None = None,
    opener: _Opener | None = None,
) -> parents.ParentIdentification:
    """Put the run's parent question to the ladder."""
    return parents.identify_parent(
        request,
        bounds,
        graph=typ.cast(
            "wheresat_graph.WheresatGraph",
            _History() if history is None else history,
        ),
        opener=opener,
    )


def _child_payload(*, stacked: bool = False) -> ParentPullRequest:
    """Return the child's own pull request, as GitHub would report it."""
    return parent_pull_request(
        identity=_CHILD_IDENTITY,
        head_ref=_BRANCH,
        head_repository=PR_REPOSITORY,
        stacked=stacked,
    )


def _parent_payload() -> ParentPullRequest:
    """Return the parent pull request, as GitHub would report it."""
    return parent_pull_request(identity=_PARENT_IDENTITY, head_sha=PARENT_HEAD)


def _page(
    associations: cabc.Mapping[str, tuple[stack_records.PullRequestIdentity, ...]],
    *,
    truncated: bool = False,
) -> wheresat_github.AssociationPage:
    """Return the association page a search would answer with."""
    return wheresat_github.AssociationPage(
        associations=associations,
        commits_examined=len(associations),
        truncated=truncated,
    )


def test_a_named_parent_is_read_from_the_forge(
    recording_recorder: RecordingRecorder,
) -> None:
    """A parent the user named is the whole of the ladder's work."""
    forge = _Forge(payloads={PR_IDENTITY.number: parent_pull_request()})
    opener = _Opener(forge=forge)

    identified = _ask(
        _request(parent=PR_IDENTITY), _bounds(repository=None), opener=opener
    )

    assert identified.parent is not None, "the named parent should have been read"
    assert identified.parent.identity == PR_IDENTITY, (
        "the run should answer about the pull request it was told to consult"
    )
    assert identified.faults == (), "a parent the forge named is no fault"
    assert forge.read == [PR_IDENTITY.number], (
        "a named parent should be the only pull request read"
    )
    assert recording_recorder.outcomes(_PARENT_IDENTIFICATION) == ["found"], (
        "the identification should be recorded as found"
    )


def test_a_named_parent_is_skipped_when_no_opener_is_offered(
    recording_recorder: RecordingRecorder,
) -> None:
    """A caller with no forge to offer leaves every question unasked."""
    identified = _ask(_request(parent=PR_IDENTITY), _bounds(), opener=None)

    assert identified.parent is None, "no forge means no parent"
    assert identified.faults == (), "asking nothing is not a question gone unanswered"
    assert recording_recorder.outcomes(_PARENT_IDENTIFICATION) == ["skipped"], (
        "the skip should be recorded rather than passed over in silence"
    )


def test_a_credential_that_cannot_be_opened_is_a_fault(
    recording_recorder: RecordingRecorder,
) -> None:
    """A missing credential is an unanswered question, not a refusal to start.

    The run's result is the evidence's, so the ladder reports the refusal as a
    fault rather than letting the class reach the usage handler: the exit
    status is Table 3's row for a credential that is not there, which is ``3``,
    and the reason the message carries is the one naming every source that was
    tried.
    """
    opener = _Opener(refusal=WheresatCredentialError("no GitHub credential: set …"))

    identified = _ask(_request(parent=PR_IDENTITY), _bounds(), opener=opener)

    assert identified.parent is None, "a credential that is not there names no parent"
    assert len(identified.faults) == 1, "the refusal should be reported once"
    assert "the forge could not be opened" in identified.faults[0], (
        "the fault should say which step failed"
    )
    assert "no GitHub credential" in identified.faults[0], (
        "the fault should carry the refusal's own account of what was missing"
    )
    assert identified.error_kind == "credential_unavailable", (
        "the run should report the credential as the bounded class of the fault"
    )
    assert recording_recorder.error_kinds(_PARENT_IDENTIFICATION) == [
        "credential_unavailable"
    ], "the observation should carry the same class the identification reports"


def test_a_forge_that_will_not_answer_is_a_fault(
    recording_recorder: RecordingRecorder,
) -> None:
    """A forge that fails to open stops the ladder rather than the run."""
    opener = _Opener(refusal=WheresatGitHubError("GitHub did not answer"))

    identified = _ask(_request(parent=PR_IDENTITY), _bounds(), opener=opener)

    assert identified.parent is None, "a fault is never read as a parent"
    assert identified.error_kind == "github_api_error", (
        "a transport failure is the GitHub class of fault"
    )
    assert recording_recorder.outcomes(_PARENT_IDENTIFICATION) == ["unavailable"], (
        "an unanswered question should be recorded as unavailable"
    )


def test_a_checkout_that_names_no_repository_asks_nothing(
    recording_recorder: RecordingRecorder,
) -> None:
    """No GitHub repository to ask about is no question to put."""
    opener = _Opener(forge=_Forge())

    identified = _ask(_request(), _bounds(repository=None), opener=opener)

    assert identified.parent is None, "a checkout with no repository names no parent"
    assert identified.faults == (), "having nothing to ask is not a failure to answer"
    assert not opener.opened, (
        "a run with no question should not open the forge to ask it"
    )


def test_an_offline_run_asks_nothing_of_an_offered_forge(
    recording_recorder: RecordingRecorder,
) -> None:
    """``--offline`` is a property of the run, not a promise the ladder keeps."""
    opener = _Opener(forge=_Forge())

    identified = _ask(_request(offline=True), _bounds(), opener=opener)

    assert identified.parent is None, "an offline run names no parent from the forge"
    assert identified.faults == (), "declining to ask is not a question unanswered"
    assert not opener.opened, "an offline run should not open the forge at all"
    assert recording_recorder.outcomes(_PARENT_IDENTIFICATION) == ["skipped"], (
        "the skip should be recorded as such"
    )


def test_a_history_longer_than_the_window_refuses_the_search(
    recording_recorder: RecordingRecorder,
) -> None:
    """A window that stopped short cannot say that nothing names a boundary.

    The refusal names the bound and the way out of it, because the answer the
    search would otherwise give is a negative one — "no pull request is
    associated with these commits" — that a partial history cannot support.
    """
    limit = 3
    opener = _Opener(forge=_Forge())
    history = _history(*("c" * 40 for _ in range(limit + 1)))

    identified = _ask(_request(), _bounds(limit=limit), history=history, opener=opener)

    assert identified.parent is None, "a truncated window names no parent"
    assert len(identified.faults) == 1, "the refusal should be reported once"
    assert f"more commits than the {limit}" in identified.faults[0], (
        "the refusal should name the bound the history exceeded"
    )
    assert "--parent" in identified.faults[0], (
        "the refusal should name the way out of the search"
    )
    assert history.limits == [limit + 1], (
        "one commit beyond the bound is asked for, so a full window is recognisable"
    )
    assert identified.error_kind == "search_incomplete", (
        "the bound the run set is what stopped the search, not the forge"
    )


def test_a_shallow_history_is_reported_as_a_shallow_history(
    recording_recorder: RecordingRecorder,
) -> None:
    """A graft that stopped the read is labelled for the operator."""
    refusal = ShallowHistoryError("cannot trust the history: the clone is shallow")
    opener = _Opener(forge=_Forge())

    identified = _ask(
        _request(), _bounds(), history=_History(refusal=refusal), opener=opener
    )

    assert identified.parent is None, "a history that could not be read names nothing"
    assert identified.error_kind == "shallow_history", (
        "the operator should be told to deepen the clone rather than to debug Git"
    )
    assert "could not be read" in identified.faults[0], (
        "the fault should say the history was the question that failed"
    )


def test_the_childs_own_pull_request_is_walked_past(
    recording_recorder: RecordingRecorder,
) -> None:
    """The first association that is not the child's own is the parent.

    The walk is ordered: the newest commit is asked about first, because it is
    the commit most likely to be the child's own, and the pull request reached
    through the commit below it is the parent.
    """
    forge = _Forge(
        payloads={
            _CHILD_IDENTITY.number: _child_payload(),
            _PARENT_IDENTITY.number: _parent_payload(),
        },
        stacks={_CHILD_IDENTITY.number: None},
        page=_page({
            CHILD_TIP: (_CHILD_IDENTITY,),
            CHILD_BELOW: (_PARENT_IDENTITY,),
        }),
    )
    history = _history(CHILD_BELOW, CHILD_TIP)

    identified = _ask(
        _request(), _bounds(), history=history, opener=_Opener(forge=forge)
    )

    assert identified.parent is not None, "the commit below the child should name one"
    assert identified.parent.identity == _PARENT_IDENTITY, (
        "the parent is the association that is not the child's own pull request"
    )
    assert forge.read == [_CHILD_IDENTITY.number, _PARENT_IDENTITY.number], (
        "the child's own pull request is read before the parent's"
    )
    assert identified.faults == (), "a parent the search found is no fault"


def test_a_native_stack_names_the_parent_before_the_walk_continues(
    recording_recorder: RecordingRecorder,
) -> None:
    """A stack GitHub records is a statement, and outranks an inference."""
    forge = _Forge(
        payloads={
            _CHILD_IDENTITY.number: _child_payload(stacked=True),
            _PARENT_IDENTITY.number: _parent_payload(),
        },
        stacks={_CHILD_IDENTITY.number: _PARENT_IDENTITY},
        page=_page({
            CHILD_TIP: (_CHILD_IDENTITY,),
            CHILD_BELOW: (_DECOY_IDENTITY,),
        }),
    )
    history = _history(CHILD_BELOW, CHILD_TIP)

    identified = _ask(
        _request(), _bounds(), history=history, opener=_Opener(forge=forge)
    )

    assert identified.parent is not None, "the stack should name the parent"
    assert identified.parent.identity == _PARENT_IDENTITY, (
        "the pull request below the child in the stack is the parent"
    )
    assert _DECOY_IDENTITY.number not in forge.read, (
        "the association below the child should not be reached once the stack answers"
    )


def test_a_second_child_head_is_not_asked_for_a_stack(
    recording_recorder: RecordingRecorder,
) -> None:
    """A branch that heads two pull requests asks its stack question once.

    The first association whose head is the child branch is the child, and the
    stack question belongs to the child rather than to the association it was
    reached through, so a second pull request the branch heads is read and
    walked past without asking GitHub about its stack.
    """
    forge = _Forge(
        payloads={
            _CHILD_IDENTITY.number: _child_payload(),
            _SECOND_CHILD_IDENTITY.number: parent_pull_request(
                identity=_SECOND_CHILD_IDENTITY, head_ref=_BRANCH
            ),
        },
        stacks={_CHILD_IDENTITY.number: None},
        page=_page({
            CHILD_TIP: (_CHILD_IDENTITY,),
            CHILD_BELOW: (_SECOND_CHILD_IDENTITY,),
        }),
    )
    history = _history(CHILD_BELOW, CHILD_TIP)

    identified = _ask(
        _request(), _bounds(), history=history, opener=_Opener(forge=forge)
    )

    assert identified.parent is None, (
        "neither child pull request should be reported as the parent"
    )
    assert identified.faults == (), "reading two child pull requests is no fault"
    assert forge.read == [_CHILD_IDENTITY.number, _SECOND_CHILD_IDENTITY.number], (
        "both pull requests the branch heads should be read, newest association first"
    )
    assert recording_recorder.outcomes(_PARENT_IDENTIFICATION) == ["empty"], (
        "the walk should run out of associations without a stack answer"
    )


def test_a_search_that_found_nothing_is_empty_and_not_a_fault(
    recording_recorder: RecordingRecorder,
) -> None:
    """A search that saw the whole history and found none has answered."""
    forge = _Forge(page=_page({CHILD_TIP: (), CHILD_BELOW: ()}))

    identified = _ask(
        _request(),
        _bounds(),
        history=_history(CHILD_BELOW, CHILD_TIP),
        opener=_Opener(forge=forge),
    )

    assert identified.parent is None, "nothing was associated with the child"
    assert identified.faults == (), "an answer of nothing is not a fault"
    assert recording_recorder.outcomes(_PARENT_IDENTIFICATION) == ["empty"], (
        "the empty answer should be recorded as its own outcome"
    )


def test_a_search_that_stopped_short_refuses(
    recording_recorder: RecordingRecorder,
) -> None:
    """A page the adapter truncated is a refusal, not a short answer."""
    forge = _Forge(page=_page({CHILD_TIP: ()}, truncated=True))

    identified = _ask(
        _request(), _bounds(), history=_history(CHILD_TIP), opener=_Opener(forge=forge)
    )

    assert identified.parent is None, "a search that stopped short names no parent"
    assert len(identified.faults) == 1, "the refusal should be reported once"
    assert "stopped after 1" in identified.faults[0], (
        "the refusal should name how much of the history the search saw"
    )
    assert "--parent" in identified.faults[0], (
        "the refusal should name the way out of the search"
    )
    assert identified.error_kind == "search_incomplete", (
        "the adapter's budget is what stopped the search, and it is not the forge"
    )


def test_the_window_is_capped_by_the_adapter_ceiling(
    recording_recorder: RecordingRecorder,
) -> None:
    """``--limit`` may ask for fewer commits, and never for more."""
    history = _history(CHILD_TIP)

    _ask(
        _request(),
        _bounds(limit=wheresat_github.ASSOCIATION_SEARCH_LIMIT * 10),
        history=history,
        opener=_Opener(forge=_Forge(page=_page({CHILD_TIP: ()}))),
    )

    assert history.limits == [wheresat_github.ASSOCIATION_SEARCH_LIMIT + 1], (
        "the search should be bounded by the adapter's ceiling"
    )


def test_a_limit_below_one_is_read_as_one(
    recording_recorder: RecordingRecorder,
) -> None:
    """A search allowed no commits has nothing to report but that it saw none."""
    history = _history(CHILD_TIP)

    _ask(
        _request(),
        _bounds(limit=0),
        history=history,
        opener=_Opener(forge=_Forge(page=_page({CHILD_TIP: ()}))),
    )

    assert history.limits == [2], (
        "a limit below one should ask about one commit, and one beyond it"
    )


@pytest.mark.parametrize("limit", [1, 2, 20])
def test_a_history_within_the_window_is_searched(
    limit: int, recording_recorder: RecordingRecorder
) -> None:
    """A window that saw the whole history is an answer about that history."""
    commits = tuple(f"{index:040d}" for index in range(limit))
    forge = _Forge(page=_page(dict.fromkeys(commits, ())))

    identified = _ask(
        _request(),
        _bounds(limit=limit),
        history=_history(*commits),
        opener=_Opener(forge=forge),
    )

    assert identified.faults == (), "a history inside the bound is not a refusal"
