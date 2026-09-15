"""Identify the parent pull request a child branch is stacked on.

A run may be told its parent, may have one written down, or may have to find
one, and the order in which it asks is the whole of this module's policy: an
explicit ``--parent`` first, then the pull request the child's own stack record
names, then the pull request GitHub's own stack records, then the one the
child's pull request body claims in prose, and last the pull requests
associated with the child's commits. The rungs are asked in that order because
each is a weaker statement than the one before it — a name the user typed, a
claim ``git donkey`` wrote down, a stack GitHub maintains, a claim the child's
author pasted into a body, and an inference from the pull requests other
commits happen to belong to — and because a run that answered a stronger
question has no business asking a weaker one.

There is no forge client here. The ladder asks :class:`WheresatGitHub`
questions and answers with what the questions produced, so one walk serves the
live API and a recorded one, and a suite can drive every rung without a
network. The port is opened by an opener this module is handed and never by the
ladder itself, which is what lets a run that was told not to touch the network
have nothing to open: ``--offline`` and a caller that offered no opener are the
two reasons this module reports that it asked no forge anything. The child's
own record is read through a reader the run hands in the same way, because two
phases read that record — this ladder, for a parent it may name, and the
collection phase, for the boundary — and neither may read a different one than
the other.

Two things about failure are decided here rather than at the boundary. A
question that goes unanswered is a fault, and a fault stops the ladder. The
next rung answers a weaker question, so reading it after a stronger question
failed would present a fallback as the answer the run was looking for, which is
the one reading ADR-005 forbids; a credential this command cannot find is
therefore a fault of the run and not a quieter walk. A body whose record cannot
be read, and one whose record supports several readings, are faults of that
same kind: the claim was made and this run cannot take it up, so walking on
would report the weakest rung's answer as the one the body's author was after.
And the association search
is bounded — by ``--limit`` commits of the child's history and by the ceiling
the adapter will ask about — with the bound reported rather than applied
silently, because "nothing names a boundary" is only an answer from a search
that saw the whole history (INV-5).

What a rung answers with, and what it is handed to read through, live in
:mod:`git_donkey.wheresat_ladder`: this module is the policy, and that module is
the vocabulary the policy is written in, so a rung states its answer the same way
whichever question it asked.

See ``docs/execplans/git-wheresat-sub-command.md`` for the rung order, both
bounds, and the reasons each is what it is.

"""

from __future__ import annotations

import typing as typ

from git_donkey import (
    stack_records,
    stack_store,
    wheresat_shared_record,
)
from git_donkey.wheresat_errors import (
    ShallowHistoryError,
    WheresatCredentialError,
    WheresatGitHubError,
    WheresatGraphError,
)
from git_donkey.wheresat_gates import short_commit
from git_donkey.wheresat_github import ASSOCIATION_SEARCH_LIMIT
from git_donkey.wheresat_ladder import (
    LadderReads,
    ParentIdentification,
    SearchBounds,
    answered,
    faulted,
)
from git_donkey.wheresat_payload import identity_text

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from git_donkey.wheresat_github import AssociationPage, WheresatGitHub
    from git_donkey.wheresat_graph import WheresatGraph
    from git_donkey.wheresat_records import BoundaryRequest, ParentPullRequest

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


def identify_parent(
    request: BoundaryRequest,
    bounds: SearchBounds,
    *,
    reads: LadderReads,
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
    reads : LadderReads
        What this walk reads through: history, the child's record, and the
        forge. The opener is called at most once, and only by a run that is
        going to ask the forge something.

    Returns
    -------
    ParentIdentification
        The parent pull request, or why none was identified.

    """
    if request.parent is not None:
        return _named(request.parent, reads.opener)
    opener = reads.opener
    if opener is None or request.offline:
        # A run offered no opener has nothing to ask through, and one told
        # ``--offline`` may not ask. Both are decided before any rung is walked,
        # and before the child's own record is read, because a question the run
        # never put is not one the forge failed to answer and a record this run
        # may not act on can name no parent to it.
        return answered(None, "skipped")
    return _searched(request, bounds, reads=reads, opener=opener)


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
        return answered(None, "skipped")
    port = _forge(opener)
    if isinstance(port, ParentIdentification):
        return port
    read = _read(port, identity)
    if isinstance(read, ParentIdentification):
        return read
    return answered(read, "found")


def _searched(
    request: BoundaryRequest,
    bounds: SearchBounds,
    *,
    reads: LadderReads,
    opener: cabc.Callable[[], WheresatGitHub],
) -> ParentIdentification:
    """Return the parent the child's own record or pull requests lead to.

    The child's record is read before the search because it is a written claim
    and the search is an inference: a record that names a pull request answers
    the question outright, and a record that names a branch, or names nothing
    at all, leaves the search to answer it.

    Parameters
    ----------
    request : BoundaryRequest
        What the run set out to answer.
    bounds : SearchBounds
        How far the search may walk, and which repository to walk it in.
    reads : LadderReads
        What this walk reads through.
    opener : collections.abc.Callable[[], WheresatGitHub]
        What opens the forge, handed in already narrowed by the caller that
        decided this run may ask one at all.

    Returns
    -------
    ParentIdentification
        The parent pull request the record or the search named, or why none was
        named.

    """
    written = _written(_record(reads, request.branch), opener)
    if written is not None:
        return written
    repository = bounds.repository
    if repository is None:
        # A checkout that names no GitHub repository has no search to put. The
        # record's own address was read first, because an address the run's
        # repository supplies is put to the forge rather than discovered in it.
        return answered(None, "skipped")
    port = _forge(opener)
    if isinstance(port, ParentIdentification):
        return port
    window = _window(request, bounds.limit, graph=reads.graph)
    if isinstance(window, ParentIdentification):
        return window
    page = _page(port, repository, window)
    if isinstance(page, ParentIdentification):
        return page
    return _walk(port, page, request.branch, window)


def _record(reads: LadderReads, branch: str) -> stack_records.RecordResult:
    """Return the child's own record, as a value even when reading it failed.

    A record that cannot be read is not this rung's answer to report: the
    collection phase reports it under its own kind, through the rung that owns
    the record, and a ladder that faulted here as well would report one
    unusable record twice. So the read failure is read as a record that names
    no parent, and the walk goes on to the questions below.

    Returns
    -------
    stack_records.RecordResult
        The record, or an absent one when it could not be read.

    """
    try:
        return reads.records.read(branch)
    except stack_store.StackRecordError:
        return stack_records.RecordAbsent()


def _written(
    record: stack_records.RecordResult,
    opener: cabc.Callable[[], WheresatGitHub] | None,
) -> ParentIdentification | None:
    """Return the parent the child's own record names, if it names one.

    A record written after the parent was opened names a pull request, and that
    address is read exactly as ``--parent`` is: the record is an address the
    run's own repository supplied, so a checkout that names no GitHub
    repository of its own can still answer it. A record written at birth names
    a branch instead, and a branch is not a pull request — nothing local says
    which pull request heads it — so such a record answers nothing here and the
    ladder walks on.

    Returns
    -------
    ParentIdentification | None
        What the named pull request is, or why it could not be read; ``None``
        when the record names no pull request at all.

    """
    match record:
        case stack_records.StackRecord(
            parent=stack_records.StackParent(pull_request=identity)
        ) if identity is not None:
            return _named(identity, opener)
    return None


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
        return faulted(f"the forge could not be opened: {exc}", kind)


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
        return faulted(f"the child's history could not be read: {exc}", kind)
    if len(history) > window:
        return faulted(_TOO_LONG.format(window=window), "search_incomplete")
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
        return faulted(
            f"the pull requests associated with the child's commits could not "
            f"be read: {exc}",
            "github_api_error",
        )
    if page.truncated:
        return faulted(
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
    head ref is the child branch is the child's own, and GitHub is asked two
    more questions about it — whether it records the child in a stack, and, when
    it records none, what the child's body claims — before the walk continues
    past it. The first pull request that is not the child's own is the parent:
    the commits above the boundary belong to the child's own pull request and
    to nothing else, so a pull request that is not the child's own can only have
    been reached through a commit the child inherited.

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
            return answered(read, "found")
        if child is not None:
            continue
        child = read
        beneath = _beneath_the_child(port, child)
        if beneath is not None:
            return beneath
    return answered(None, "empty")


def _beneath_the_child(
    port: WheresatGitHub,
    child: ParentPullRequest,
) -> ParentIdentification | None:
    """Return the parent below the child, from GitHub's stack or its own claim.

    The two questions are asked in the order the ladder puts them: a stack
    GitHub maintains is a statement the forge makes about the child, and the
    child's body is a claim its author made, so the statement is read first and
    the claim only when there is no statement to read.

    Parameters
    ----------
    port : WheresatGitHub
        Forge every question is put to.
    child : ParentPullRequest
        The child's own pull request.

    Returns
    -------
    ParentIdentification | None
        The parent's payload, or the fault to report; ``None`` when neither
        GitHub's stack nor the child's body names one, which sends the walk on
        to the commits the child inherited.

    """
    below = _below_the_child(port, child)
    if isinstance(below, ParentIdentification):
        return below
    if below is not None:
        return answered(below, "found")
    return _claimed(port, child)


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
        return faulted(
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
        return faulted(
            f"the stack GitHub records for {identity_text(child.identity)} "
            f"could not be read: {exc}",
            "github_api_error",
        )
    if identity is None:
        return None
    return _read(port, identity)


def _claimed(
    port: WheresatGitHub,
    child: ParentPullRequest,
) -> ParentIdentification | None:
    """Return the parent the child's pull request body claims, if it claims one.

    The body is read as prose, and what it claims is a candidate like every
    other rung: the pull request it names is read from the forge and reported as
    the parent only because the address came from the child's own author. The
    parse rides back on the identification so the collection phase can credit
    the claim without reading the body a second time — collection has no forge,
    and a body read twice is a body two phases could read differently.

    A claim that cannot be read, and one that supports several readings, are
    faults rather than a reason to walk on: the author named a parent and this
    run cannot take the naming up, so the answer below would be the weakest
    rung's rather than the one the author was after.

    Parameters
    ----------
    port : WheresatGitHub
        Forge the body is read from and the claim is put to.
    child : ParentPullRequest
        The child's own pull request, whose body carries the claim.

    Returns
    -------
    ParentIdentification | None
        The claimed parent's payload, or the fault to report; ``None`` when the
        body claims no parent at all, which sends the walk on to the commits the
        child inherited.

    """
    try:
        body = port.pull_request_body(child.identity)
    except WheresatGitHubError as exc:
        return faulted(
            f"the body of {identity_text(child.identity)} could not be read: {exc}",
            "github_api_error",
        )
    claim = wheresat_shared_record.parse_shared_record(body)
    match claim:
        case wheresat_shared_record.SharedRecordAbsent():
            return None
        case wheresat_shared_record.SharedRecordMalformed(reason=reason):
            return faulted(
                f"the shared record in the body of {identity_text(child.identity)} "
                f"could not be read: {reason}",
                "stack_record_malformed",
            )
        case wheresat_shared_record.SharedRecordAmbiguous(records=readings):
            return faulted(_ambiguous(child, readings), "stack_record_malformed")
        case wheresat_shared_record.SharedRecord(parent=parent):
            return _claimed_parent(port, parent, claim)
    return None


def _claimed_parent(
    port: WheresatGitHub,
    parent: stack_records.PullRequestIdentity,
    claim: wheresat_shared_record.SharedRecord,
) -> ParentIdentification:
    """Return the pull request a claim names, read from the forge.

    The claim is handed back with the answer rather than paraphrased into it, so
    the collection phase credits the boundary the body's author wrote and not
    this run's reading of it.

    Parameters
    ----------
    port : WheresatGitHub
        Forge the claimed pull request is read from.
    parent : stack_records.PullRequestIdentity
        The pull request the body names as the child's stack parent.
    claim : wheresat_shared_record.SharedRecord
        The reading the parent was named in, which rides back with the answer.

    Returns
    -------
    ParentIdentification
        The claimed parent's payload, or the fault reading it produced.

    """
    read = _read(port, parent)
    if isinstance(read, ParentIdentification):
        return read
    return answered(read, "found", shared=claim)


def _ambiguous(
    child: ParentPullRequest,
    records: tuple[wheresat_shared_record.SharedRecord, ...],
) -> str:
    """Return the refusal of a body that supports more than one reading.

    Every reading the body supports is named, because the person who can settle
    the disagreement is the one who pasted the block, and a refusal that named
    only the reading this run happened to take first would send them to look for
    a second block they were not told about.

    Parameters
    ----------
    child : ParentPullRequest
        The child's own pull request, whose body carries the readings.
    records : tuple[wheresat_shared_record.SharedRecord, ...]
        The readings the body supports, in the order it gives them.

    Returns
    -------
    str
        The refusal, naming every reading it will not choose among.

    """
    readings = "; ".join(
        f"{identity_text(record.parent)} with boundary {short_commit(record.boundary)}"
        for record in records
    )
    return (
        f"the shared record in the body of {identity_text(child.identity)} names "
        f"more than one reading, and this run will not choose among them: "
        f"{readings}; leave one reading in the body, or name the parent with "
        "--parent"
    )
