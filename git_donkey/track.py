"""git-track workflow helpers."""

from __future__ import annotations

import difflib

from git import GitCommandError, Repo

from git_donkey import helpers

_GIT_TRACK_PREFIX = "git-track"


def _suggest_remote_branches(repo: Repo, remote: str, branch: str) -> list[str]:
    available = sorted({
        ref.remote_head for ref in repo.remote(remote).refs if ref.remote_head != "HEAD"
    })
    return difflib.get_close_matches(branch, available, n=8, cutoff=0.35)


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

    head = None
    try:
        head = repo.heads[branch]
        helpers._eprint(f"Local branch exists: {branch}")
        helpers._eprint(f"Switching to: {branch}")
        head.checkout()
    except IndexError:
        head = None
    except GitCommandError as exc:
        helpers._die(
            _GIT_TRACK_PREFIX,
            f"could not switch to local branch '{branch}': {exc}",
            1,
        )

    if not helpers._remote_branch_exists(repo, remote, branch):
        suggestions = _suggest_remote_branches(repo, remote, branch)
        hint = ""
        if suggestions:
            hint = "\nDid you mean:\n  " + "\n  ".join(suggestions)
        helpers._die(
            _GIT_TRACK_PREFIX,
            f"remote branch not found: {remote}/{branch}{hint}",
            1,
        )

    if head is not None:
        helpers._eprint(f"Updating from: {remote}/{branch}")
        try:
            repo.git.merge(f"{remote}/{branch}")
        except GitCommandError as exc:
            helpers._die(_GIT_TRACK_PREFIX, f"update failed (merge): {exc}", 1)
        return 0

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

    return 0
