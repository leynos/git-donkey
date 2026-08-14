"""Mercurial-style incoming and outgoing branch comparison workflows."""

from __future__ import annotations

import typing as typ

from git import GitCommandError, Repo

from git_donkey import helpers

_GIT_INCOMING_PREFIX = "git-incoming"
_GIT_OUTGOING_PREFIX = "git-outgoing"


class _GitLog(typ.Protocol):
    """Small protocol for the ``git log`` command surface."""

    def log(self, *args: str) -> str:
        """Run ``git log`` with the provided arguments."""


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
    for remote in repo.remotes:
        remote_name = str(remote.name)
        if ref == remote_name or ref.startswith(f"{remote_name}/"):
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
        helpers._fetch_remote(repo, remote_name, prefix)

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
    """Report commits that would be pulled from the comparison ref."""
    return _run_comparison(
        prefix=_GIT_INCOMING_PREFIX,
        ref=ref,
        fetch=fetch,
        direction="incoming",
    )


def run_git_outgoing(ref: str | None = None, *, fetch: bool = True) -> int:
    """Report commits that would be pushed to the comparison ref."""
    return _run_comparison(
        prefix=_GIT_OUTGOING_PREFIX,
        ref=ref,
        fetch=fetch,
        direction="outgoing",
    )
