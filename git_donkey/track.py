"""git-track workflow helpers.

Encapsulates the logic for syncing or creating tracking branches from the first
remote.

Usage:
    from git_donkey import track
    track.run_git_track("feature/foo")
"""

from __future__ import annotations

import difflib
import typing as typ

from git import GitCommandError, Repo

from git_donkey import helpers

if typ.TYPE_CHECKING:
    from git.refs.head import Head

_GIT_TRACK_PREFIX = "git-track"


def _suggest_remote_branches(repo: Repo, remote: str, branch: str) -> list[str]:
    """Suggest similar remote branch names for a missing branch."""
    available = sorted({
        ref.remote_head for ref in repo.remote(remote).refs if ref.remote_head != "HEAD"
    })
    return difflib.get_close_matches(branch, available, n=8, cutoff=0.35)


def _checkout_local_branch(repo: Repo, branch: str) -> Head | None:
    """Check out a local branch and return its head if it exists."""
    try:
        head = repo.heads[branch]
    except IndexError:
        return None

    helpers._eprint(f"Local branch exists: {branch}")
    helpers._eprint(f"Switching to: {branch}")
    try:
        head.checkout()
    except GitCommandError as exc:
        helpers._die(
            _GIT_TRACK_PREFIX,
            f"could not switch to local branch '{branch}': {exc}",
            1,
        )
    return head


def _ensure_remote_branch(repo: Repo, remote: str, branch: str) -> None:
    """Ensure the remote branch exists or exit with suggestions."""
    if helpers._remote_branch_exists(repo, remote, branch):
        return
    suggestions = _suggest_remote_branches(repo, remote, branch)
    hint = ""
    if suggestions:
        hint = "\nDid you mean:\n  " + "\n  ".join(suggestions)
    helpers._die(
        _GIT_TRACK_PREFIX,
        f"remote branch not found: {remote}/{branch}{hint}",
        1,
    )


def _merge_remote_branch(repo: Repo, remote: str, branch: str) -> None:
    """Merge the remote branch into the current branch."""
    helpers._eprint(f"Updating from: {remote}/{branch}")
    try:
        repo.git.merge(f"{remote}/{branch}")
    except GitCommandError as exc:
        helpers._die(_GIT_TRACK_PREFIX, f"update failed (merge): {exc}", 1)


def _checkout_tracking_branch(repo: Repo, remote: str, branch: str) -> None:
    """Check out a new tracking branch for the remote branch."""
    helpers._eprint(f"Creating tracking branch from: {remote}/{branch}")
    try:
        repo.git.checkout("-t", f"{remote}/{branch}")
    except GitCommandError as exc:
        helpers._die(
            _GIT_TRACK_PREFIX,
            f"could not create tracking branch '{branch}' from {remote}/{branch}: "
            f"{exc}",
            1,
        )


def run_git_track(branch: str) -> int:
    """Run the git-track workflow.

    Parameters
    ----------
    branch : str
        Branch name (for example, feature/foo).

    Returns
    -------
    int
        The desired process exit code.

    """
    repo = helpers._find_repo(_GIT_TRACK_PREFIX)
    remote = helpers._first_remote_name(repo, _GIT_TRACK_PREFIX)

    helpers._eprint(f"Using remote: {remote}")
    helpers._fetch_remote(repo, remote, _GIT_TRACK_PREFIX)

    head = _checkout_local_branch(repo, branch)
    _ensure_remote_branch(repo, remote, branch)
    if head is not None:
        _merge_remote_branch(repo, remote, branch)
        return 0
    _checkout_tracking_branch(repo, remote, branch)

    return 0
