"""The forge double, and what opens it.

Every forge question the ladder puts is answered by the port here from data a
test supplied, and recorded as it is put, because which questions were put is
half of what the suite asserts: one the test did not provide for is refused as
the defect in the test it is rather than read as a negative answer. The opener
beside it counts the opens, so a case can assert the forge was never reached,
and the values a case answers with are written in
:mod:`tests.unit.wheresat_parents_payloads`.

Nothing here opens a repository or a socket.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from git_donkey import wheresat_github
from tests.unit.wheresat_parents_corpus import ASSOCIATION_REPOSITORY

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from git_donkey import stack_records
    from git_donkey.wheresat_records import ParentPullRequest


def _recorded[Supplied](
    log: list[stack_records.PullRequestIdentity],
    provided: cabc.Mapping[stack_records.PullRequestIdentity, Supplied],
    identity: stack_records.PullRequestIdentity,
    *,
    asking: str,
) -> Supplied:
    """Return what the test supplied, recording ``identity`` as it is read.

    Two of the forge's questions read a pull request and answer from a mapping
    the test supplied, so the recording goes with the lookup rather than being
    written out beside each of them.

    Parameters
    ----------
    log : list[stack_records.PullRequestIdentity]
        Reads recorded for the question that was put, which is the one list the
        suites assert on.
    provided : collections.abc.Mapping[stack_records.PullRequestIdentity, Supplied]
        What the test supplied, keyed by the pull request's repository and
        number.
    identity : stack_records.PullRequestIdentity
        Pull request the ladder asked about.
    asking : str
        Question the ladder put, phrased to read after ``the ladder``.

    Returns
    -------
    Supplied
        What the test supplied for ``identity``.

    Raises
    ------
    NotImplementedError
        If the test supplied nothing for ``identity``.

    """
    log.append(identity)
    return _supplied(provided, identity, asking=asking)


def _supplied[Supplied](
    provided: cabc.Mapping[stack_records.PullRequestIdentity, Supplied],
    identity: stack_records.PullRequestIdentity,
    *,
    asking: str,
) -> Supplied:
    """Return what the test supplied for ``identity``, or refuse to answer.

    The answer is keyed by the pull request whole rather than by its number,
    because a number names a pull request only within its repository: a stack
    that crossed repositories could otherwise be answered for the wrong one.

    A question the test did not provide for is a defect in the test rather than
    an answer of nothing: which questions the ladder puts is half of what these
    tests assert, so the refusal names the question the ladder put. It is a
    ``NotImplementedError`` because the doubles are cast to their ports rather
    than completed — what a port does not implement is what the ladder must not
    have reached for.

    Parameters
    ----------
    provided : collections.abc.Mapping[stack_records.PullRequestIdentity, Supplied]
        What the test supplied, keyed by the pull request's repository and
        number.
    identity : stack_records.PullRequestIdentity
        Pull request the ladder asked about.
    asking : str
        Question the ladder put, phrased to read after ``the ladder``.

    Returns
    -------
    Supplied
        What the test supplied for ``identity``.

    Raises
    ------
    NotImplementedError
        If the test supplied nothing for ``identity``.

    """
    try:
        return provided[identity]
    except KeyError as exc:
        msg = f"the ladder {asking} {identity.repository}#{identity.number}, unprovided"
        raise NotImplementedError(msg) from exc


@dataclasses.dataclass(frozen=True, slots=True)
class Forge(wheresat_github.WheresatGitHub):
    """A forge whose every answer a test supplies, asked in a recorded order.

    A question the test did not provide for is a defect in the test rather than
    an answer of nothing: which questions the ladder puts is half of what these
    tests assert, so an unprovided one is refused by ``_supplied`` rather than
    read as a negative.

    Attributes
    ----------
    payloads : cabc.Mapping[stack_records.PullRequestIdentity, ParentPullRequest]
        What ``pull_request`` answers, keyed by the pull request's repository
        and number, because a number alone names one only within a repository.
    stacks : cabc.Mapping[
        stack_records.PullRequestIdentity, stack_records.PullRequestIdentity | None
    ]
        What ``stack_parent`` answers, keyed the same way, answering the bottom
        of a stack with ``None``.
    bodies : cabc.Mapping[stack_records.PullRequestIdentity, str]
        What ``pull_request_body`` answers, keyed the same way.
    page : wheresat_github.AssociationPage
        What the association search answers.
    read : list[stack_records.PullRequestIdentity]
        Every pull request read, in the order it was asked about.
    bodies_read : list[stack_records.PullRequestIdentity]
        Every body read, in the order it was asked about. It is kept apart from
        ``read`` because the body is a second question about the child, and a
        rung that answered before it was put should leave this list empty.
    searches : list[tuple[str, tuple[str, ...]]]
        Every association search put, as the repository it named and the
        commits it asked about, in the order they were put.

    """

    payloads: cabc.Mapping[stack_records.PullRequestIdentity, ParentPullRequest] = (
        dataclasses.field(default_factory=dict)
    )
    stacks: cabc.Mapping[
        stack_records.PullRequestIdentity, stack_records.PullRequestIdentity | None
    ] = dataclasses.field(default_factory=dict)
    bodies: cabc.Mapping[stack_records.PullRequestIdentity, str] = dataclasses.field(
        default_factory=dict
    )
    page: wheresat_github.AssociationPage = dataclasses.field(
        default_factory=lambda: wheresat_github.AssociationPage(
            associations={}, commits_examined=0, truncated=False
        )
    )
    read: list[stack_records.PullRequestIdentity] = dataclasses.field(
        default_factory=list
    )
    bodies_read: list[stack_records.PullRequestIdentity] = dataclasses.field(
        default_factory=list
    )
    searches: list[tuple[str, tuple[str, ...]]] = dataclasses.field(
        default_factory=list
    )

    @typ.override
    def pull_request(
        self, identity: stack_records.PullRequestIdentity
    ) -> ParentPullRequest:
        """Return the payload this test supplied for ``identity``.

        Parameters
        ----------
        identity : stack_records.PullRequestIdentity
            Pull request read, which the double records.

        Returns
        -------
        ParentPullRequest
            The payload the test supplied for that pull request.

        Raises
        ------
        NotImplementedError
            If the test supplied nothing for that pull request, which is a
            defect in the test rather than an answer of nothing.

        """
        return _recorded(self.read, self.payloads, identity, asking="read pull request")

    @typ.override
    def pull_request_body(self, identity: stack_records.PullRequestIdentity) -> str:
        """Return the body this test supplied for ``identity``.

        Parameters
        ----------
        identity : stack_records.PullRequestIdentity
            The pull request the body is read for, which is recorded as read.

        Returns
        -------
        str
            The body the test supplied for that pull request.

        Raises
        ------
        NotImplementedError
            If the test supplied no body for that pull request.

        Notes
        -----
        The body is a second question about the child rather than part of the
        payload the question above answers, so a test that means the ladder to
        read one supplies a body for the same pull request it supplied a
        payload for, and a rung that answered from that payload leaves this
        question unasked.

        """
        return _recorded(
            self.bodies_read, self.bodies, identity, asking="read the body of"
        )

    @typ.override
    def stack_parent(
        self, identity: stack_records.PullRequestIdentity
    ) -> stack_records.PullRequestIdentity | None:
        """Return the pull request this test put below ``identity``, if any.

        Parameters
        ----------
        identity : stack_records.PullRequestIdentity
            Pull request the stack is asked about.

        Returns
        -------
        stack_records.PullRequestIdentity | None
            The pull request the test supplied as the one below, or ``None``
            when it supplied none.

        Raises
        ------
        NotImplementedError
            If the test supplied no answer for that pull request.

        """
        return _supplied(self.stacks, identity, asking="asked about the stack of")

    @typ.override
    def associated_pull_requests(
        self, repository: str, commits: cabc.Sequence[str]
    ) -> wheresat_github.AssociationPage:
        """Return the association page this test supplied.

        The question is recorded whole before it is answered, because what the
        walk asks about is half of what these tests assert: a search put to the
        wrong repository, or one whose window is empty or wider than the bound
        the run set, would otherwise answer with a page no test looks behind.

        Parameters
        ----------
        repository : str
            ``OWNER/REPOSITORY`` slug the search was put to, which must be the
            one the run names.
        commits : collections.abc.Sequence[str]
            Commits the search asked about, newest first.

        Returns
        -------
        wheresat_github.AssociationPage
            The page this test supplied.

        Raises
        ------
        AssertionError
            If the search is put to a repository the run did not name.

        """
        if repository != ASSOCIATION_REPOSITORY:
            msg = "the search should be put to the repository the run names"
            raise AssertionError(msg)
        self.searches.append((repository, tuple(commits)))
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
        """Return the port, or raise the refusal this opener was built with.

        Returns
        -------
        wheresat_github.WheresatGitHub
            The port the test put in this opener.

        Raises
        ------
        Exception
            The refusal the test built the opener with, if it built one.
        NotImplementedError
            If the opener was built with no port and no refusal, which is a
            defect in the test rather than an opener that offers nothing.

        """
        self.opened.append(1)
        if self.refusal is not None:
            raise self.refusal
        if self.forge is None:
            msg = "this opener has no forge to offer"
            raise NotImplementedError(msg)
        return self.forge
