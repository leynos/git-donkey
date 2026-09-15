"""Doubles and builders the ladder's suite drives its rungs with.

The ladder asks three readers — history, the clone's own record, and the forge —
and stating one rung means supplying all three as data, which is more
scaffolding than assertion. They sit here rather than beside the tests because
the suite that uses them carries more lines of code than a single file may hold;
drawn apart, both modules are within the limit.

Nothing here opens a repository or a socket. Nothing here is collected by
pytest either, so a check of its own raises rather than asserting: the suite
states what the ladder does, and this module states only what a test asked for.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from git_donkey import observability, stack_records, wheresat_github
from git_donkey import wheresat_parents as parents
from git_donkey.wheresat_records import (
    BoundaryRequest,
    EvidenceKind,
    ParentPullRequest,
)
from tests.unit.wheresat_helpers import (
    CHILD_TIP,
    DEFAULT_WINDOW,
    PARENT_HEAD,
    PR_REPOSITORY,
    TARGET,
    parent_pull_request,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from git_donkey import stack_store, wheresat_graph

CHILD_BRANCH: typ.Final = "child"
"""The child branch the walk tells apart from the parent's."""

ASSOCIATION_REPOSITORY: typ.Final = "acme/widget"
"""``OWNER/REPOSITORY`` the association search is put to."""

CHILD_IDENTITY: typ.Final = stack_records.PullRequestIdentity(
    repository=ASSOCIATION_REPOSITORY, number=17
)
"""The child's own pull request, as the association search reports it."""

PARENT_IDENTITY: typ.Final = stack_records.PullRequestIdentity(
    repository=ASSOCIATION_REPOSITORY, number=16
)
"""The parent pull request the search or a stack may lead to."""

DECOY_IDENTITY: typ.Final = stack_records.PullRequestIdentity(
    repository=ASSOCIATION_REPOSITORY, number=15
)
"""A pull request on an older commit, which a stronger rung should pre-empt."""

BOUNDARY: typ.Final = "d" * 40
"""The boundary a body's shared record names."""

SILENT_BODY: typ.Final = (
    "This branch was branched off its parent. The commits above the boundary "
    "are its own work."
)
"""A pull request body that claims no shared record at all."""

SECOND_CHILD_IDENTITY: typ.Final = stack_records.PullRequestIdentity(
    repository=ASSOCIATION_REPOSITORY, number=18
)
"""A second pull request the child branch heads, on an older commit."""

PARENT_IDENTIFICATION: typ.Final[observability.Operation] = "parent_identification"
"""Operation every question about the parent is recorded under."""


@dataclasses.dataclass(frozen=True, slots=True)
class History:
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
    windows : list[tuple[str, ...]]
        The commits every read returned, in the order the reads were made.

    """

    commits: tuple[str, ...] = ()
    refusal: Exception | None = None
    limits: list[int | None] = dataclasses.field(default_factory=list)
    windows: list[tuple[str, ...]] = dataclasses.field(default_factory=list)

    def history(self, rev: str, *, limit: int | None = None) -> tuple[str, ...]:
        """Return the newest commits of the history this double holds.

        The window is taken from the tip, as the real reader takes it, so a
        read of a history longer than the bound answers with the commits a
        squash could have landed rather than with the oldest ones. A ``limit``
        of zero keeps no commits at all, which ``self.commits[-0:]`` would
        otherwise read as keeping every one of them.

        Parameters
        ----------
        rev : str
            Revision the history is read from, which this double ignores.
        limit : int | None
            How many of the newest commits to keep, or ``None`` for all of
            them.

        Returns
        -------
        tuple[str, ...]
            The commits kept, oldest first.

        """
        self.limits.append(limit)
        if self.refusal is not None:
            raise self.refusal
        if limit is None:
            window = self.commits
        elif limit <= 0:
            window = ()
        else:
            window = self.commits[-limit:]
        self.windows.append(window)
        return window


@dataclasses.dataclass(frozen=True, slots=True)
class Records:
    """A record reader that answers with one record, and counts the reads.

    Only ``read`` is implemented, because it is the only record question the
    ladder puts: the double is cast to the port rather than completed, so a
    ladder that reached for another question would fail against it.

    Attributes
    ----------
    record : stack_records.RecordResult
        The record the branch is read as having, absent by default.
    refusal : Exception | None
        Failure the read raises instead of answering.
    reads : list[str]
        The branch of every read, in the order the reads were made.

    """

    record: stack_records.RecordResult = dataclasses.field(
        default_factory=stack_records.RecordAbsent
    )
    refusal: Exception | None = None
    reads: list[str] = dataclasses.field(default_factory=list)

    def read(self, branch: str) -> stack_records.RecordResult:
        """Return the record this test supplied, or raise its refusal."""
        self.reads.append(branch)
        if self.refusal is not None:
            raise self.refusal
        return self.record


def _supplied[Supplied](
    provided: cabc.Mapping[int, Supplied], number: int, *, asking: str
) -> Supplied:
    """Return what the test supplied for ``number``, or refuse to answer.

    A question the test did not provide for is a defect in the test rather than
    an answer of nothing: which questions the ladder puts is half of what these
    tests assert, so the refusal names the question the ladder put. It is a
    ``NotImplementedError`` because the doubles are cast to their ports rather
    than completed — what a port does not implement is what the ladder must not
    have reached for.

    Parameters
    ----------
    provided : collections.abc.Mapping[int, Supplied]
        What the test supplied, keyed by pull request number.
    number : int
        Pull request number the ladder asked about.
    asking : str
        Question the ladder put, phrased to read after ``the ladder``.

    Returns
    -------
    Supplied
        What the test supplied for ``number``.

    Raises
    ------
    NotImplementedError
        If the test supplied nothing for ``number``.

    """
    try:
        return provided[number]
    except KeyError:
        msg = f"the ladder {asking} {number}, unprovided"
        raise NotImplementedError(msg) from None


@dataclasses.dataclass(frozen=True, slots=True)
class Forge(wheresat_github.WheresatGitHub):
    """A forge whose every answer a test supplies, asked in a recorded order.

    A question the test did not provide for is a defect in the test rather than
    an answer of nothing: which questions the ladder puts is half of what these
    tests assert, so an unprovided one is refused by ``_supplied`` rather than
    read as a negative.

    Attributes
    ----------
    payloads : cabc.Mapping[int, ParentPullRequest]
        What ``pull_request`` answers, keyed by pull request number.
    stacks : cabc.Mapping[int, stack_records.PullRequestIdentity | None]
        What ``stack_parent`` answers, keyed by pull request number.
    bodies : cabc.Mapping[int, str]
        What ``pull_request_body`` answers, keyed by pull request number.
    page : wheresat_github.AssociationPage
        What the association search answers.
    read : list[int]
        Every pull request read, in the order it was asked about.
    bodies_read : list[int]
        Every body read, in the order it was asked about. It is kept apart from
        ``read`` because the body is a second question about the child, and a
        rung that answered before it was put should leave this list empty.

    """

    payloads: cabc.Mapping[int, ParentPullRequest] = dataclasses.field(
        default_factory=dict
    )
    stacks: cabc.Mapping[int, stack_records.PullRequestIdentity | None] = (
        dataclasses.field(default_factory=dict)
    )
    bodies: cabc.Mapping[int, str] = dataclasses.field(default_factory=dict)
    page: wheresat_github.AssociationPage = dataclasses.field(
        default_factory=lambda: wheresat_github.AssociationPage(
            associations={}, commits_examined=0, truncated=False
        )
    )
    read: list[int] = dataclasses.field(default_factory=list)
    bodies_read: list[int] = dataclasses.field(default_factory=list)

    @typ.override
    def pull_request(
        self, identity: stack_records.PullRequestIdentity
    ) -> ParentPullRequest:
        """Return the payload this test supplied for ``identity``."""
        self.read.append(identity.number)
        return _supplied(self.payloads, identity.number, asking="read pull request")

    @typ.override
    def pull_request_body(self, identity: stack_records.PullRequestIdentity) -> str:
        """Return the body this test supplied for ``identity``."""
        self.bodies_read.append(identity.number)
        return _supplied(self.bodies, identity.number, asking="read the body of")

    @typ.override
    def stack_parent(
        self, identity: stack_records.PullRequestIdentity
    ) -> stack_records.PullRequestIdentity | None:
        """Return the pull request this test put below ``identity``, if any."""
        return _supplied(
            self.stacks, identity.number, asking="asked about the stack of"
        )

    @typ.override
    def associated_pull_requests(
        self, repository: str, commits: cabc.Sequence[str]
    ) -> wheresat_github.AssociationPage:
        """Return the association page this test supplied."""
        if repository != ASSOCIATION_REPOSITORY:
            msg = "the search should be put to the repository the run names"
            raise AssertionError(msg)
        return self.page


@dataclasses.dataclass(frozen=True, slots=True)
class Opener:
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


def boundary_request(
    *,
    parent: stack_records.PullRequestIdentity | None = None,
    offline: bool = False,
) -> BoundaryRequest:
    """Return the run's question, naming a parent and an offline flag."""
    return BoundaryRequest(
        branch=CHILD_BRANCH,
        child_tip=CHILD_TIP,
        target=TARGET,
        parent=parent,
        deep=False,
        heuristic_window=DEFAULT_WINDOW,
        offline=offline,
    )


def search_bounds(
    *, limit: int = 20, repository: str | None = ASSOCIATION_REPOSITORY
) -> parents.SearchBounds:
    """Return how far the search may walk, and in which repository."""
    return parents.SearchBounds(limit=limit, repository=repository)


def graph_over(*commits: str) -> History:
    """Return a graph answering with ``commits``, oldest first."""
    return History(commits=commits)


@dataclasses.dataclass(frozen=True, slots=True)
class Run:
    """Everything one walk of the ladder reads through, as a test supplies it.

    Attributes
    ----------
    history : History
        The graph the search's window is read from.
    records : Records
        The child's own record, which may name a parent.
    opener : Opener | None
        What opens the forge, or ``None`` for a caller that offers none.

    """

    history: History = dataclasses.field(default_factory=History)
    records: Records = dataclasses.field(default_factory=Records)
    opener: Opener | None = None

    @property
    def reads(self) -> parents.LadderReads:
        """The three reads this walk is handed: history, record, and forge."""
        return parents.LadderReads(
            graph=typ.cast("wheresat_graph.WheresatGraph", self.history),
            records=typ.cast("stack_store.StackRecordReader", self.records),
            opener=self.opener,
        )


def ask(
    request: BoundaryRequest,
    bounds: parents.SearchBounds,
    *,
    run: Run | None = None,
) -> parents.ParentIdentification:
    """Put the run's parent question to the ladder."""
    return parents.identify_parent(request, bounds, reads=(run or Run()).reads)


def child_payload(*, stacked: bool = False) -> ParentPullRequest:
    """Return the child's own pull request, as GitHub would report it."""
    return parent_pull_request(
        identity=CHILD_IDENTITY,
        head_ref=CHILD_BRANCH,
        head_repository=PR_REPOSITORY,
        stacked=stacked,
    )


def stack_record(parent: stack_records.StackParent) -> stack_records.StackRecord:
    """Return the child's record, stacked as ``parent`` says."""
    return stack_records.StackRecord(
        branch=CHILD_BRANCH,
        parent=parent,
        base=BOUNDARY,
        recorded_from=CHILD_TIP,
        evidence=EvidenceKind.MERGE_BASE,
    )


def stacked_on(
    pull_request: stack_records.PullRequestIdentity,
) -> stack_records.StackRecord:
    """Return a record naming a pull request, as a refreshed one does."""
    return stack_record(
        stack_records.StackParent(branch=None, pull_request=pull_request)
    )


def born_on(branch: str) -> stack_records.StackRecord:
    """Return a record naming a branch, as one written at birth does."""
    return stack_record(stack_records.StackParent(branch=branch, pull_request=None))


def shared_body(number: int, *, boundary: str = BOUNDARY) -> str:
    """Return a body carrying a shared record, as an author pastes one.

    The two lines are written out here rather than rendered, because what a body
    carries is the spelling a person types; the parser's own suite pins that
    spelling, and this one pins that the ladder reads it.

    Parameters
    ----------
    number : int
        The pull request number the record names as the child's stack parent.
    boundary : str
        The commit the record names as the exclusive replay boundary.

    Returns
    -------
    str
        The two lines the body carries, with no trailing newline.

    """
    return (
        f"Stack parent: {ASSOCIATION_REPOSITORY}#{number}\n"
        f"Replay boundary (exclusive): {boundary}"
    )


def parent_payload() -> ParentPullRequest:
    """Return the parent pull request, as GitHub would report it."""
    return parent_pull_request(identity=PARENT_IDENTITY, head_sha=PARENT_HEAD)


def association_page(
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
