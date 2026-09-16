"""GitHub as a source of parent evidence for ``git wheresat``.

The command asks the forge four questions and no more: what a pull request
merged as, what its body says, whether GitHub records it as part of a stack,
and which pull requests a bounded set of commits belongs to. They are declared
here as :class:`WheresatGitHub` rather than left implicit, so that a run can be
handed a recording or a stub without the command learning a second way to reach
the network, and so that the one module which speaks HTTP is the one module
that has to be read to know what the command will ask for.

Every question goes through one primitive, :meth:`ApiWheresatGitHub.get`, which
is what makes the failure vocabulary complete: an answer that is not ``200``
becomes :class:`~git_donkey.wheresat_errors.WheresatGitHubError` there and
nowhere else, so a status this module has never seen cannot reach a caller as
though it were data. That is the INV-5 obligation at this boundary — a 404 from
a credential that cannot see a private repository is not "there is no such pull
request", and it is reported as a question GitHub could not answer rather than
as a parent that is not there.

The search for a commit's pull requests is bounded twice, by
:data:`ASSOCIATION_SEARCH_LIMIT` commits and by :data:`NETWORK_BUDGET_SECONDS`
of wall clock, because GitHub's primary limit is 5,000 requests an hour and a
long child branch would spend them one commit at a time. A search that stopped
short says so in :attr:`AssociationPage.truncated` rather than returning the
part it saw as though it were the whole answer; what to do about that — refuse,
and advise ``--parent`` — is the caller's decision, because only the caller
knows whether the user already named one.

Reading the decoded bodies those requests return belongs to
:mod:`git_donkey.wheresat_payload`, so what is left here is the transport and
the vocabulary of its failures.

Nothing here writes, and nothing here decides whether a parent is acceptable. A
pull request this module returns is what GitHub says about it, and the gates
that weigh it live above.

"""

from __future__ import annotations

import dataclasses
import enum
import os
import time
import typing as typ
import urllib.parse

import github3.session as github3_session
import requests

from git_donkey import github_credentials, stack_records
from git_donkey.stack_records import identity_text
from git_donkey.wheresat_errors import (
    WheresatCredentialError,
    WheresatGitHubError,
    WheresatUsageError,
)
from git_donkey.wheresat_payload import (
    associated,
    count_field,
    flag_field,
    list_field,
    mapping,
    nested,
    pull_identity,
    sequence,
    string_field,
)
from git_donkey.wheresat_records import ParentPullRequest

if typ.TYPE_CHECKING:
    import collections.abc as cabc

REQUEST_TIMEOUT_SECONDS: typ.Final = 10.0
"""How long one request may take before it is reported as unanswered."""

NETWORK_BUDGET_SECONDS: typ.Final = 60.0
"""How long the commit-to-pull-request association search may take in total."""

ASSOCIATION_SEARCH_LIMIT: typ.Final = 20
"""How many commits one association search will examine.

A ceiling rather than a default: ``--limit`` may ask for fewer, and no caller
may ask for more, because the cost of the search is one request per commit
against a rate limit the user's other work shares.
"""

_API_ROOT: typ.Final = "https://api.github.com"
_ACCEPT: typ.Final = "application/vnd.github+json"
_ANSWERED: typ.Final = 200
_TOKEN_VARIABLES: typ.Final = ("GITHUB_TOKEN", "GH_TOKEN")
_RATE_LIMIT_HEADERS: typ.Final = (
    "Retry-After",
    "X-RateLimit-Reset",
    "X-RateLimit-Remaining",
)


class _Status(enum.IntEnum):
    """HTTP statuses whose refusal has a reason of its own.

    Members rather than module constants because :func:`_status_reason` weighs
    them in a ``match``: a case written as a bare name is a capture pattern,
    which matches every status and leaves the cases below it unreachable, so
    the names a case compares against have to be dotted.
    """

    UNAUTHORIZED = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    SERVER_ERROR = 500


type _PayloadCache = dict[stack_records.PullRequestIdentity, cabc.Mapping[str, object]]
"""Decoded pull request payloads, keyed by the pull request they describe."""


@dataclasses.dataclass(frozen=True, slots=True)
class AssociationPage:
    """Pull requests associated with a bounded set of commits.

    ``associations`` holds one entry per commit that was examined, including an
    empty tuple for a commit GitHub associates with nothing: the two are
    different answers, and a mapping that simply omitted the second would let a
    truncated search and a barren one look alike.

    Parameters
    ----------
    associations : collections.abc.Mapping[str, tuple[PullRequestIdentity, ...]]
        Pull requests GitHub associates with each examined commit.
    commits_examined : int
        How many commits were actually asked about.
    truncated : bool
        Whether commits were left unexamined, by the limit or by the budget.

    """

    associations: cabc.Mapping[str, tuple[stack_records.PullRequestIdentity, ...]]
    commits_examined: int
    truncated: bool


class WheresatGitHub(typ.Protocol):
    """GitHub surface required to identify and describe the parent."""

    def pull_request(
        self, identity: stack_records.PullRequestIdentity
    ) -> ParentPullRequest:
        """Return merge state, head, base, integration commit, and stack flag."""

    def pull_request_body(self, identity: stack_records.PullRequestIdentity) -> str:
        """Return a pull request body, for shared-record extraction."""

    def stack_parent(
        self, identity: stack_records.PullRequestIdentity
    ) -> stack_records.PullRequestIdentity | None:
        """Return the pull request below this one in a native GitHub stack."""

    def associated_pull_requests(
        self, repository: str, commits: cabc.Sequence[str]
    ) -> AssociationPage:
        """Return the pull requests associated with a bounded set of commits."""


def open_github() -> ApiWheresatGitHub:
    """Return the GitHub adapter, drawing its credential from where one is kept.

    The credential is read from ``GITHUB_TOKEN``, then ``GH_TOKEN``, then the
    file ``git fafo`` caches, in that order. There is deliberately no fourth
    source: this command may not start an interactive authorization, because
    the run that would block on one is the run nobody can use from a script.

    Returns
    -------
    ApiWheresatGitHub
        The adapter, over an authenticated session.

    Raises
    ------
    WheresatCredentialError
        If none of the three sources holds a token.

    """
    session = github3_session.GitHubSession(
        default_connect_timeout=REQUEST_TIMEOUT_SECONDS,
        default_read_timeout=REQUEST_TIMEOUT_SECONDS,
    )
    session.headers.update({"Accept": _ACCEPT})
    session.token_auth(_token())
    return ApiWheresatGitHub(session)


def _token() -> str:
    """Return a GitHub token, or give up as only a script may.

    Returns
    -------
    str
        The token the environment or the credential cache holds.

    Raises
    ------
    WheresatCredentialError
        If no source holds a token, naming every source that was tried.

    """
    for variable in _TOKEN_VARIABLES:
        token = os.environ.get(variable)
        if token:
            return token

    path = github_credentials.credentials_path()
    token = github_credentials.read_token(path)
    if token:
        return token

    msg = (
        "no GitHub credential: set GITHUB_TOKEN or GH_TOKEN, or run git fafo "
        f"to authorize one and cache it in {path}; git wheresat never prompts, "
        "and --offline asks nothing of GitHub at all"
    )
    raise WheresatCredentialError(msg)


@dataclasses.dataclass(frozen=True, slots=True)
class ApiWheresatGitHub:
    """The GitHub implementation of :class:`WheresatGitHub`.

    Parameters
    ----------
    session : requests.Session
        Transport every request goes through. It carries the credential and
        the media type; this class adds nothing to it, so a recorded session
        replays the same requests the live one would have made.
    clock : collections.abc.Callable[[], float], optional
        Monotonic clock the association search measures its budget with. It is
        a parameter because a test cannot wait a minute to watch a budget run
        out, and because nothing else here reads the time.
    payloads : _PayloadCache, optional
        Decoded pull request payloads this adapter has already read, keyed by
        the pull request they describe. Three questions read one pull request's
        own payload, and an adapter answers them from one ask per pull request;
        the field is excluded from equality and from the representation because
        it is what the adapter has been asked, not what it is.

    """

    session: requests.Session
    clock: cabc.Callable[[], float] = time.monotonic
    payloads: _PayloadCache = dataclasses.field(
        default_factory=dict, compare=False, repr=False
    )

    def pull_request(
        self, identity: stack_records.PullRequestIdentity
    ) -> ParentPullRequest:
        """Return merge state, head, base, integration commit, and stack flag.

        Parameters
        ----------
        identity : stack_records.PullRequestIdentity
            Pull request to read.

        Returns
        -------
        ParentPullRequest
            What GitHub reports, with ``head_fetched_from`` left ``None``
            because no fetch has happened yet: the origin of a head is a fact
            about a fetch, and the gate that weighs it must see the absence
            rather than be told the head came from its own repository.

        """
        payload = self._payload(identity)
        merged_at = string_field(nested(payload, "merged_at")) or None
        merged = flag_field(nested(payload, "merged")) or merged_at is not None
        landed = string_field(nested(payload, "merge_commit_sha")) or None
        return ParentPullRequest(
            identity=identity,
            merged=merged,
            merged_at=merged_at,
            head_sha=string_field(nested(payload, "head", "sha")),
            head_ref=string_field(nested(payload, "head", "ref")),
            head_repository=string_field(nested(payload, "head", "repo", "full_name")),
            head_fetched_from=None,
            base_ref=string_field(nested(payload, "base", "ref")),
            base_repository=string_field(nested(payload, "base", "repo", "full_name")),
            landed=landed if merged else None,
            stacked=nested(payload, "stack") is not None,
        )

    def pull_request_body(self, identity: stack_records.PullRequestIdentity) -> str:
        """Return a pull request body, for shared-record extraction.

        Parameters
        ----------
        identity : stack_records.PullRequestIdentity
            Pull request to read.

        Returns
        -------
        str
            The body, or the empty string when GitHub reports none. A body is
            prose a user wrote, so the empty string is the whole of what an
            absent one means here.

        """
        payload = self._payload(identity)
        return string_field(nested(payload, "body"))

    def stack_parent(
        self, identity: stack_records.PullRequestIdentity
    ) -> stack_records.PullRequestIdentity | None:
        """Return the pull request below this one in a native GitHub stack.

        A pull request's own payload says which stack it belongs to and where
        it sits in it, but not which pull request is below it; that neighbour
        is named only by the stack itself. So a pull request whose ``stack`` is
        present and whose position is above the bottom costs a second request,
        and one GitHub does not record as stacked costs none.

        Parameters
        ----------
        identity : stack_records.PullRequestIdentity
            Pull request to read.

        Returns
        -------
        stack_records.PullRequestIdentity | None
            The pull request immediately below it, or ``None`` when GitHub
            records no stack for it or records it at the bottom of one.

        Raises
        ------
        WheresatGitHubError
            If GitHub reports a position the stack it names does not have, or
            lists a pull request below this one that names no number.

        """
        payload = self._payload(identity)
        position = count_field(nested(payload, "stack", "position"))
        if position is None or position <= 1:
            return None
        neighbours = self._stack_members(identity)
        if position > len(neighbours):
            msg = (
                f"GitHub reports {identity_text(identity)} at position {position} "
                f"of a stack that lists {len(neighbours)} pull requests"
            )
            raise WheresatGitHubError(msg)
        below = pull_identity(identity.repository, neighbours[position - 2])
        if below is None:
            msg = (
                f"GitHub's stack for {identity_text(identity)} lists no pull "
                f"request number at position {position - 1}"
            )
            raise WheresatGitHubError(msg)
        return below

    def associated_pull_requests(
        self, repository: str, commits: cabc.Sequence[str]
    ) -> AssociationPage:
        """Return the pull requests associated with a bounded set of commits.

        At most :data:`ASSOCIATION_SEARCH_LIMIT` commits are asked about, and
        at most :data:`NETWORK_BUDGET_SECONDS` of wall clock is spent asking.

        Parameters
        ----------
        repository : str
            ``OWNER/REPOSITORY`` slug the commits belong to.
        commits : collections.abc.Sequence[str]
            Commits to ask about, in the order the run would rather know.

        Returns
        -------
        AssociationPage
            One entry per commit examined, and whether commits were left over.

        """
        examined = commits[:ASSOCIATION_SEARCH_LIMIT]
        deadline = self.clock() + NETWORK_BUDGET_SECONDS
        associations: dict[str, tuple[stack_records.PullRequestIdentity, ...]] = {}
        truncated = len(examined) < len(commits)
        for commit in examined:
            if self.clock() > deadline:
                truncated = True
                break
            payload = self.get(*_commit_pulls_path(repository, commit))
            associations[commit] = associated(payload, repository)
        return AssociationPage(
            associations=associations,
            commits_examined=len(associations),
            truncated=truncated,
        )

    def get(self, *parts: str, params: cabc.Mapping[str, str] | None = None) -> object:
        """Return what GitHub answers for one path, or raise for anything else.

        The two halves of a request are kept apart below — whether GitHub
        answered at all, and whether the answer is one — because they are
        different facts about the run and only the second is the endpoint's
        business. Both raise the same class, which is what leaves this method
        the one place a caller has to know about.

        Every component is percent-encoded before it is joined, so a value
        holding ``?`` or ``#`` asks for the path it spells rather than ending
        the path and opening a query. A component of ``.`` or ``..`` is
        refused instead of encoded, because encoding leaves a dot as it found
        it and the path such a component names is not one this API root
        serves.

        Parameters
        ----------
        *parts : str
            Path below the API root, unencoded and unjoined.
        params : collections.abc.Mapping[str, str] | None, optional
            Query parameters, when the endpoint takes any.

        Returns
        -------
        object
            The decoded body, whose shape is the endpoint's business and not
            this method's.

        Raises
        ------
        WheresatUsageError
            If a component is ``.`` or ``..``, which names a step of the path
            rather than a resource at it.
        WheresatGitHubError
            If the request did not produce a ``200`` with a JSON body, which
            is every way a question can go unanswered: a status GitHub chose,
            a connection that never completed, and a name that would not
            resolve.

        """
        encoded = tuple(_path_component(part) for part in parts)
        url = "/".join((_API_ROOT, *encoded))
        return _decoded(self._answered(url, params), url)

    def _payload(
        self, identity: stack_records.PullRequestIdentity
    ) -> cabc.Mapping[str, object]:
        """Return the pull request's own payload, asking GitHub at most once.

        Three questions read one pull request from one endpoint — whether it
        merged, what its body says, and where it sits in a stack — and a run
        that asks all three would otherwise read the same resource three times.
        The first ask is kept, so a run that asks one question spends what it
        did before.

        Parameters
        ----------
        identity : stack_records.PullRequestIdentity
            Pull request whose payload is wanted.

        Returns
        -------
        collections.abc.Mapping[str, object]
            The decoded payload, whose fields are the callers' business.

        Raises
        ------
        WheresatGitHubError
            If the request did not produce a ``200`` with a JSON object body.

        """
        if identity not in self.payloads:
            self.payloads[identity] = mapping(self.get(*_pull_path(identity)))
        return self.payloads[identity]

    def _answered(
        self, url: str, params: cabc.Mapping[str, str] | None
    ) -> requests.Response:
        """Return GitHub's answer to one request, or refuse the transport fault.

        Parameters
        ----------
        url : str
            Request to make.
        params : collections.abc.Mapping[str, str] | None
            Query parameters, when the endpoint takes any.

        Returns
        -------
        requests.Response
            Whatever came back, including a status that is not an answer: the
            status belongs to :meth:`_decoded`, which reads it.

        Raises
        ------
        WheresatGitHubError
            If the request never completed. The three handlers are ordered,
            not chosen: ``requests`` makes a connect timeout both a timeout and
            a connection failure, and the operator's diagnosis differs — one
            says GitHub is slow and the other says the network is wrong — so
            the timeout is read first. The last handler exists because a
            transport can fail in ways neither of the first two names, and a
            failure that escaped as its own exception type would be a fault
            reported as something other than a question that went unanswered.

        """
        try:
            return self.session.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT_SECONDS,
                headers={"Accept": _ACCEPT},
            )
        except requests.Timeout as exc:
            msg = (
                f"GitHub did not answer {url} within "
                f"{REQUEST_TIMEOUT_SECONDS:g} seconds"
            )
            raise WheresatGitHubError(msg) from exc
        except requests.ConnectionError as exc:
            msg = f"GitHub could not be reached at {url}: {exc}"
            raise WheresatGitHubError(msg) from exc
        except requests.RequestException as exc:
            msg = f"the request for {url} failed before GitHub answered: {exc}"
            raise WheresatGitHubError(msg) from exc

    def _stack_members(
        self, identity: stack_records.PullRequestIdentity
    ) -> tuple[cabc.Mapping[str, object], ...]:
        """Return the pull requests of the stack that holds ``identity``.

        The endpoint is asked for the stack containing one pull request rather
        than for the repository's stacks, because that is the question this
        method has: the answer is either one stack or none.

        Every member is read as an object, and one that cannot be is refused.
        :meth:`stack_parent` finds a pull request's neighbour by its position
        in this tuple, so a member quietly dropped would shift every index
        above it and name a different pull request — or the pull request
        itself — rather than reporting that the stack could not be read.

        Returns
        -------
        tuple[collections.abc.Mapping[str, object], ...]
            The stack's pull requests, in GitHub's order, which is the bottom
            of the stack first.

        Raises
        ------
        WheresatGitHubError
            If the stack lists a member this version cannot read.

        """
        owner, name = _slug(identity.repository)
        params = {"pull_request": str(identity.number)}
        payload = self.get("repos", owner, name, "stacks", params=params)
        stacks = sequence(payload, "a list of stacks")
        if not stacks:
            return ()
        members = list_field(nested(stacks[0], "pull_requests"))
        return tuple(mapping(member) for member in members)


def _decoded(response: requests.Response, url: str) -> object:
    """Return the body of a ``200`` answer, or refuse everything else.

    Parameters
    ----------
    response : requests.Response
        What GitHub answered with.
    url : str
        Request the answer belongs to, which is what the operator needs to
        reproduce it.

    Returns
    -------
    object
        The decoded body, whose shape is the endpoint's business.

    Raises
    ------
    WheresatGitHubError
        If the status was not ``200``, or if the body was not JSON. A body
        that cannot be decoded is a question this version cannot read the
        answer to, which is a question that went unanswered rather than an
        answer of nothing.

    """
    if response.status_code != _ANSWERED:
        raise WheresatGitHubError(_status_reason(response, url))
    try:
        return response.json()
    except ValueError as exc:
        msg = f"GitHub answered {url} with a body that is not JSON"
        raise WheresatGitHubError(msg) from exc


def _status_reason(response: requests.Response, url: str) -> str:
    """Return why a status other than ``200`` is not an answer.

    Parameters
    ----------
    response : requests.Response
        What GitHub answered with.
    url : str
        Request the answer belongs to, which is what the operator needs to
        reproduce it.

    Returns
    -------
    str
        The reason, worded as one of the ways a question can go unanswered.

    """
    status = response.status_code
    match status:
        case _Status.UNAUTHORIZED:
            return (
                f"GitHub rejected the credential for {url} "
                f"(HTTP {_Status.UNAUTHORIZED}); the token may have been "
                "revoked or expired"
            )
        case _Status.FORBIDDEN:
            return _forbidden_reason(response, url)
        case _Status.NOT_FOUND:
            return (
                f"GitHub has nothing at {url} (HTTP {_Status.NOT_FOUND}); a "
                "credential that cannot see a private repository is answered "
                "this way, so this is not evidence that the pull request or "
                "the commit does not exist"
            )
        # ``requests`` types a status code as optional, so the range guard
        # names the absent case before it compares; a response carrying no
        # status at all falls through to the message below.
        case _ if status is not None and status >= _Status.SERVER_ERROR:
            return (
                f"GitHub answered {url} with a server error (HTTP {status}); "
                "the question went unanswered"
            )
    return f"GitHub answered {url} with HTTP {status}, which is not an answer"


def _forbidden_reason(response: requests.Response, url: str) -> str:
    """Return why a ``403`` is not an answer, telling the two causes apart.

    A rate limit and a missing scope are both ``403``, and they need opposite
    responses from an operator: one is answered by waiting, the other never is.
    What tells them apart is ``Retry-After`` or an exhausted remaining count —
    GitHub sends ``X-RateLimit-Reset`` on every answer, so its presence alone
    says nothing. The headers are reported verbatim rather than converted,
    because they are the numbers the operator will be reconciling against
    GitHub's own rate-limit page.

    Parameters
    ----------
    response : requests.Response
        The ``403`` GitHub answered with.
    url : str
        Request the answer belongs to.

    Returns
    -------
    str
        The reason, naming the rate limit when the headers say so.

    """
    headers = {
        name: value
        for name in _RATE_LIMIT_HEADERS
        if (value := response.headers.get(name)) is not None
    }
    if (
        headers.get("Retry-After") is None
        and headers.get("X-RateLimit-Remaining") != "0"
    ):
        return (
            f"GitHub refused the request for {url} (HTTP 403); the credential "
            "may lack the scopes this needs"
        )
    facts = ", ".join(f"{name} {value}" for name, value in headers.items())
    return (
        f"GitHub rate limited the credential for {url} (HTTP 403); {facts}; "
        "the question went unanswered"
    )


def _slug(repository: str) -> tuple[str, str]:
    """Return the owner and repository name a slug is made of.

    Parameters
    ----------
    repository : str
        ``OWNER/REPOSITORY`` slug to split.

    Returns
    -------
    tuple[str, str]
        The slug's two components.

    Raises
    ------
    WheresatUsageError
        If the value is not an ``OWNER/REPOSITORY`` slug. Every identity
        reaching here is parsed from a command line or a record, both of which
        validate it, so what is left for this to refuse is a value that is not
        a slug at all. The two components are returned unescaped: ``.`` and
        ``..`` among them are refused, and every other character that could
        end a path or open a query is encoded, where the API path is joined
        from them.

    """
    owner, separator, name = repository.partition("/")
    if not stack_records.is_repository_slug(owner, separator, name):
        msg = f"{repository!r} is not an OWNER/REPOSITORY slug"
        raise WheresatUsageError(msg)
    return owner, name


def _path_component(value: str) -> str:
    """Return one component of an API path, percent-encoded.

    A component is encoded before it is joined to the others because ``/``,
    ``?``, and ``#`` are characters a value here may hold and any of them left
    as it was would end the path or open a query — a different request than
    the one asked for. ``quote`` leaves ``.`` alone, so an owner spelled
    ``.github`` survives encoding as itself; a component that is exactly ``.``
    or ``..`` is therefore refused first, because such a component is a step
    of the path rather than a resource at it.

    Parameters
    ----------
    value : str
        One component of a path below the API root.

    Returns
    -------
    str
        The component, with every character that is not a letter, a digit, or
        one of ``-._~`` replaced by its percent-escape.

    Raises
    ------
    WheresatUsageError
        If the component is ``.`` or ``..``, which no endpoint here names a
        resource with and which encoding would pass through unchanged.

    """
    if value in {".", ".."}:
        msg = f"{value!r} is a step of a path, not a component of an API path"
        raise WheresatUsageError(msg)
    return urllib.parse.quote(value, safe="")


def _pull_path(identity: stack_records.PullRequestIdentity) -> tuple[str, ...]:
    """Return the API path of one pull request."""
    owner, name = _slug(identity.repository)
    return "repos", owner, name, "pulls", str(identity.number)


def _commit_pulls_path(repository: str, commit: str) -> tuple[str, ...]:
    """Return the API path of the pull requests associated with one commit."""
    owner, name = _slug(repository)
    return "repos", owner, name, "commits", commit, "pulls"
