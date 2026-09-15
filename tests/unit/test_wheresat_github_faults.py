"""INV-5 at the GitHub boundary: a fault is never read as an answer.

Seven ways a request can fail to produce an answer — HTTP 401, a rate-limited
403, a forbidden 403, 404, a server error, a connection timeout, and a name
that will not resolve — are one class, because the run does one thing with all
of them: the question went unanswered, so the gate that asked it is
indeterminate and the run exits ``3``. Each is driven through the same session
stub. The two ``403``s are kept apart on purpose, because they are the pair an
operator has to act on differently — one is answered by waiting and the other
never is — so the forbidden case asserts that its refusal does _not_ claim a
rate limit.

Every case also holds that the refusal names the request it belongs to. A
mutant that raised the right class with the wrong message would otherwise
satisfy the suite, and the message is the whole of what an operator has to
work with when the run reports that it could not tell.

The other half of this boundary is the one failure whose class is a usage
failure and whose result is not. A missing credential is raised without asking
a terminal, because a command that answered a question about a repository by
blocking on a browser would be unusable from the scripts it exists for, and the
refusal keeps the usage class so that it reads as a failure to start. What the
run reports it as is decided one layer up, where the ladder that opens the
forge names this class ahead of its parent: the question went unanswered, so
the result is indeterminate with status ``3`` — Table 3's row — and the refusal
names ``--offline`` as the way to ask GitHub nothing at all.

Usage
-----
Run this module directly with pytest::

    python -m pytest -k test_wheresat_github_faults -q
"""

from __future__ import annotations

import dataclasses
import sys
import time
import typing as typ

import github3.session as github3_session
import pytest
import requests

from git_donkey import github_credentials, stack_records
from git_donkey.wheresat_errors import (
    WheresatCredentialError,
    WheresatGitHubError,
    WheresatUsageError,
)
from git_donkey.wheresat_github import (
    NETWORK_BUDGET_SECONDS,
    ApiWheresatGitHub,
    open_github,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

_IDENTITY = stack_records.PullRequestIdentity(repository="leynos/git-donkey", number=80)
_URL = "https://api.github.com/repos/leynos/git-donkey/pulls/80"
_COMMIT = "0e1d2c3b4a5968778695a4b3c2d1e0f1a2b3c4d5"
_RATE_LIMIT_HEADERS: typ.Final = {
    "Retry-After": "60",
    "X-RateLimit-Remaining": "0",
    "X-RateLimit-Reset": "1757850000",
}
_DNS_MESSAGE = (
    "HTTPSConnectionPool(host='api.github.com', port=443): Max retries exceeded "
    "with url: /repos/leynos/git-donkey/pulls/80 (Caused by "
    "NameResolutionError: Failed to resolve api.github.com "
    "[Errno -2] Name or service not known)"
)
_UNREADABLE_BODY = (
    "Expecting value: line 1 column 1 (char 0): a proxy answered with an HTML "
    "error page where the adapter expected JSON"
)


@dataclasses.dataclass(frozen=True, slots=True)
class _StubResponse:
    """An answer's status and body, or the body it will not decode.

    One class covers both, because the difference between an answer the
    adapter can read and an answer it cannot is a case's choice of contents
    rather than a different shape of answer: a ``200`` carrying a JSON body
    and a ``200`` carrying a proxy's HTML error page are the same object with
    different fields set.

    Parameters
    ----------
    status_code : int
        Status the answer carries.
    body : object, optional
        Decoded body the answer carries, ignored when ``undecodable`` is set.
    headers : collections.abc.Mapping[str, str], optional
        Headers the answer carries.
    undecodable : str, optional
        Message :meth:`json` raises with, for an answer whose status promised
        a body that is not JSON. The empty string means the body is readable.

    """

    status_code: int
    body: object = None
    headers: cabc.Mapping[str, str] = dataclasses.field(default_factory=dict)
    undecodable: str = ""

    def json(self) -> object:
        """Return the decoded body, or refuse the one this answer cannot decode."""
        if self.undecodable:
            raise ValueError(self.undecodable)
        return self.body


@dataclasses.dataclass(slots=True)
class _StubSession:
    """A session whose one request answers with whatever a case gave it.

    ``calls`` records the URLs asked for, in order, so a case can hold that the
    fault came from the request it meant to make rather than from a retry, a
    second endpoint, or a probe the adapter had no business making.
    """

    answer: object
    calls: list[str] = dataclasses.field(default_factory=list)

    def get(
        self,
        url: str,
        *,
        params: cabc.Mapping[str, str] | None = None,
        timeout: float | None = None,
        headers: cabc.Mapping[str, str] | None = None,
    ) -> object:
        """Return the stubbed answer, or raise the stubbed failure."""
        self.calls.append(url)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@dataclasses.dataclass(frozen=True, slots=True)
class _Fault:
    """One way a question goes unanswered, and what its refusal must say."""

    name: str
    answer: object
    reason: str
    forbidden: str = ""


_FAULTS: typ.Final = (
    _Fault(
        "unauthorized",
        _StubResponse(401, body={"message": "Bad credentials"}),
        "rejected the credential",
    ),
    _Fault(
        "rate-limited",
        _StubResponse(
            403,
            headers=_RATE_LIMIT_HEADERS,
            body={"message": "API rate limit exceeded"},
        ),
        "rate limited",
    ),
    _Fault(
        "forbidden",
        _StubResponse(
            403,
            headers={"X-RateLimit-Remaining": "4999"},
            body={"message": "Resource not accessible by integration"},
        ),
        "refused the request",
        forbidden="rate limited",
    ),
    _Fault(
        "not-found",
        _StubResponse(404, body={"message": "Not Found"}),
        "not evidence that the pull request",
    ),
    _Fault(
        "server-error",
        _StubResponse(500, body={"message": "Server Error"}),
        "server error",
    ),
    _Fault(
        "timeout",
        requests.exceptions.ReadTimeout("HTTPSConnectionPool: Read timed out"),
        "did not answer",
    ),
    _Fault(
        "unresolvable",
        requests.exceptions.ConnectionError(_DNS_MESSAGE),
        "could not be reached",
    ),
)


def _client(
    session: _StubSession,
    clock: cabc.Callable[[], float] = time.monotonic,
) -> ApiWheresatGitHub:
    """Return an adapter whose transport is the stub passed in.

    The cast is the one place this module says what its stub stands in for.
    The adapter asks a session for exactly one thing, ``get``, and every other
    attribute of ``requests.Session`` is surface this boundary never touches.

    Parameters
    ----------
    session : _StubSession
        Transport the adapter will make its requests through.
    clock : collections.abc.Callable[[], float], optional
        Clock the association search measures its budget with, so a case can
        run the budget out without waiting a minute.

    Returns
    -------
    ApiWheresatGitHub
        The adapter under test.

    """
    return ApiWheresatGitHub(typ.cast("requests.Session", session), clock)


def _ask_for_the_pull_request(client: ApiWheresatGitHub) -> object:
    """Ask what a pull request merged as."""
    return client.pull_request(_IDENTITY)


def _ask_for_the_body(client: ApiWheresatGitHub) -> object:
    """Ask for a pull request body."""
    return client.pull_request_body(_IDENTITY)


def _ask_for_the_neighbour(client: ApiWheresatGitHub) -> object:
    """Ask which pull request sits below this one in a stack."""
    return client.stack_parent(_IDENTITY)


def _ask_for_associations(client: ApiWheresatGitHub) -> object:
    """Ask which pull requests a commit belongs to."""
    return client.associated_pull_requests(_IDENTITY.repository, (_COMMIT,))


_QUESTIONS: typ.Final[tuple[cabc.Callable[[ApiWheresatGitHub], object], ...]] = (
    _ask_for_the_pull_request,
    _ask_for_the_body,
    _ask_for_the_neighbour,
    _ask_for_associations,
)


@pytest.mark.parametrize("fault", _FAULTS, ids=lambda fault: fault.name)
def test_a_fault_is_refused_rather_than_answered(fault: _Fault) -> None:
    """Each way GitHub fails to answer raises one class, naming the request."""
    session = _StubSession(fault.answer)

    with pytest.raises(WheresatGitHubError) as raised:
        _client(session).pull_request(_IDENTITY)

    message = str(raised.value)
    assert fault.reason in message, (
        f"{fault.name}: the refusal should say {fault.reason!r}; it says {message!r}"
    )
    assert _URL in message, (
        f"{fault.name}: the refusal should name the request; it says {message!r}"
    )
    assert session.calls == [_URL], (
        f"{fault.name}: the fault should come from the one request; "
        f"the adapter made {session.calls!r}"
    )
    if fault.forbidden:
        assert fault.forbidden not in message, (
            f"{fault.name}: the refusal should not say {fault.forbidden!r}; "
            f"it says {message!r}"
        )


@pytest.mark.parametrize("fault", _FAULTS, ids=lambda fault: fault.name)
def test_every_question_refuses_a_fault_the_same_way(fault: _Fault) -> None:
    """The classification belongs to the transport, not to one question."""
    for question in _QUESTIONS:
        session = _StubSession(fault.answer)
        with pytest.raises(WheresatGitHubError):
            question(_client(session))


def test_a_body_that_is_not_json_is_a_fault() -> None:
    """A status of 200 with an unreadable body answered nothing."""
    session = _StubSession(_StubResponse(200, undecodable=_UNREADABLE_BODY))

    with pytest.raises(WheresatGitHubError) as raised:
        _client(session).pull_request(_IDENTITY)

    assert "not JSON" in str(raised.value), (
        f"the refusal should say the body is unreadable; it says {raised.value!r}"
    )


def test_a_reachable_host_that_never_answers_is_a_timeout() -> None:
    """A connect timeout is reported as a timeout, not as an unreachable host.

    ``requests`` makes its connect timeout a subclass of both the timeout and
    the connection failure, so the two handlers are decided by their order.
    Which one wins is the operator's whole diagnosis: one says GitHub is slow,
    the other says the network is wrong.
    """
    session = _StubSession(requests.exceptions.ConnectTimeout("connect timed out"))

    with pytest.raises(WheresatGitHubError) as raised:
        _client(session).pull_request(_IDENTITY)

    message = str(raised.value)
    assert "did not answer" in message, (
        f"a connect timeout should read as an unanswered request; it says {message!r}"
    )
    assert "could not be reached" not in message, (
        f"a connect timeout should not read as a bad host; it says {message!r}"
    )


def test_an_association_page_reports_what_it_examined() -> None:
    """The bound is reported rather than applied silently."""
    answer = _StubResponse(200, body=[{"number": 82}, {"number": 83}])
    client = _client(_StubSession(answer))

    page = client.associated_pull_requests("leynos/git-donkey", (_COMMIT,))

    assert page.commits_examined == 1, "the one commit should have been examined"
    assert page.truncated is False, "nothing was left over to examine"
    assert page.associations[_COMMIT] == (
        stack_records.PullRequestIdentity(repository="leynos/git-donkey", number=82),
        stack_records.PullRequestIdentity(repository="leynos/git-donkey", number=83),
    ), "both associated pull requests should be named, in GitHub's order"


def test_a_search_that_runs_out_of_time_says_so() -> None:
    """A search the budget stops reports truncation rather than a short answer.

    The clock is a parameter for exactly this case: the alternative is a test
    that waits a minute to watch a deadline pass.
    """
    answer = _StubResponse(200, body=[{"number": 82}])
    ticks = iter((0.0, NETWORK_BUDGET_SECONDS + 1.0))
    client = _client(_StubSession(answer), clock=lambda: next(ticks))

    page = client.associated_pull_requests("leynos/git-donkey", (_COMMIT,))

    assert page.commits_examined == 0, "no commit should have been asked about"
    assert page.truncated is True, "the search should say it stopped short"


def test_a_slug_component_of_dots_is_refused_before_the_transport() -> None:
    """A ``..`` component is refused, and the transport is never reached.

    ``is_repository_slug`` asks only that a slug be two non-empty components,
    so ``../git-donkey`` is a slug by that rule; it is the encoder that knows
    ``..`` names a step of the path rather than a resource at it. The refusal
    must come before the request, or the value would already have been put
    into a URL the transport was handed.
    """
    repository = "../git-donkey"
    session = _StubSession(_StubResponse(200, body={}))

    with pytest.raises(WheresatUsageError) as raised:
        _client(session).pull_request(
            stack_records.PullRequestIdentity(repository=repository, number=80)
        )

    assert "'..'" in str(raised.value), (
        f"{repository!r}: the refusal should name the offending component; "
        f"it says {raised.value!r}"
    )
    assert not session.calls, (
        f"{repository!r}: a traversing component should never reach the "
        f"transport; the adapter asked for {session.calls!r}"
    )


def test_an_encoded_component_cannot_open_a_query() -> None:
    """A component holding ``?`` is encoded, not read as a separator.

    The slug's owner is a whole path component, so a ``?`` left unencoded
    would end the path there and turn everything after it into a query — a
    request for a different resource than the one asked for. The URL the
    transport receives is the encoded one, and it carries no query.
    """
    repository = "a?b/c"
    session = _StubSession(_StubResponse(200, body={}))

    _client(session).pull_request(
        stack_records.PullRequestIdentity(repository=repository, number=80)
    )

    urls = list(session.calls)
    assert urls == ["https://api.github.com/repos/a%3Fb/c/pulls/80"], (
        f"{repository!r}: the request should carry the encoded component; "
        f"the adapter asked for {urls!r}"
    )
    assert "?" not in urls[0], (
        f"{repository!r}: the '?' should be encoded rather than open a query; "
        f"the adapter asked for {urls[0]!r}"
    )


def test_a_legal_slug_is_asked_for_as_written() -> None:
    """A slug whose owner holds a dot is unchanged by the encoding.

    ``.github`` is a legal owner name, so the encoder must leave a dot as it
    found it and refuse only a component that is nothing but dots. This is
    the case that pins the hardening against having altered valid input.
    """
    repository = ".github/git-donkey"
    session = _StubSession(_StubResponse(200, body={}))

    _client(session).pull_request(
        stack_records.PullRequestIdentity(repository=repository, number=80)
    )

    assert session.calls == [
        "https://api.github.com/repos/.github/git-donkey/pulls/80"
    ], (
        f"{repository!r}: a legal slug should be asked for as written; "
        f"the adapter asked for {session.calls!r}"
    )


def _without_credential(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """Leave the run with no credential to find and a terminal it could use."""
    for variable in ("GITHUB_TOKEN", "GH_TOKEN"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv(github_credentials.CREDENTIALS_FILE_ENV, str(path))
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)


def _authenticated_with(client: ApiWheresatGitHub) -> str:
    """Return the token a client's session authenticates its requests with."""
    auth = client.session.auth
    assert isinstance(auth, github3_session.TokenAuth), (
        "the session should be carrying a token rather than nothing"
    )
    return auth.token


def test_a_missing_credential_is_refused_without_a_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """No credential is refused, naming every source, with a terminal attached.

    The refusal is asserted whole rather than by fragment, because it is the
    operator's only account of a run that could not ask the forge anything: it
    has to name both environment variables, the cache it looked in, the reason
    it did not ask a human, and the flag that asks GitHub nothing. The path is
    resolved here exactly as the reader resolves it, so the comparison is exact
    without being machine-dependent. The class stays the usage one because the
    run's result is decided above this boundary: the ladder that opens the forge
    reads the refusal as an unanswered question and reports ``3``.
    """
    path = (tmp_path / "absent-token").resolve()
    _without_credential(monkeypatch, path)

    with pytest.raises(WheresatCredentialError) as raised:
        open_github()

    assert isinstance(raised.value, WheresatUsageError), (
        "the refusal is worded as a failure to start, so it keeps the usage class"
    )
    assert str(raised.value) == (
        "no GitHub credential: set GITHUB_TOKEN or GH_TOKEN, or run git fafo "
        f"to authorize one and cache it in {path}; git wheresat never prompts, "
        "and --offline asks nothing of GitHub at all"
    ), "the refusal should name every source it tried, why it did not ask, and the flag"


def test_the_environment_is_read_before_the_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A token in the environment wins over a cached one."""
    path = tmp_path / "token"
    github_credentials.write_token(path, "cached-token", None)
    monkeypatch.setenv(github_credentials.CREDENTIALS_FILE_ENV, str(path))
    monkeypatch.setenv("GITHUB_TOKEN", "environment-token")

    client = open_github()

    assert _authenticated_with(client) == "environment-token", (
        "the environment should take precedence over the cache"
    )


def test_the_cached_credential_is_read_when_the_environment_has_none(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The token git fafo cached is one a later run can find."""
    path = tmp_path / "token"
    github_credentials.write_token(path, "cached-token", None)
    monkeypatch.setenv(github_credentials.CREDENTIALS_FILE_ENV, str(path))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    client = open_github()

    assert _authenticated_with(client) == "cached-token", (
        "the cached token should be the one the session authenticates with"
    )
