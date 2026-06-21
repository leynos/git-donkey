"""Worktree creation helpers for git-donkey.

Provides the worktree creation logic and upstream tracking helpers used by the
main git-donkey workflow.

Usage
-----
Import and call the worktree helper from the workflow module::

    from git_donkey import donkey_worktrees
    donkey_worktrees.create_worktree(...)
"""

from __future__ import annotations

import dataclasses
import typing as typ

from git import GitCommandError, Repo

from git_donkey import helpers

if typ.TYPE_CHECKING:
    from pathlib import Path

_GIT_DONKEY_PREFIX = helpers._GIT_DONKEY_PREFIX


@dataclasses.dataclass(frozen=True, slots=True)
class _WorktreeContext:
    """Resolved repository state needed while creating a worktree.

    Parameters
    ----------
    repo_home
        Source repository that owns the branches and worktrees.
    remote
        Remote name used for branch discovery and upstream tracking.
    branch_to_worktree
        Existing branch-to-worktree mapping for collision checks.

    """

    repo_home: Repo
    remote: str
    branch_to_worktree: dict[str, Path]


@dataclasses.dataclass(frozen=True, slots=True)
class _WorktreeRequest:
    """Requested worktree branch, base branch, and target directory.

    Parameters
    ----------
    branch_name
        Branch that should be checked out in the new worktree.
    base_branch
        Existing branch used when creating ``branch_name``.
    target_path
        Filesystem path for the new worktree checkout.

    """

    branch_name: str
    base_branch: str
    target_path: Path


def _add_worktree_for_existing_local_branch(
    *,
    context: _WorktreeContext,
    request: _WorktreeRequest,
) -> bool:
    """Add a worktree for an existing local branch if present."""
    if not helpers._local_branch_exists(context.repo_home, request.branch_name):
        return False

    helpers._eprint(
        f"Creating worktree for existing local branch '{request.branch_name}'"
    )
    _ensure_upstream_for_branch(
        context.repo_home,
        branch=request.branch_name,
        remote=context.remote,
        prefix=_GIT_DONKEY_PREFIX,
    )
    try:
        context.repo_home.git.worktree(
            "add",
            str(request.target_path),
            request.branch_name,
        )
    except GitCommandError as exc:
        helpers._die(_GIT_DONKEY_PREFIX, f"worktree add failed: {exc}", 1)
    return True


def _add_worktree_for_remote_branch(
    *,
    context: _WorktreeContext,
    request: _WorktreeRequest,
) -> bool:
    """Add a worktree for a remote branch, creating a local tracker."""
    if not helpers._remote_branch_exists(
        context.repo_home,
        context.remote,
        request.branch_name,
    ):
        return False

    helpers._eprint(
        f"Branch '{request.branch_name}' exists on {context.remote}; creating a "
        "local tracking branch"
    )
    try:
        helpers._ensure_local_tracking_branch(
            context.repo_home,
            context.remote,
            request.branch_name,
            _GIT_DONKEY_PREFIX,
        )
        context.repo_home.git.worktree(
            "add",
            str(request.target_path),
            request.branch_name,
        )
    except GitCommandError as exc:
        helpers._die(_GIT_DONKEY_PREFIX, f"worktree add failed: {exc}", 1)
    return True


def _ensure_base_branch_available(
    *,
    context: _WorktreeContext,
    base_branch: str,
) -> None:
    """Ensure the base branch exists locally or on the remote."""
    if helpers._remote_branch_exists(
        context.repo_home,
        context.remote,
        base_branch,
    ):
        helpers._ensure_local_tracking_branch(
            context.repo_home,
            context.remote,
            base_branch,
            _GIT_DONKEY_PREFIX,
        )
        return

    if not helpers._local_branch_exists(context.repo_home, base_branch):
        helpers._die(
            _GIT_DONKEY_PREFIX,
            f"base branch '{base_branch}' not found locally or on "
            f"'{context.remote}/{base_branch}'",
            1,
        )


def _add_worktree_for_new_branch(
    *,
    context: _WorktreeContext,
    request: _WorktreeRequest,
) -> None:
    """Add a worktree for a new branch based on the base branch."""
    helpers._eprint(
        "Creating new branch "
        f"'{request.branch_name}' from '{request.base_branch}' in a new worktree"
    )
    try:
        _ensure_base_branch_available(
            context=context,
            base_branch=request.base_branch,
        )
        context.repo_home.git.worktree(
            "add",
            "-b",
            request.branch_name,
            str(request.target_path),
            request.base_branch,
        )
    except GitCommandError as exc:
        helpers._die(_GIT_DONKEY_PREFIX, f"worktree add failed: {exc}", 1)


def _ensure_upstream_for_branch(
    repo: Repo,
    *,
    branch: str,
    remote: str,
    prefix: str,
) -> None:
    """Ensure the specified local branch tracks its remote counterpart."""
    if _has_tracking_branch(repo, branch):
        return

    if not helpers._remote_branch_exists(repo, remote, branch):
        helpers._die(prefix, f"remote branch not found: {remote}/{branch}", 1)

    try:
        repo.git.branch("--set-upstream-to", f"{remote}/{branch}", branch)
    except GitCommandError as exc:
        helpers._die(prefix, f"failed to set upstream for '{branch}': {exc}", 1)


def _has_tracking_branch(repo: Repo, branch: str) -> bool:
    """Return whether the branch has an upstream tracking branch."""
    try:
        head = repo.heads[branch]
    except IndexError:
        return False
    return head.tracking_branch() is not None


def create_worktree(
    *,
    context: _WorktreeContext,
    request: _WorktreeRequest,
) -> None:
    """Create a new worktree for the specified branch."""
    existing_worktree = context.branch_to_worktree.get(request.branch_name)
    if existing_worktree is not None:
        helpers._die_conflict(
            _GIT_DONKEY_PREFIX,
            f"branch '{request.branch_name}' is already checked out at: "
            f"{existing_worktree}",
            1,
        )

    if request.target_path.exists():
        helpers._die_conflict(
            _GIT_DONKEY_PREFIX,
            f"target path already exists: {request.target_path}",
            1,
        )

    if _add_worktree_for_existing_local_branch(
        context=context,
        request=request,
    ):
        return

    if _add_worktree_for_remote_branch(
        context=context,
        request=request,
    ):
        return

    _add_worktree_for_new_branch(
        context=context,
        request=request,
    )
