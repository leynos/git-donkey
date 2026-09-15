"""Identify the parent pull request a child branch is stacked on.

A run may be told its parent, may have one written down, or may have to find
one, and the order in which it asks is the whole of this module's policy: an
explicit ``--parent`` first, then the pull request the child's own stack record
names, then the pull request GitHub's own stack records, and last the pull
requests associated with the child's commits. The rungs are asked in that order
because each is a weaker statement than the one before it — a name the user
typed, a claim ``git donkey`` wrote down, a stack GitHub maintains, and an
inference from the pull requests other commits happen to belong to — and
because a run that answered a stronger question has no business asking a weaker
one.

There is no forge client here. The ladder asks :class:`WheresatGitHub`
questions and answers with what the questions produced, so one walk serves the
live API and a recorded one, and a suite can drive every rung without a
network. The port is opened by an opener this module is handed and never by the
ladder itself, which is what lets a run that was told not to touch the network
have nothing to open: ``--offline`` and a caller that offered no opener are the
two reasons this module reports that it asked no forge anything.

Two things about failure are decided here rather than at the boundary. A
question that goes unanswered is a fault, and a fault stops the ladder. The
next rung answers a weaker question, so reading it after a stronger question
failed would present a fallback as the answer the run was looking for, which is
the one reading ADR-005 forbids; a credential this command cannot find is
therefore a fault of the run and not a quieter walk. And the association search
is bounded — by ``--limit`` commits of the child's history and by the ceiling
the adapter will ask about — with the bound reported rather than applied
silently, because "nothing names a boundary" is only an answer from a search
that saw the whole history (INV-5).

See ``docs/execplans/git-wheresat-sub-command.md`` for the rung order, both
bounds, and the reasons each is what it is.

"""

from __future__ import annotations

import dataclasses
import typing as typ

from git_donkey import observability, stack_records
from git_donkey.wheresat_errors import (
    ShallowHistoryError,
    WheresatCredentialError,
    WheresatGitHubError,
    WheresatGraphError,
)
from git_donkey.wheresat_github import ASSOCIATION_SEARCH_LIMIT
from git_donkey.wheresat_payload import identity_text

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from git_donkey.wheresat_github import AssociationPage, WheresatGitHub
    from git_donkey.wheresat_graph import WheresatGraph
    from git_donkey.wheresat_records import BoundaryRequest, ParentPullRequest

_PARENT_IDENTIFICATION: typ.Final[observability.Operation] = "parent_identification"
"""Operation every question about the parent is recorded under."""

_TOO_LONG: typ.Final = (
    "the child's history holds more commits than the {window} this run was "
    "willing to examine, so the commit-to-pull-request search did not run: a "
    "search that stopped short cannot say that nothing names a boundary; name "
    "the parent with --parent"
)
"""Refusal for a history longer than ``--limit`` allows the search to walk."""

_TRUNCATED: typ.Final = (
    "the commit-to-pull-request search stopped after {examined} of the child's "
    "commits, so it did not see the whole history: a search that stopped short "
    "cannot say that nothing names a boundary; name the parent with --parent"
)
"""Refusal for a search the adapter's own budget stopped."""


@dataclasses.dataclass(frozen=True, slots=True)
class ParentIdentification:
    """The parent pull request the ladder named, and what it could not ask.

    ``parent`` and ``faults`` are two answers to one question, and the pair is
    what tells them apart: no parent and no fault is the honest answer of a
    ladder that asked every question it could and found none, while no parent
    with a fault is a question that went unanswered and must not be read as the
    same thing (INV-5).

    Parameters
    ----------
    parent : ParentPullRequest | None
        The parent pull request, as the forge reports it, or ``None`` when the
        ladder named none.
    faults : tuple[str, ...]
        One reason the ladder could not answer, or nothing at all when it
        answered.
    error_kind : observability.ErrorKind | None
        The bounded class of that failure, for the run's observation.

    """

    parent: ParentPullRequest | None = None
    faults: tuple[str, ...] = ()
    error_kind: observability.ErrorKind | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class SearchBounds:
    """How much of the child's history the association search may examine.

    Parameters
    ----------
    limit : int
        Commits the search may examine, from ``--limit``.
    repository : str | None
        ``OWNER/REPOSITORY`` slug of the repository to ask about those commits,
        or ``None`` when the checkout names no GitHub repository at all. A run
        with no repository to ask has no question to put, which is a different
        thing from a question the forge could not answer.

    """

    limit: int
    repository: str | None


def identify_parent(
    request: BoundaryRequest,
    bounds: SearchBounds,
    *,
    graph: WheresatGraph,
    opener: cabc.Callable[[], WheresatGitHub] | None,
) -> ParentIdentification:
    """Return the parent pull request the run can identify, and how it found it.

    Parameters
    ----------
    request : BoundaryRequest
        What the run set out to answer, including a parent the user named and
        whether the run may touch the network at all.
    bounds : SearchBounds
        How far the association search may walk, and which repository to walk
        it in.
    graph : WheresatGraph
        Read-only history questions, for the commits the search asks about.
    opener : collections.abc.Callable[[], WheresatGitHub] | None
        Callable that returns a forge port, or ``None`` when the caller has
        none to offer. It is called at most once, and only by a run that is
        going to ask the forge something.

    Returns
    -------
    ParentIdentification
        The parent pull request, or why none was identified.

    """
    if request.parent is not None:
        return _named(request.parent, opener)
    return _searched(request, bounds, graph=graph, opener=opener)


def _named(
    identity: stack_records.PullRequestIdentity,
    opener: cabc.Callable[[], WheresatGitHub] | None,
) -> ParentIdentification:
    """Return the pull request the run was told to consult, as the forge reads it.

    A named parent overrides every rung below it, and the repository the
    association search would have run in with them: a run whose checkout names
    no GitHub repository can still answer a question about a pull request the
    user named, because the address was given rather than discovered.

    Parameters
    ----------
    identity : stack_records.PullRequestIdentity
        The pull request ``--parent`` named.
    opener : collections.abc.Callable[[], WheresatGitHub] | None
        Callable that returns a forge port, or ``None`` when the caller has
        none to offer.

    Returns
    -------
    ParentIdentification
        What the pull request is, or why it could not be read.

    """
    if opener is None:
        return _observed(None, "skipped")
    port = _forge(opener)
    if isinstance(port, ParentIdentification):
        return port
    read = _read(port, identity)
    if isinstance(read, ParentIdentification):
        return read
    return _observed(read, "found")


def _searched(
    request: BoundaryRequest,
    bounds: SearchBounds,
    *,
    graph: WheresatGraph,
    opener: cabc.Callable[[], WheresatGitHub] | None,
) -> ParentIdentification:
    """Return the parent the child's own pull requests lead to, if any do.

    Parameters
    ----------
    request : BoundaryRequest
        What the run set out to answer.
    bounds : SearchBounds
        How far the search may walk, and which repository to walk it in.
    graph : WheresatGraph
        Read-only history questions.
    opener : collections.abc.Callable[[], WheresatGitHub] | None
        Callable that returns a forge port, or ``None`` when the caller has
        none to offer.

    Returns
    -------
    ParentIdentification
        The parent pull request the search found, or why none was found.

    """
    repository = bounds.repository
    # Three reasons a run asks nothing, and they are three different facts: a
    # checkout that names no GitHub repository has nothing to ask about, a run
    # offered no opener has nothing to ask through, and one told ``--offline``
    # may not ask. All three leave the search skipped rather than faulted,
    # because a question the run never put is not one the forge failed to answer.
    unasked = opener is None or request.offline or repository is None
    if unasked:
        return _observed(None, "skipped")
    port = _forge(opener)
    if isinstance(port, ParentIdentification):
        return port
    window = _window(request, bounds.limit, graph=graph)
    if isinstance(window, ParentIdentification):
        return window
    page = _page(port, repository, window)
    if isinstance(page, ParentIdentification):
        return page
    return _walk(port, page, request.branch, window)


def _forge(
    opener: cabc.Callable[[], WheresatGitHub],
) -> WheresatGitHub | ParentIdentification:
    """Return the forge port, or the identification a run with none reports.

    A credential this command cannot find is not an answer about a boundary but
    a question left unasked, so it is reported as a fault with the reasons the
    refusal already names — the two environment variables, the cache, and
    ``--offline`` — rather than as a usage failure that would put the run's
    environment above its evidence: the refusal is Table 3's row, and the exit
    status is the one an unanswered question carries. Catching the credential
    failure here is also what keeps it off the usage path, because the class is
    a :class:`~git_donkey.wheresat_errors.WheresatUsageError` everywhere else
    and the run that opened a forge is the only one that may answer ``3``.

    Parameters
    ----------
    opener : collections.abc.Callable[[], WheresatGitHub]
        Callable that returns a forge port.

    Returns
    -------
    WheresatGitHub | ParentIdentification
        The port, or the identification to report instead of walking.

    """
    try:
        return opener()
    except (WheresatGitHubError, WheresatCredentialError) as exc:
        kind = (
            "credential_unavailable"
            if isinstance(exc, WheresatCredentialError)
            else "github_api_error"
        )
        return _fault(f"the forge could not be opened: {exc}", kind)


def _window(
    request: BoundaryRequest,
    limit: int,
    *,
    graph: WheresatGraph,
) -> tuple[str, ...] | ParentIdentification:
    """Return the child's commits the search may examine, newest first.

    One commit beyond the bound is asked for, because a window that came back
    exactly full cannot be told from a history that is exactly as long as the
    bound; asking for one more is what makes "the search saw the whole history"
    a fact rather than an assumption. A ``--limit`` below one is read as one:
    the option bounds the search, and a search allowed no commits has nothing
    to report but that it examined none.

    Parameters
    ----------
    request : BoundaryRequest
        What the run set out to answer, for the tip the history is read from.
    limit : int
        Commits the search may examine, from ``--limit``.
    graph : WheresatGraph
        Read-only history questions.

    Returns
    -------
    tuple[str, ...] | ParentIdentification
        The commits to ask about, newest first, or the refusal to report.

    """
    window = min(max(limit, 1), ASSOCIATION_SEARCH_LIMIT)
    try:
        history = graph.history(request.child_tip, limit=window + 1)
    except WheresatGraphError as exc:
        kind = (
            "shallow_history"
            if isinstance(exc, ShallowHistoryError)
            else "git_command_error"
        )
        return _fault(f"the child's history could not be read: {exc}", kind)
    if len(history) > window:
        return _fault(_TOO_LONG.format(window=window), "search_incomplete")
    return tuple(reversed(history))


def _page(
    port: WheresatGitHub,
    repository: str,
    commits: tuple[str, ...],
) -> AssociationPage | ParentIdentification:
    """Return the pull requests associated with the child's commits.

    Parameters
    ----------
    port : WheresatGitHub
        Forge the question is put to.
    repository : str
        ``OWNER/REPOSITORY`` slug the commits belong to.
    commits : tuple[str, ...]
        Commits to ask about, newest first.

    Returns
    -------
    AssociationPage | ParentIdentification
        What GitHub associated with those commits, or the refusal to report.
        A page the adapter says it truncated is a refusal: the walk cannot
        reach the bound and then read what it saw as the whole history.

    """
    try:
        page = port.associated_pull_requests(repository, commits)
    except WheresatGitHubError as exc:
        return _fault(
            f"the pull requests associated with the child's commits could not "
            f"be read: {exc}",
            "github_api_error",
        )
    if page.truncated:
        return _fault(
            _TRUNCATED.format(examined=page.commits_examined), "search_incomplete"
        )
    return page


def _walk(
    port: WheresatGitHub,
    page: AssociationPage,
    branch: str,
    commits: tuple[str, ...],
) -> ParentIdentification:
    """Return the parent pull request the child's associations name.

    The walk asks about each associated pull request in turn, newest commit
    first and GitHub's own order within a commit. The first pull request whose
    head ref is the child branch is the child's own, and GitHub is asked one
    more question about it — whether it records the child in a stack — before
    the walk continues past it. The first pull request that is not the child's
    own is the parent: the commits above the boundary belong to the child's own
    pull request and to nothing else, so a pull request that is not the child's
    own can only have been reached through a commit the child inherited.

    A pull request associated with more than one of the child's commits is read
    once, because the answer is a fact about the pull request rather than about
    the commit it was reached through.

    Parameters
    ----------
    port : WheresatGitHub
        Forge every question is put to.
    page : AssociationPage
        What GitHub associated with the child's commits.
    branch : str
        The child branch, which is what tells the child's own pull request
        apart from the parent's.
    commits : tuple[str, ...]
        Commits the page was asked about, newest first.

    Returns
    -------
    ParentIdentification
        The parent pull request, or ``empty`` when nothing named one.

    """
    child: ParentPullRequest | None = None
    seen: set[stack_records.PullRequestIdentity] = set()
    for identity in _reported(page, commits):
        if identity in seen:
            continue
        seen.add(identity)
        read = _read(port, identity)
        if isinstance(read, ParentIdentification):
            return read
        if read.head_ref != branch:
            return _observed(read, "found")
        if child is not None:
            continue
        child = read
        below = _below_the_child(port, child)
        if isinstance(below, ParentIdentification):
            return below
        if below is None:
            continue
        return _observed(below, "found")
    return _observed(None, "empty")


def _reported(
    page: AssociationPage,
    commits: tuple[str, ...],
) -> cabc.Iterator[stack_records.PullRequestIdentity]:
    """Yield the associated pull requests, in the order the walk should ask.

    The commits are walked in the order the run asked about them — newest
    first — rather than in whatever order the page holds them, because the walk
    stops at the first association that is not the child's own and the newest
    commit is the one most likely to be the child's.

    Parameters
    ----------
    page : AssociationPage
        What GitHub associated with the child's commits.
    commits : tuple[str, ...]
        Commits the page was asked about, newest first.

    Yields
    ------
    stack_records.PullRequestIdentity
        One associated pull request at a time.

    """
    for commit in commits:
        yield from page.associations.get(commit, ())


def _read(
    port: WheresatGitHub,
    identity: stack_records.PullRequestIdentity,
) -> ParentPullRequest | ParentIdentification:
    """Return what the forge reports for one pull request, or why it would not.

    Parameters
    ----------
    port : WheresatGitHub
        Forge the question is put to.
    identity : stack_records.PullRequestIdentity
        Pull request to read.

    Returns
    -------
    ParentPullRequest | ParentIdentification
        The pull request's payload, or the refusal to report.

    """
    try:
        return port.pull_request(identity)
    except WheresatGitHubError as exc:
        return _fault(
            f"the pull request {identity_text(identity)} could not be read: {exc}",
            "github_api_error",
        )


def _below_the_child(
    port: WheresatGitHub,
    child: ParentPullRequest,
) -> ParentPullRequest | ParentIdentification | None:
    """Return the pull request GitHub's own stack puts below the child, if any.

    GitHub's stack is asked before the association search continues because it
    is a statement rather than an inference: the child's payload carries a
    stack, and the stack names its neighbours. ``None`` is the answer of a pull
    request GitHub does not record in a stack, which is what sends the walk on
    to the commits the child inherited.

    Parameters
    ----------
    port : WheresatGitHub
        Forge the question is put to.
    child : ParentPullRequest
        The child's own pull request.

    Returns
    -------
    ParentPullRequest | ParentIdentification | None
        The parent's payload, the refusal to report, or nothing at all when
        GitHub records no stack below the child.

    """
    try:
        identity = port.stack_parent(child.identity)
    except WheresatGitHubError as exc:
        return _fault(
            f"the stack GitHub records for {identity_text(child.identity)} "
            f"could not be read: {exc}",
            "github_api_error",
        )
    if identity is None:
        return None
    return _read(port, identity)


def _observed(
    parent: ParentPullRequest | None,
    outcome: observability.Outcome,
) -> ParentIdentification:
    """Return an identification, recorded as ``outcome``.

    Parameters
    ----------
    parent : ParentPullRequest | None
        The parent pull request, when the ladder identified one.
    outcome : observability.Outcome
        What became of the question, as the bounded vocabulary spells it.

    Returns
    -------
    ParentIdentification
        The identification, with no fault: the ladder answered.

    """
    observability.get_recorder().record(
        observability.Observation(
            operation=_PARENT_IDENTIFICATION,
            outcome=outcome,
        )
    )
    return ParentIdentification(parent=parent)


def _fault(
    reason: str,
    error_kind: observability.ErrorKind,
) -> ParentIdentification:
    """Return the identification of a question the ladder could not put.

    Parameters
    ----------
    reason : str
        Why the question went unanswered, in the operator's words.
    error_kind : observability.ErrorKind
        The bounded class of the failure.

    Returns
    -------
    ParentIdentification
        The identification, carrying the fault and no parent.

    """
    observability.get_recorder().record(
        observability.Observation(
            operation=_PARENT_IDENTIFICATION,
            outcome="unavailable",
            error_kind=error_kind,
        )
    )
    return ParentIdentification(faults=(reason,), error_kind=error_kind)
