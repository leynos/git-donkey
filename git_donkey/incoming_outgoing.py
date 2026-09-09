"""Mercurial-style incoming and outgoing branch comparison workflows.

The ``git-incoming`` and ``git-outgoing`` commands answer the two questions a
developer asks before syncing with a shared branch: which commits would arrive
on a pull, and which would leave on a push. Both compare the local ``HEAD``
against a comparison ref and print the commits reachable from one side only.

The comparison ref is the explicit ``ref`` argument when supplied, otherwise
the current branch's configured upstream (``@{upstream}``). A ref that names a
configured remote is fetched by default before the comparison, whether it is
written as ``origin/main`` or in canonical ``refs/remotes/origin/main`` form.
That fetch updates the shared remote-tracking ref, so a later ``--no-fetch``
run compares against the newly fetched state. ``--no-fetch`` skips the fetch
entirely and never contacts the remote; local refs such as ``main`` are never
fetched regardless.

The ``git_donkey.cli`` module exposes these workflows as Cyclopts apps and
delegates to ``run_git_incoming`` and ``run_git_outgoing``. Shared Git helpers,
including repository discovery and fetching, live in ``git_donkey.helpers``.

Notes
-----
Both runners return standard process exit codes:

- 0: matching commits were found and printed
- 1: the comparison succeeded but found no matching commits
- 2: the command could not run, such as when no upstream is configured and no
  explicit ref was supplied, a fetch failed, or the comparison failed

Examples
--------
>>> from git_donkey import incoming_outgoing
>>> incoming_outgoing.run_git_incoming("origin/main")
0
>>> incoming_outgoing.run_git_outgoing("origin/main", fetch=False)
1

"""

from __future__ import annotations

import typing as typ

from git import GitCommandError, Repo

from git_donkey import helpers

_GIT_INCOMING_PREFIX = "git-incoming"
_GIT_OUTGOING_PREFIX = "git-outgoing"
_REFS_REMOTES_PREFIX = "refs/remotes/"


class _GitLog(typ.Protocol):
    """Small protocol for the ``git log`` command surface."""

    def log(self, *args: str) -> str:
        """Run ``git log`` with the provided arguments."""
        ...


def _current_branch_upstream(repo: Repo, prefix: str) -> str | None:
    """Return the current branch upstream ref, or report a configuration error."""
    try:
        return str(
            repo.git.rev_parse(
                "--abbrev-ref",
                "--symbolic-full-name",
                "@{upstream}",
            )
        )
    except GitCommandError:
        helpers._eprint(
            f"{prefix}: no upstream branch configured; set one with "
            "`git branch --set-upstream-to <remote>/<branch>` or pass a ref"
        )
        return None


def _remote_name_for_ref(repo: Repo, ref: str) -> str | None:
    """Return the configured remote that owns a remote-style ref."""
    normalized_ref = ref.removeprefix(_REFS_REMOTES_PREFIX)
    for remote in repo.remotes:
        remote_name = str(remote.name)
        if normalized_ref == remote_name or normalized_ref.startswith(
            f"{remote_name}/"
        ):
            return remote_name
    return None


def _comparison_ref(repo: Repo, ref: str | None, prefix: str) -> str | None:
    """Resolve an explicit comparison ref or the current branch upstream."""
    if ref is not None:
        return ref
    return _current_branch_upstream(repo, prefix)


def _print_commits_unique_to(
    git: _GitLog,
    *,
    include_ref: str,
    exclude_ref: str,
) -> bool:
    """Print commits reachable from ``include_ref`` but not ``exclude_ref``."""
    output = git.log(
        "--oneline",
        "--decorate",
        include_ref,
        "--not",
        exclude_ref,
    )
    if not output:
        return False
    print(output)
    return True


def _run_comparison(
    *,
    prefix: str,
    ref: str | None,
    fetch: bool,
    direction: typ.Literal["incoming", "outgoing"],
) -> int:
    """Run one incoming or outgoing branch comparison."""
    repo = helpers._find_repo(prefix)
    comparison_ref = _comparison_ref(repo, ref, prefix)
    if comparison_ref is None:
        return 2

    remote_name = _remote_name_for_ref(repo, comparison_ref)
    if fetch and remote_name is not None:
        try:
            helpers._fetch_remote(repo, remote_name, prefix)
        except SystemExit:
            # The shared helper exits 1; this workflow reserves 1 for a
            # successful comparison that found no commits, so a failed fetch
            # must exit 2 like any other failure to run.
            return 2

    include_ref = comparison_ref if direction == "incoming" else "HEAD"
    exclude_ref = "HEAD" if direction == "incoming" else comparison_ref

    try:
        has_commits = _print_commits_unique_to(
            typ.cast("_GitLog", repo.git),
            include_ref=include_ref,
            exclude_ref=exclude_ref,
        )
    except GitCommandError as exc:
        helpers._eprint(f"{prefix}: comparison failed: {exc}")
        return 2
    return 0 if has_commits else 1


def run_git_incoming(ref: str | None = None, *, fetch: bool = True) -> int:
    """Report commits that would be pulled from the comparison ref.

    Compares ``HEAD`` against ``ref`` and prints the commits reachable from
    the comparison ref but not from ``HEAD``, one line per commit.

    Parameters
    ----------
    ref : str | None
        Explicit comparison ref. When ``None``, the current branch's
        configured upstream (``@{upstream}``) is used. A ref naming a
        configured remote, whether as ``origin/main`` or
        ``refs/remotes/origin/main``, is fetched when ``fetch`` is true.
    fetch : bool
        When true (the default), fetch the remote owning a remote-backed
        comparison ref before comparing, updating the shared remote-tracking
        ref. When false, compare against the currently known tracking ref
        without contacting the remote. Local refs are never fetched.

    Returns
    -------
    int
        ``0`` if commits were found and printed, ``1`` if the comparison
        succeeded but found no commits, or ``2`` if the command could not
        run (no upstream and no explicit ref, a failed fetch, or a failed
        comparison).

    Examples
    --------
    >>> from git_donkey import incoming_outgoing
    >>> incoming_outgoing.run_git_incoming("origin/main")
    0
    >>> incoming_outgoing.run_git_incoming("main", fetch=False)
    1

    """
    return _run_comparison(
        prefix=_GIT_INCOMING_PREFIX,
        ref=ref,
        fetch=fetch,
        direction="incoming",
    )


def run_git_outgoing(ref: str | None = None, *, fetch: bool = True) -> int:
    """Report commits that would be pushed to the comparison ref.

    Compares ``HEAD`` against ``ref`` and prints the commits reachable from
    ``HEAD`` but not from the comparison ref, one line per commit.

    Parameters
    ----------
    ref : str | None
        Explicit comparison ref. When ``None``, the current branch's
        configured upstream (``@{upstream}``) is used. A ref naming a
        configured remote, whether as ``origin/main`` or
        ``refs/remotes/origin/main``, is fetched when ``fetch`` is true.
    fetch : bool
        When true (the default), fetch the remote owning a remote-backed
        comparison ref before comparing, updating the shared remote-tracking
        ref. When false, compare against the currently known tracking ref
        without contacting the remote. Local refs are never fetched.

    Returns
    -------
    int
        ``0`` if commits were found and printed, ``1`` if the comparison
        succeeded but found no commits, or ``2`` if the command could not
        run (no upstream and no explicit ref, a failed fetch, or a failed
        comparison).

    Examples
    --------
    >>> from git_donkey import incoming_outgoing
    >>> incoming_outgoing.run_git_outgoing("origin/main")
    0
    >>> incoming_outgoing.run_git_outgoing("main", fetch=False)
    1

    """
    return _run_comparison(
        prefix=_GIT_OUTGOING_PREFIX,
        ref=ref,
        fetch=fetch,
        direction="outgoing",
    )
