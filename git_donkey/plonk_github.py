"""Read-only GitHub completion fallback for git-plonk.

Branch names are not always enough to decide whether work has landed. A branch
called ``fix-markdown-gates`` names no issue and no roadmap task, so no offline
marker can complete it. The authoritative answer is whether a merged pull
request exists for that head branch, which this module asks GitHub for through
the authenticated ``gh`` CLI.

Only read-only queries are made, and every failure fails closed: a missing
tool, a non-zero exit, a timeout, malformed JSON, or a response that names a
different head branch, base branch, or head owner produces ``unknown`` rather
than ``merged``. The caller reports such candidates as unverified and leaves
them in place.

The remote URL is read from raw Git configuration by the caller and parsed
here. Git's ``insteadOf`` rewrites are deliberately not applied: a repository
whose remote is rewritten to a local mirror is still a GitHub repository, and
the fallback must query the repository the user configured.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]  # runs read-only gh
import typing as typ

from git_donkey import observability

_DEFAULT_TIMEOUT_SECONDS = 10.0
_GH_PROMPT_DISABLED_ENV = "GH_PROMPT_DISABLED"
_GH_HOST_ENV = "GH_HOST"
_GH_FIELDS = "number,headRefName,baseRefName,headRepositoryOwner"
_GH_MISSING_STATUS = 127
# A bounded page lets the local identity filter see past same-named fork
# branches without unbounded pagination. 100 is the REST API's maximum page.
_GH_RESULT_LIMIT = 100
_GITHUB_HOST = "github.com"
# GitHub serves SSH from a dedicated host on port 443 for networks that block
# port 22; a remote using it still names the same repository.
_GITHUB_SSH_HOST = "ssh.github.com"
_INVALID_PATH_COMPONENTS = frozenset({".", ".."})
# GitHub owner and repository names are slugs. Restricting them here keeps a
# crafted URL from smuggling option-like text into the `gh` argument list.
_VALID_PATH_COMPONENT = re.compile(r"^[A-Za-z0-9_.-]+$")
_REPOSITORY_NAME = r"(?P<name>[^/]+?)(?:\.git)?"
_OWNER = r"(?P<owner>[^/]+)"
_GITHUB_URL_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        rf"^git@{re.escape(_GITHUB_HOST)}:{_OWNER}/{_REPOSITORY_NAME}$",
        (
            rf"^ssh://git@{re.escape(_GITHUB_HOST)}(?::\d+)?/"
            rf"{_OWNER}/{_REPOSITORY_NAME}$"
        ),
        (
            rf"^ssh://git@{re.escape(_GITHUB_SSH_HOST)}(?::\d+)?/"
            rf"{_OWNER}/{_REPOSITORY_NAME}$"
        ),
        rf"^git://{re.escape(_GITHUB_HOST)}/{_OWNER}/{_REPOSITORY_NAME}$",
        (
            rf"^https?://(?:[^@/]+@)?{re.escape(_GITHUB_HOST)}(?::(?:80|443))?/"
            rf"{_OWNER}/{_REPOSITORY_NAME}/?$"
        ),
    )
)

type _LookupState = typ.Literal["merged", "not_merged", "unknown"]


@dataclasses.dataclass(frozen=True, slots=True)
class _GitHubRepository:
    """GitHub repository named by a configured remote URL.

    Attributes
    ----------
    owner : str
        Account or organization that owns the repository.
    name : str
        Repository name, without a trailing ``.git``.

    """

    owner: str
    name: str


@dataclasses.dataclass(frozen=True, slots=True)
class _PullRequestQuery:
    """One read-only merged-pull-request query.

    Attributes
    ----------
    owner : str
        Owner of the repository to query.
    repository : str
        Repository name to query.
    head_branch : str
        Head branch the pull request must have.
    base_branch : str
        Base branch the pull request must target.

    """

    owner: str
    repository: str
    head_branch: str
    base_branch: str


@dataclasses.dataclass(frozen=True, slots=True)
class _MergedPullRequest:
    """Outcome of one merged-pull-request lookup.

    Attributes
    ----------
    state : _LookupState
        ``merged`` only when a matching pull request was found; ``unknown``
        whenever the answer could not be established.
    number : int | None
        Pull-request number, present only when ``state`` is ``merged``.

    """

    state: _LookupState
    number: int | None = None


class _GhRunner(typ.Protocol):
    """Callable that runs an argument list and returns its completed process."""

    def __call__(
        self, arguments: typ.Sequence[str], /
    ) -> subprocess.CompletedProcess[str]:
        """Run ``arguments`` without a shell and return the completed process."""


class MergedPullRequestLookup(typ.Protocol):
    """Injectable read-only merged-pull-request lookup."""

    def __call__(self, query: _PullRequestQuery, /) -> _MergedPullRequest:
        """Return whether ``query`` names a merged pull request."""


def _is_valid_path_component(value: str) -> bool:
    """Return whether ``value`` is a safe GitHub owner or repository slug."""
    if value in _INVALID_PATH_COMPONENTS or value.startswith("-"):
        return False
    return _VALID_PATH_COMPONENT.fullmatch(value) is not None


def parse_github_remote_url(url: str) -> _GitHubRepository | None:
    """Parse a configured Git remote URL into its GitHub repository.

    The accepted forms are the ones Git itself supports for GitHub: the scp-like
    ``git@github.com:owner/repo.git`` syntax, ``ssh://`` URLs including the
    port-443 ``ssh.github.com`` host, ``git://`` URLs, and ``http(s)://`` URLs
    with or without credentials. Enterprise hosts and any other URL shape are
    rejected rather than guessed at.

    Parameters
    ----------
    url : str
        Raw value of ``remote.<name>.url``, before any ``insteadOf`` rewrite.

    Returns
    -------
    _GitHubRepository | None
        The repository the URL names, or ``None`` when it is not a supported
        GitHub remote URL.

    Examples
    --------
    >>> parse_github_remote_url("git@github.com:leynos/git-donkey.git")
    _GitHubRepository(owner='leynos', name='git-donkey')
    >>> parse_github_remote_url("https://gitlab.com/leynos/git-donkey.git") is None
    True

    """
    candidate = url.strip()
    for pattern in _GITHUB_URL_PATTERNS:
        match = pattern.match(candidate)
        if match is None:
            continue
        owner = match.group("owner")
        name = match.group("name")
        if not (_is_valid_path_component(owner) and _is_valid_path_component(name)):
            return None
        return _GitHubRepository(owner=owner, name=name)
    return None


def gh_available() -> bool:
    """Return whether the ``gh`` executable is on ``PATH``.

    Returns
    -------
    bool
        ``True`` when a ``gh`` executable can be found.

    """
    return shutil.which("gh") is not None


def _gh_arguments(query: _PullRequestQuery) -> list[str]:
    """Return the ``gh pr list`` argument list for ``query``."""
    return [
        "gh",
        "pr",
        "list",
        "--repo",
        f"{query.owner}/{query.repository}",
        "--head",
        query.head_branch,
        "--base",
        query.base_branch,
        "--state",
        "merged",
        "--json",
        _GH_FIELDS,
        "--limit",
        str(_GH_RESULT_LIMIT),
    ]


def _run_gh(arguments: typ.Sequence[str]) -> subprocess.CompletedProcess[str]:
    """Run ``gh`` with prompts disabled and standard input closed.

    ``gh`` is resolved to an absolute path first so the invocation cannot be
    captured by a partially named executable; a missing tool becomes a
    non-zero completed process rather than an exception, because every caller
    treats failure as an unknown answer.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The completed process, real or synthesised for a missing tool.

    """
    executable = shutil.which(arguments[0])
    if executable is None:
        return subprocess.CompletedProcess(
            arguments,
            _GH_MISSING_STATUS,
            "",
            "gh is not installed",
        )
    environment = os.environ.copy()
    # A prompt in a non-interactive sweep would block until the timeout.
    environment[_GH_PROMPT_DISABLED_ENV] = "1"
    # The parsed URL is always github.com, so pin gh to that host rather than
    # letting a configured GitHub Enterprise host reinterpret `owner/name`.
    environment[_GH_HOST_ENV] = _GITHUB_HOST
    # Args are fixed plus local branch names, and shell=False is explicit.
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [executable, *arguments[1:]],
        capture_output=True,
        text=True,
        timeout=_DEFAULT_TIMEOUT_SECONDS,
        check=False,
        stdin=subprocess.DEVNULL,
        env=environment,
    )


def _matching_pull_request_number(
    record: object,
    query: _PullRequestQuery,
) -> int | None:
    """Return the PR number when ``record`` matches every identity field."""
    match record:
        case {
            "number": int() as number,
            "headRefName": str() as head,
            "baseRefName": str() as base,
            "headRepositoryOwner": {"login": str() as login},
        }:
            if isinstance(number, bool) or number <= 0:
                return None
            if head != query.head_branch or base != query.base_branch:
                return None
            if login.casefold() != query.owner.casefold():
                return None
            return number
    return None


def _pull_request_from_payload(
    payload: object,
    query: _PullRequestQuery,
) -> _MergedPullRequest:
    """Interpret a ``gh pr list --json`` payload, failing closed on surprises."""
    if not isinstance(payload, list):
        return _MergedPullRequest("unknown")
    if not payload:
        return _MergedPullRequest("not_merged")
    # The page may contain a fork's same-named branch ahead of the configured
    # owner, so select the first record that matches every identity field
    # instead of rejecting the page on its first row.
    for record in payload:
        number = _matching_pull_request_number(record, query)
        if number is not None:
            return _MergedPullRequest("merged", number)
    return _MergedPullRequest("unknown")


def merged_pull_request(
    query: _PullRequestQuery,
    *,
    run: _GhRunner = _run_gh,
) -> _MergedPullRequest:
    """Ask GitHub whether ``query`` names a merged pull request.

    The query is read-only. Every failure mode maps to ``unknown``: a non-zero
    exit, a timeout or other process error, malformed JSON, or a response whose
    head branch, base branch, or head owner does not match the request. Only a
    response that matches all three is accepted as ``merged``. An empty result
    is ``not_merged``, which is a definitive answer but not completion evidence.

    Parameters
    ----------
    query : _PullRequestQuery
        Repository, head branch, and base branch to look up.
    run : _GhRunner, optional
        Process runner, injected by tests. Defaults to the real ``gh`` CLI.

    Returns
    -------
    _MergedPullRequest
        The lookup outcome.

    """
    with observability.get_recorder().span("github_lookup"):
        result = _perform_lookup(query, run)
    observability.get_recorder().record(
        observability.Observation(
            operation="github_lookup",
            outcome="failure" if result.state == "unknown" else "success",
        )
    )
    return result


def _perform_lookup(
    query: _PullRequestQuery,
    run: _GhRunner,
) -> _MergedPullRequest:
    """Run one ``gh`` query and interpret its response."""
    arguments = _gh_arguments(query)
    try:
        completed = run(arguments)
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        return _MergedPullRequest("unknown")
    if completed.returncode != 0:
        return _MergedPullRequest("unknown")
    try:
        payload = json.loads(completed.stdout)
    except (ValueError, RecursionError):
        # JSONDecodeError is a ValueError; the integer conversion limit and
        # deeply nested input are the other ways malformed output can fail.
        return _MergedPullRequest("unknown")
    return _pull_request_from_payload(payload, query)
