"""REQ-parent-pr replayed: the parent metadata comes from GitHub's own answers.

``git wheresat`` reads a parent pull request through one adapter, and every
fact the gates weigh — whether it merged, which commit it landed as, which refs
it was made of, and whether GitHub records it in a stack — is read from the
fields that adapter maps. This module checks that mapping against recordings of
the real API rather than against a double, because the mapping's whole job is
to be right about somebody else's data: a payload that renames a field, or
reports merge state as ``null`` where a boolean was expected, is invisible to a
double written by the same hand as the reader.

The recordings are of this repository's own traffic where that is enough — the
merged squash pull request #93 and the open pull request #82 — and of
``microsoft/vscode`` where it is not. No repository here has a native GitHub
stack, so the one recording that covers the ``stack`` field and the pull
request below it is of a stacked pull request there, at a position the
recording and the test both name. Each was recorded once against live traffic
with the ``Authorization`` header filtered out, and none is edited by hand.

The recordings replay in ``none`` record mode, so a request one of them does
not hold fails inside the adapter rather than reaching the network: every
answer asserted here is one GitHub really gave, and no test can pass by asking
a question the recording never saw.

The refusal is recorded too, and is the one recording that cannot be refreshed
for free: GitHub answers ``403`` with an exhausted allowance, so making it
again costs one endpoint's whole allowance for the minute. It is kept in its
own cassette for that reason, and the test that replays it asserts the fact
that tells a rate limit from a missing scope — the two ``403``s an operator has
to act on differently.

Usage
-----
Replay the recordings, which is what the suite does::

    python -m pytest tests/integration/test_wheresat_github.py -q

Record them again, which needs a credential and reaches the network::

    env -u GH_TOKEN GITHUB_TOKEN="$(env -u GH_TOKEN gh auth token)" \
      python -m pytest tests/integration/test_wheresat_github.py \
      --record-mode=once -q

``GH_TOKEN`` is unset in both places deliberately: an injected ``GH_TOKEN``
shadows the stored ``gh`` session, so ``gh auth token`` prints that shadowing
value, and unsetting it only for the test process would leave the substitution
reading the wrong token. See ``docs/developers-guide.md`` for the whole
procedure, including what the refusal recording costs.

"""

from __future__ import annotations

import os
import typing as typ

import github3.session as github3_session
import pytest

from git_donkey import stack_records
from git_donkey.wheresat_errors import WheresatGitHubError
from git_donkey.wheresat_github import (
    REQUEST_TIMEOUT_SECONDS,
    ApiWheresatGitHub,
)
from tests.integration.conftest import _RECORDER_HEADERS

if typ.TYPE_CHECKING:
    from vcr.cassette import Cassette

REPOSITORY: str = "leynos/git-donkey"
"""Repository the merged, open, and association recordings were made against."""

MERGED: stack_records.PullRequestIdentity = stack_records.PullRequestIdentity(
    repository=REPOSITORY,
    number=93,
)
"""Pull request GitHub recorded as squash-merged, and the squash it landed as."""

OPEN: stack_records.PullRequestIdentity = stack_records.PullRequestIdentity(
    repository=REPOSITORY,
    number=82,
)
"""Pull request GitHub had not merged when the recording was made."""

SQUASH_COMMIT: str = "02ab4e9e1bcb6ec498f2d4dde6e1a7df2d897c41"
"""Commit the squash merge of :data:`MERGED` created on ``main``."""

MERGED_HEAD: str = "cb813a96c4e2cd494a7df4aaa279b71b5765cea5"
"""Branch tip :data:`MERGED` reports, which is not the commit it landed as."""

MERGED_HEAD_REF: str = "markdown-formatting-baseline"
"""Branch :data:`MERGED` was opened from."""

OPEN_HEAD_REF: str = "git-wheresat-sub-command"
"""Branch :data:`OPEN` was opened from."""

STACKED: stack_records.PullRequestIdentity = stack_records.PullRequestIdentity(
    repository="microsoft/vscode",
    number=335346,
)
"""Pull request GitHub records at the second position of a four-deep stack."""

STACK_PARENT: stack_records.PullRequestIdentity = stack_records.PullRequestIdentity(
    repository="microsoft/vscode",
    number=335345,
)
"""Pull request immediately below :data:`STACKED`, which only the stack names."""

SEARCH_QUERY: str = "repo:leynos/git-donkey filename:pyproject.toml"
"""Query whose answer the refusal recording holds, and which it refuses."""

RATE_LIMITED_URL: str = "https://api.github.com/search/code"
"""Request the refusal recording answers, for the assertion that it is named."""


def _session() -> github3_session.GitHubSession:
    """Return a session for the recordings, authenticated only when asked to be.

    The credential is read from the environment and never invented: recording
    needs a real token and replaying needs none, so a session built without one
    is what lets the suite run where no credential exists. Nothing about the
    header reaches a recording — it is filtered out of every one of them — and
    replay matches on the request's method and URL, so a replayed run is the
    same run with or without a token.

    Returns
    -------
    github3.session.GitHubSession
        The session, carrying the media type GitHub's answers were recorded
        with, and a credential when the environment holds one.

    """
    session = github3_session.GitHubSession(
        default_connect_timeout=REQUEST_TIMEOUT_SECONDS,
        default_read_timeout=REQUEST_TIMEOUT_SECONDS,
    )
    session.headers.update({"Accept": "application/vnd.github+json"})
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        session.token_auth(token)
    return session


def _asked(cassette: Cassette, path: str) -> None:
    """Assert the recording holds the question an answer was read from.

    The suite replays in ``none`` record mode, so a request a recording does
    not hold fails inside the adapter rather than reaching the network: an
    answer asserted against a recording is an answer GitHub really gave. This
    names the question each answer came from, so a test that started asking a
    different one is reported as the change it is rather than silently
    replaying a stale answer.

    Parameters
    ----------
    cassette : Cassette
        Recording the answer was read from.
    path : str
        Part of the request's URL that identifies the question.

    """
    asked = [request.uri for request in cassette.requests if path in request.uri]
    assert asked, (
        f"expected the recording to hold a request for {path}, got "
        f"{[request.uri for request in cassette.requests]}"
    )


def _response_header_names(cassette: Cassette) -> set[str]:
    """Return every header name the recording's responses carry, in lower case.

    Parameters
    ----------
    cassette : Cassette
        Recording to read, as the recorder has already filtered it.

    Returns
    -------
    set[str]
        The names, folded so that a header is recognised however it is cased.

    """
    return {
        name.lower() for response in cassette.responses for name in response["headers"]
    }


@pytest.fixture
def github() -> ApiWheresatGitHub:
    """Return the GitHub adapter, over a session the recordings were made with.

    The adapter is built here rather than opened through
    :func:`git_donkey.wheresat_github.open_github`, because replaying a request
    needs no credential and the opener's whole remaining job is to find one.
    That the opener finds a credential, and refuses without one, is stated
    where it lives: ``tests/unit/test_wheresat_github_faults.py``.

    Returns
    -------
    ApiWheresatGitHub
        The adapter every test in this module asks its questions through.

    """
    return ApiWheresatGitHub(_session())


def test_parent_metadata_contract(
    github: ApiWheresatGitHub,
    wheresat_parent_metadata_cassette: Cassette,
) -> None:
    """Every field of the parent record is read from GitHub's recorded answer.

    The pull request is this repository's own, squash-merged: GitHub names the
    branch tip under ``head.sha`` and the commit created on the base branch
    under ``merge_commit_sha``, and the two are different commits. That
    difference is the fact the whole feature turns on — the boundary a child is
    looking for is the parent head, not the squash that replaced it — so the
    contract is asserted with both IDs rather than with a flag.
    """
    metadata = github.pull_request(MERGED)

    _asked(wheresat_parent_metadata_cassette, "/pulls/93")
    assert metadata.identity == MERGED, (
        "the answer is about the pull request asked about"
    )
    assert metadata.merged is True, "a merged pull request reads as merged"
    assert metadata.merged_at == "2026-09-15T15:57:54Z", (
        "and carries the moment GitHub recorded the merge"
    )
    assert metadata.landed == SQUASH_COMMIT, (
        "the commit it landed as is the squash GitHub created on the base branch"
    )
    assert metadata.head_sha == MERGED_HEAD, "and the head is the branch tip instead"
    assert metadata.head_sha != metadata.landed, (
        "a squash merge lands a commit the pull request head does not name"
    )
    assert metadata.head_ref == MERGED_HEAD_REF, (
        "the head ref is read from its repository"
    )
    assert metadata.head_repository == REPOSITORY, "which is this repository"
    assert metadata.base_ref == "main", "and the base ref is read the same way"
    assert metadata.base_repository == REPOSITORY, "from the base's own repository"
    assert metadata.head_fetched_from is None, (
        "no fetch has happened, so nothing claims the head came from anywhere"
    )
    assert metadata.stacked is False, (
        "and GitHub records no stack for this pull request"
    )


def test_an_open_pull_request_names_nothing_landed(
    github: ApiWheresatGitHub,
    wheresat_parent_metadata_cassette: Cassette,
) -> None:
    """An unmerged pull request reads as unmerged, whatever GitHub names.

    GitHub answers an unmerged pull request with a ``merge_commit_sha`` all the
    same — a synthetic test-merge commit, which AXIOM-6 warns is not an
    integration commit — so the field cannot be read as what the pull request
    landed as. The recording is of an open pull request in this repository, and
    what it proves is that the flag, not the commit, decides.
    """
    metadata = github.pull_request(OPEN)

    _asked(wheresat_parent_metadata_cassette, "/pulls/82")
    assert metadata.merged is False, "an open pull request is not merged"
    assert metadata.merged_at is None, "and GitHub recorded no moment for it"
    assert metadata.landed is None, (
        "so the commit GitHub still names is not reported as what it landed as"
    )
    assert metadata.head_ref == OPEN_HEAD_REF, "the head ref is read all the same"
    assert metadata.base_ref == "main", "and so is the base"
    assert metadata.stacked is False, "and it is not part of a stack either"


def test_a_stacked_pull_request_names_the_one_below_it(
    github: ApiWheresatGitHub,
    wheresat_parent_metadata_cassette: Cassette,
) -> None:
    """The pull request below a stacked one is read from the stack, not the pull.

    GitHub's pull request payload says which stack a pull request belongs to
    and where it sits in that stack, and names no neighbour: the parent is in
    the stack's own list, ordered from the bottom up. The recording is of a
    pull request at the second position of a four-deep stack, so the answer is
    the first entry. The pull request GitHub records no stack for is asked
    about too, and the recording holds no stack question about it: a reader
    that went looking for one would fail here rather than answer from
    somewhere else.
    """
    metadata = github.pull_request(STACKED)
    parent = github.stack_parent(STACKED)
    unstacked = github.stack_parent(MERGED)

    _asked(wheresat_parent_metadata_cassette, "/stacks?pull_request=335346")
    assert metadata.stacked is True, "GitHub records this pull request in a stack"
    assert parent == STACK_PARENT, (
        "and the pull request below it is read from the stack's own ordering"
    )
    assert unstacked is None, "a pull request no stack holds has nothing below it"
    assert not [
        request.uri
        for request in wheresat_parent_metadata_cassette.requests
        if "/stacks?pull_request=93" in request.uri
    ], "and no stack was asked about, so the answer above cost no request"


def test_the_association_search_names_the_pull_request(
    github: ApiWheresatGitHub,
    wheresat_parent_metadata_cassette: Cassette,
) -> None:
    """A commit's pull requests are read from the page GitHub answers with.

    The commit is the squash merge of :data:`MERGED`, which GitHub associates
    with that pull request and no other. One commit examined and nothing left
    over is the shape a search has when it saw the whole history, which is what
    the rung above reads before it treats an empty answer as an answer.
    """
    page = github.associated_pull_requests(REPOSITORY, [SQUASH_COMMIT])

    _asked(wheresat_parent_metadata_cassette, f"/commits/{SQUASH_COMMIT}/pulls")
    assert page.associations == {SQUASH_COMMIT: (MERGED,)}, (
        "the commit is associated with the pull request it was squashed from"
    )
    assert page.commits_examined == 1, (
        "and the search examined the one commit it was given"
    )
    assert page.truncated is False, (
        "a search with nothing left over did not stop short of the history"
    )


def test_a_recording_does_not_say_which_client_made_the_requests(
    wheresat_parent_metadata_cassette: Cassette,
    wheresat_rate_limited_cassette: Cassette,
) -> None:
    """GitHub's description of the client and its access reaches no test.

    ``filter_headers`` drops a request header and nothing more, so the three
    headers GitHub answers with — ``x-oauth-client-id``, ``x-oauth-scopes``,
    and ``x-accepted-oauth-scopes`` — are dropped as each recording is read.
    None of the three is a credential, and none is read by any test, but
    together they name who recorded the traffic, which a recording a reader can
    check into a repository has no reason to keep. The recordings on disk still
    carry them, because a recording is not edited by hand; the pass that
    refreshes one writes a file that never held them.
    """
    for described, cassette in {
        "the parent metadata recording": wheresat_parent_metadata_cassette,
        "the rate-limited recording": wheresat_rate_limited_cassette,
    }.items():
        present = _response_header_names(cassette) & set(_RECORDER_HEADERS)

        assert not present, (
            f"{described} must state neither the client the requests were made "
            f"with nor the access it was granted, but carries {sorted(present)}"
        )


def test_a_rate_limited_answer_is_not_an_answer(
    github: ApiWheresatGitHub,
    wheresat_rate_limited_cassette: Cassette,
) -> None:
    """A refusal is reported as a question that went unanswered, and as which.

    A ``403`` means a rate limit or a missing scope, and an operator answers
    the two in opposite ways: one is answered by waiting and the other never
    is. What tells them apart is the exhausted remaining count or a
    ``Retry-After``, and the recording is of the first — GitHub refusing a
    request whose endpoint allowance the credential had spent. The test asserts
    the refusal names the rate limit *and* that it does not claim the other
    cause, so a reader that guessed either way fails one of the two.
    """
    with pytest.raises(WheresatGitHubError) as refusal:
        github.get("search", "code", params={"q": SEARCH_QUERY})

    _asked(wheresat_rate_limited_cassette, "/search/code")
    message = str(refusal.value)
    assert "rate limited" in message, f"expected a rate limit, got: {message}"
    assert RATE_LIMITED_URL in message, (
        f"expected the refused request to be named, got: {message}"
    )
    assert "HTTP 403" in message, f"expected the status to be reported, got: {message}"
    assert "scopes" not in message, (
        f"a spent allowance is not a missing scope, got: {message}"
    )
