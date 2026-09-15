"""Fetching the parent's head into the durable cache ref, and what a refusal says.

The head is the whole of what a run reasons about the parent's history from, so
every way of ending up without one is a case here, and they are different facts
rather than one: the run was told not to fetch, no remote names the repository
the head lives in, the head moved between the payload and the fetch, and no ref
at the remote holds it. Each is checked for the class the run reports and for
the account the fault gives, because a refusal that named the wrong one of them
would leave an operator with nothing to act on.

Two of the rungs are pinned by fetching for real, from a repository on disk: the
pull request's own ref, which is the head rather than a branch that may since
have moved, and the head branch, which is what a fork carries when it has no
``refs/pull/<N>/head`` of its own. The remote is configured with the URL the
repository has on GitHub, and a ``url.<path>.insteadOf`` rewrite sends the fetch
to the local repository: the URL is what the command reads to decide which
remote holds the head, the rewrite is what makes Git fetch from here, and the
two disagree on purpose — the reader ignores rewrites, which is the property
this suite would otherwise have to take on trust.

The cache is the reason a run that may not use the network can still answer.
Its check comes before ``--no-fetch`` rather than after it, so the first case
here fetches once and then removes the remote: the second run must answer from
what the first brought down, with nothing left to fetch from.

Usage
-----
Run this module directly with pytest::

    python -m pytest -k test_wheresat_parent_head -q
"""

from __future__ import annotations

import pathlib
import typing as typ

from git import Repo

from git_donkey import observability, wheresat_refs, wheresat_writes
from tests import git_repo_helpers
from tests.unit.wheresat_helpers import PR_IDENTITY, PR_REPOSITORY, parent_pull_request

if typ.TYPE_CHECKING:
    from git_donkey.wheresat_records import ParentPullRequest
    from tests.observability_helpers import RecordingRecorder

_FETCH: typ.Final[observability.Operation] = "evidence_fetch"
"""Operation a run's fetch of the parent's head is recorded under."""

_HEAD_URL: typ.Final = f"https://github.com/{PR_REPOSITORY}"
"""URL the working checkout's remote is configured with."""

_ELSEWHERE_URL: typ.Final = "https://github.com/acme/elsewhere"
"""URL naming a repository the head does not live in."""

_PULL_REF: typ.Final = f"refs/pull/{PR_IDENTITY.number}/head"
"""Ref GitHub serves a pull request's head under."""

_BRANCH_REF: typ.Final = f"refs/heads/{parent_pull_request().head_ref}"
"""Ref a fork serves the head branch under, which is the second rung tried."""

_HEAD_NOT_FETCHED: typ.Final = "not fetched"
"""Wording every refusal shares: the run has no head to reason from."""


def _parent(**overrides: object) -> ParentPullRequest:
    """Return the parent pull request as the forge would report it.

    The head is not yet fetched, which is the state the payload is in when the
    fetch begins: ``head_fetched_from`` is what the function under test fills
    in, so a case that left it set would assert nothing about the fetch.

    Parameters
    ----------
    **overrides : object
        Fields to replace, for the cases that change the head or its refs.

    Returns
    -------
    ParentPullRequest
        The payload, with no repository recorded as the head's origin.

    """
    return parent_pull_request(head_fetched_from=None, **overrides)


def _origin(root: pathlib.Path) -> Repo:
    """Return the repository the working checkout fetches the head from."""
    return git_repo_helpers.seed_repo(root / "origin")


def _published(origin: Repo, ref: str) -> str:
    """Commit an empty change, point ``ref`` at it, and return the new ID."""
    commit = git_repo_helpers.advance(origin, message=f"Work on {ref}")
    origin.git.update_ref(ref, commit)
    return commit


def _checkout(root: pathlib.Path, *, url: str = _HEAD_URL) -> Repo:
    """Return a checkout whose remote is named by ``url`` and fetches from ``root``.

    The remote carries the URL the repository has on GitHub, because that URL
    is what the command reads to decide which remote holds the head. The
    rewrite is what makes the fetch itself run against the repository built
    beside this checkout, and it is deliberately invisible to the command: the
    configuration reader ignores ``insteadOf``, so everything below happens as
    it would against GitHub, without a socket.

    Parameters
    ----------
    root : pathlib.Path
        Directory holding the repository the rewrite points the fetch at.
    url : str, optional
        URL the remote is configured with.

    Returns
    -------
    Repo
        The checkout, which has no commit of its own and one remote.

    """
    repo = Repo.init(root / "work")
    git_repo_helpers.configure_repo(repo)
    repo.git.remote("add", "origin", url)
    repo.git.config(f"url.{(root / 'origin').as_posix()}.insteadOf", url)
    return repo


def _cache_ref(repo: Repo) -> str | None:
    """Return the commit the durable cache ref names, if it names one."""
    return wheresat_refs.GitWheresatRefWriter(repo).commit_at(
        wheresat_refs.parent_head_ref(PR_IDENTITY)
    )


def _fetch(
    repo: Repo, parent: ParentPullRequest, *, no_fetch: bool = False
) -> wheresat_writes.ParentHeadFetch:
    """Fetch the head of ``parent`` from ``repo``, or report why there is none."""
    return wheresat_writes.fetch_parent_head(repo, parent, no_fetch=no_fetch)


def test_a_second_run_answers_from_the_cache_with_nothing_left_to_fetch_from(
    tmp_path: pathlib.Path, recording_recorder: RecordingRecorder
) -> None:
    """A head a previous run brought down is used without any transport at all.

    The remote is removed between the two runs, so a second run that reached
    for it would fault rather than answer. That is the whole of why the cache
    check comes before ``--no-fetch``: a run that may not use the network still
    has the evidence an earlier run fetched.
    """
    origin = _origin(tmp_path)
    head = _published(origin, _PULL_REF)
    repo = _checkout(tmp_path)
    parent = _parent(head_sha=head)

    first = _fetch(repo, parent)

    assert first.fault is None, "the pull ref holds the head, so the fetch answers"
    assert first.parent is not None, "a fetch that answered has the payload"
    assert first.parent.head_fetched_from == PR_REPOSITORY, (
        "a fetched head comes from the repository the payload names"
    )
    assert _cache_ref(repo) == head, "the fetch should have cached the head"
    assert recording_recorder.outcomes(_FETCH) == ["success"], (
        "the fetch should be recorded as the answer it was"
    )

    repo.git.remote("remove", "origin")
    second = _fetch(repo, parent, no_fetch=True)

    assert second.fault is None, "the cached head answers with no remote to fetch from"
    assert second.parent is not None, "the cached answer carries the payload too"
    assert second.parent.head_fetched_from == PR_REPOSITORY, (
        "a cached head was fetched from the repository the payload names"
    )
    assert recording_recorder.outcomes(_FETCH) == ["success", "success"], (
        "the cached answer should be recorded, and not as a reduced one"
    )


def test_a_run_that_may_not_fetch_leaves_the_head_unfetched(
    tmp_path: pathlib.Path, recording_recorder: RecordingRecorder
) -> None:
    """``--no-fetch`` whose cache misses reports the flag rather than asking.

    The remote here would answer, so a run that checked the cache and then
    fetched anyway would succeed and fail this test: what is pinned is that the
    flag decides, not that the fetch happened to be impossible.
    """
    origin = _origin(tmp_path)
    head = _published(origin, _PULL_REF)
    repo = _checkout(tmp_path)

    fetched = _fetch(repo, _parent(head_sha=head), no_fetch=True)

    assert fetched.parent is None, "a run that may not fetch has no head"
    assert fetched.fault is not None, "and it has a reason for that"
    assert _HEAD_NOT_FETCHED in fetched.fault, (
        f"the run should report no head; it says {fetched.fault!r}"
    )
    assert "--no-fetch" in fetched.fault, (
        f"the refusal should name the flag that decided it; it says {fetched.fault!r}"
    )
    assert _cache_ref(repo) is None, "a run that may not fetch should cache nothing"
    assert recording_recorder.outcomes(_FETCH) == ["not_requested"], (
        "the run should record that no fetch was asked for"
    )
    assert not recording_recorder.error_kinds(_FETCH), (
        "a run that asked for no fetch has no failure to class"
    )


def test_a_head_in_a_repository_no_remote_names_is_unavailable(
    tmp_path: pathlib.Path, recording_recorder: RecordingRecorder
) -> None:
    """A fetch is aimed by repository, not by whichever remote is configured.

    The configured remote would serve the head if it were asked — the rewrite
    points at a repository that holds it — so refusing here is the run's own
    decision rather than the transport's: fetching the parent's head from a
    remote that names another repository would read a commit as evidence it is
    not.
    """
    origin = _origin(tmp_path)
    head = _published(origin, _PULL_REF)
    repo = _checkout(tmp_path, url=_ELSEWHERE_URL)

    fetched = _fetch(repo, _parent(head_sha=head))

    assert fetched.fault is not None, "no remote names the head's repository"
    assert _HEAD_NOT_FETCHED in fetched.fault, (
        f"the run should report no head; it says {fetched.fault!r}"
    )
    assert PR_REPOSITORY in fetched.fault, (
        f"the refusal should name the repository it looked for; {fetched.fault!r}"
    )
    assert _cache_ref(repo) is None, "nothing should have been fetched"
    assert recording_recorder.outcomes(_FETCH) == ["unavailable"], (
        "the run should record that it had nowhere to fetch from"
    )
    assert not recording_recorder.error_kinds(_FETCH), (
        "nothing failed: the run had no remote to ask"
    )


def test_the_head_is_fetched_from_the_pull_requests_own_ref(
    tmp_path: pathlib.Path,
) -> None:
    """The pull ref answers before the branch, and only the cache ref is written.

    The head branch is given a different commit, so a run that asked the branch
    first would fetch that one and refuse the head as moved. Which of the two
    was fetched is therefore read from the commit the cache ends up holding.
    """
    origin = _origin(tmp_path)
    head = _published(origin, _PULL_REF)
    _published(origin, _BRANCH_REF)
    repo = _checkout(tmp_path)

    fetched = _fetch(repo, _parent(head_sha=head))

    assert fetched.fault is None, "the pull request's own ref holds the head"
    assert _cache_ref(repo) == head, (
        "the pull request's own ref is the one the head came from"
    )
    git_dir = pathlib.Path(str(repo.git.rev_parse("--absolute-git-dir")))
    assert not (git_dir / "FETCH_HEAD").exists(), "the fetch should write no FETCH_HEAD"
    tracked = str(repo.git.for_each_ref("--format=%(refname)", "refs/remotes")).strip()
    assert not tracked, "the fetch should write no remote-tracking ref"


def test_a_fork_head_repository_is_fetched_by_branch(tmp_path: pathlib.Path) -> None:
    """A repository with no pull ref of its own is fetched by head branch.

    A pull request whose head lives in a fork is the case: the fork carries the
    branch, not ``refs/pull/<N>/head``, which exists only at the repository the
    pull request was opened against. Both rungs name the same commit when both
    answer, and the payload's ``head_sha`` is what either is held to.
    """
    origin = _origin(tmp_path)
    head = _published(origin, _BRANCH_REF)
    repo = _checkout(tmp_path)

    fetched = _fetch(repo, _parent(head_sha=head))

    assert fetched.fault is None, "the head branch is the second rung, and answers"
    assert _cache_ref(repo) == head, "the head branch should have been fetched"


def test_a_head_that_moved_is_refused(
    tmp_path: pathlib.Path, recording_recorder: RecordingRecorder
) -> None:
    """A fetched commit the payload does not name is refused, not reasoned from.

    The boundary the run is about to establish would otherwise be a boundary
    for a commit the pull request no longer names, which is the one thing the
    fetch is held to prevent.
    """
    origin = _origin(tmp_path)
    head = _published(origin, _PULL_REF)
    reported = git_repo_helpers.advance(origin, message="The head the payload names")
    repo = _checkout(tmp_path)

    fetched = _fetch(repo, _parent(head_sha=reported))

    assert fetched.parent is None, "a head that moved is not the reported one"
    assert fetched.fault is not None, "and the run says so"
    assert "moved" in fetched.fault, (
        f"the refusal should say the head moved; it says {fetched.fault!r}"
    )
    assert f"{PR_REPOSITORY}#{PR_IDENTITY.number}" in fetched.fault, (
        f"the refusal should name the pull request; it says {fetched.fault!r}"
    )
    assert reported in fetched.fault, (
        f"the refusal should name the commit the pull request reports; "
        f"it says {fetched.fault!r}"
    )
    assert head in fetched.fault, (
        f"the refusal should name the commit it fetched; it says {fetched.fault!r}"
    )
    assert recording_recorder.outcomes(_FETCH) == ["failure"], (
        "a fetch that obtained the wrong commit failed"
    )
    assert recording_recorder.error_kinds(_FETCH) == ["github_api_error"], (
        "a head that moved is the pull request's own class of failure"
    )


def test_a_head_no_ref_holds_reports_every_attempt(
    tmp_path: pathlib.Path, recording_recorder: RecordingRecorder
) -> None:
    """Both rungs are tried, and the refusal carries what each of them reported.

    A remote holding neither ref is what an operator sees when a fork was
    deleted or a branch renamed: the fault has to be enough to tell that from a
    network failure, so each attempt's own words are kept.
    """
    origin = _origin(tmp_path)
    head = git_repo_helpers.advance(origin, message="The head the payload names")
    repo = _checkout(tmp_path)

    fetched = _fetch(repo, _parent(head_sha=head))

    assert fetched.fault is not None, "no ref at the remote holds the head"
    assert _HEAD_NOT_FETCHED in fetched.fault, (
        f"the run should report no head; it says {fetched.fault!r}"
    )
    assert _PULL_REF in fetched.fault, (
        f"the refusal should name the first ref tried; it says {fetched.fault!r}"
    )
    assert _BRANCH_REF in fetched.fault, (
        f"the refusal should name the second ref tried; it says {fetched.fault!r}"
    )
    assert _cache_ref(repo) is None, "a fetch that failed leaves no cached head"
    assert recording_recorder.outcomes(_FETCH) == ["failure"], (
        "the run should record that the fetch failed"
    )
    assert recording_recorder.error_kinds(_FETCH) == ["git_command_error"], (
        "a refused fetch is a Git failure rather than an API one"
    )


def test_a_stale_cached_head_is_replaced_rather_than_reported(
    tmp_path: pathlib.Path,
) -> None:
    """A cache holding another commit is refetched, not read as the answer.

    A pull request whose head moved between two runs leaves the cache holding
    the commit the first run saw. Answering with it would make the second run's
    boundary a commit the pull request no longer names.
    """
    origin = _origin(tmp_path)
    stale = _published(origin, _PULL_REF)
    repo = _checkout(tmp_path)
    wheresat_refs.GitWheresatRefWriter(repo).fetch_evidence(
        "origin", _PULL_REF, wheresat_refs.parent_head_ref(PR_IDENTITY)
    )
    assert _cache_ref(repo) == stale, "the cache should start holding the stale head"

    head = _published(origin, _PULL_REF)
    fetched = _fetch(repo, _parent(head_sha=head))

    assert fetched.fault is None, (
        "the moved head is at the remote, so the fetch answers"
    )
    assert _cache_ref(repo) == head, "the stale cache entry should have been replaced"
