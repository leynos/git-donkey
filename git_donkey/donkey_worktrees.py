"""Worktree creation helpers for git-donkey.

Provides the worktree creation logic and upstream tracking helpers used by the
main git-donkey workflow, and writes the stack record for a branch born from a
base that is not the trunk. The record is written from inside the same step
that creates the branch, because the frozen start point is the whole of the
evidence and a second observation could disagree with it.

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

from git_donkey import helpers, observability, stack_records, stack_store
from git_donkey.helpers import _GIT_DONKEY_PREFIX as _GIT_DONKEY_PREFIX
from git_donkey.observability import Observation

if typ.TYPE_CHECKING:
    from pathlib import Path


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
class _StackContext:
    """What a branch born from a non-trunk base records about its parent.

    ``git donkey`` resolves both values before the worktree is created, so the
    decision to record is made once, from the same frozen base commit the
    worktree is started from, rather than from a second observation that could
    disagree with it.

    Parameters
    ----------
    parent
        Name of the base ref this ref was selected by, as stored in the record.
    writer
        Store the record is written through; constructed only when the base is
        not the trunk, so a trunk birth never holds an object that can write.

    """

    parent: str
    writer: stack_store.GitStackRecordWriter


@dataclasses.dataclass(frozen=True, slots=True)
class _WorktreeRequest:
    """Requested worktree branch, base branch, and target directory.

    Parameters
    ----------
    branch_name
        Branch that should be checked out in the new worktree.
    base_branch
        Existing branch or fully qualified remote ref used for a new branch.
    target_path
        Filesystem path for the new worktree checkout.
    stack
        What to record at branch birth, or ``None`` when the resolved base is
        the trunk and the branch is therefore not stacked.

    """

    branch_name: str
    target_path: Path
    base_branch: str
    stack: _StackContext | None = None


def _record(observation: Observation) -> None:
    """Record one bounded workflow observation on the active recorder."""
    observability.get_recorder().record(observation)


def _birth_record(
    *,
    branch: str,
    parent: str,
    base: str,
    writer: stack_store.GitStackRecordWriter,
) -> None:
    """Write ``branch``'s stack record from the commit it was created at.

    Both the boundary and the observed child tip are that one commit: the
    branch is created at the base and has no commits of its own yet, so an
    exact observation is available here and is never reconstructed later.

    Parameters
    ----------
    branch : str
        Branch that was just created.
    parent : str
        Base ref the branch was created from.
    base : str
        Commit the base resolved to, frozen before the branch was created.
    writer : stack_store.GitStackRecordWriter
        Store the record is written to.

    Raises
    ------
    stack_store.StackRecordError
        Propagated from the store when the branch already has a record or the
        anchor ref could not be created. The caller reports it as a run that
        created the branch but could not record it, because by this point the
        branch exists and undoing it would discard the user's request.

    """
    _record(Observation(operation="stack_record_write", outcome="started"))
    with observability.get_recorder().span("stack_record_write"):
        try:
            writer.create(
                stack_records.StackRecord(
                    branch=branch,
                    parent=stack_records.StackParent(branch=parent, pull_request=None),
                    base=base,
                    recorded_from=base,
                    evidence=stack_records.EVIDENCE_BIRTH,
                )
            )
        except stack_store.StackRecordError:
            _record(
                Observation(
                    operation="stack_record_write",
                    outcome="failure",
                    error_kind="stack_record_conflict",
                )
            )
            raise
    _record(Observation(operation="stack_record_write", outcome="success"))


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
    """Ensure the base branch exists without localizing an implicit remote ref."""
    if base_branch.startswith(f"refs/remotes/{context.remote}/"):
        if not helpers._ref_exists(context.repo_home, base_branch):
            helpers._die(
                _GIT_DONKEY_PREFIX,
                f"remote base ref not found: {base_branch}",
                1,
            )
        return

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
    """Add a new branch from a resolved commit without inheriting base tracking."""
    helpers._eprint(
        "Creating new branch "
        f"'{request.branch_name}' from '{request.base_branch}' in a new worktree"
    )
    try:
        _ensure_base_branch_available(
            context=context,
            base_branch=request.base_branch,
        )
        # Freeze the selected ref once and never track the remote default branch
        # from a new feature branch, even with branch.autoSetupMerge enabled.
        start_point = context.repo_home.commit(request.base_branch).hexsha
        context.repo_home.git.worktree(
            "add",
            "--no-track",
            "-b",
            request.branch_name,
            str(request.target_path),
            start_point,
        )
        # The record is written after the branch exists, because the anchor is
        # a ref and the record's whole point is to keep the boundary reachable
        # for as long as the branch it belongs to.
        if request.stack is not None:
            _birth_record(
                branch=request.branch_name,
                parent=request.stack.parent,
                base=start_point,
                writer=request.stack.writer,
            )
    except (GitCommandError, ValueError) as exc:
        helpers._die(_GIT_DONKEY_PREFIX, f"worktree add failed: {exc}", 1)
    except stack_store.StackRecordError as exc:
        helpers._die(
            _GIT_DONKEY_PREFIX,
            f"the branch was created but its stack record was not written: {exc}",
            1,
        )


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
    """Create a new worktree for the specified branch.

    Prefers an existing local branch, then a remote branch for which a local
    tracking branch is created, and otherwise branches from the base branch.
    A branch created from a non-trunk base is recorded as stacked, from the
    same frozen commit the worktree is started from.

    Parameters
    ----------
    context : _WorktreeContext
        Resolved repository state: the owning repository, the remote used for
        branch discovery, and the existing branch-to-worktree mapping consulted
        for collisions.
    request : _WorktreeRequest
        The branch to check out, the base branch to create it from, the
        filesystem path for the new worktree, and the stack record to write for
        a branch born from a base that is not the trunk.

    Raises
    ------
    SystemExit
        Propagated from the ``git_donkey.helpers`` exit helpers when the branch
        is already checked out elsewhere, when the target path exists, when
        ``git worktree add`` fails, or when the branch was created but its
        stack record could not be written.
    """
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
