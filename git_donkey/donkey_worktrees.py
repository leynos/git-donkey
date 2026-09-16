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

from git import BadName, GitCommandError, Repo

from git_donkey import (
    helpers,
    observability,
    stack_records,
    stack_store,
    stack_writes,
)
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

    ``git donkey`` resolves the base's commit before the worktree is created and
    carries it on the request, so the decision to record and the start point the
    branch is created from are one observation rather than two that could
    disagree.

    Parameters
    ----------
    parent
        Name of the base ref this ref was selected by, as stored in the record.
    writer
        Store the record is written through; constructed only when the base is
        not the trunk, so a trunk birth never holds an object that can write.

    """

    parent: str
    writer: stack_writes.GitStackRecordWriter


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
    base_commit
        Commit the base resolved to, frozen before the worktree was created, or
        ``None`` when no ref of that name is in this repository. It is the one
        commit the branch is started from and recorded at, so a base that moves
        while the branch is being created cannot split the two.
    stack
        What to record at branch birth, or ``None`` when the resolved base is
        the trunk and the branch is therefore not stacked.

    """

    branch_name: str
    target_path: Path
    base_branch: str
    base_commit: str | None = None
    stack: _StackContext | None = None


class StackRecordRefusalError(SystemExit):
    """Exit taken when a branch was created but its stack record was not.

    The branch and its worktree exist by the time this is raised, so it is not
    a failed birth: it is a created branch whose record could not be written,
    reported as its own step's failure by :func:`_birth_record` before it is
    raised. It derives from ``SystemExit`` because the status it carries is
    still the one the process must end with, and the class is what lets a
    handler weigh it against the exits that report a worktree that was never
    created at all.
    """


def _record(observation: Observation) -> None:
    """Record one bounded workflow observation on the active recorder."""
    observability.get_recorder().record(observation)


def _birth_record(
    *,
    branch: str,
    parent: str,
    base: str,
    writer: stack_writes.GitStackRecordWriter,
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
    writer : stack_writes.GitStackRecordWriter
        Store the record is written to.

    Raises
    ------
    stack_store.StackRecordConflictError
        Propagated from the store when the branch already has a record or the
        anchor ref could not be created.
    stack_store.StackRecordError
        Propagated from the store for any other refusal, which is recorded
        without a kind because the store's error says no more than that the
        write failed.
    ValueError
        If the record is not one this package's reader reads back, or the branch
        name would be unsafe in a ref path.
    GitCommandError
        If a write the store does not wrap fails, which is the store's own
        documented gap rather than a decision made here.

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
        # Every refusal is recorded, because every one of them leaves a branch
        # whose record was not written. Catching the store's base error beside
        # the two shapes that escape it unwrapped is what keeps this step from
        # ending in ``started`` with no outcome after it.
        except (stack_store.StackRecordError, GitCommandError, ValueError) as exc:
            _record(
                Observation(
                    operation="stack_record_write",
                    outcome="failure",
                    error_kind=stack_writes.refusal_kind(exc),
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


def _write_birth_record(
    *,
    branch: str,
    stack: _StackContext,
    base: str,
) -> None:
    """Write ``branch``'s stack record, reporting a refusal as a failed record.

    The branch exists by the time this runs, so a store that refuses the record
    leaves a branch whose birth could not be recorded, not a failed birth. A
    refusal reaches here as the store's own error, as a ``ValueError`` from the
    validation its ref paths run, or as a ``GitCommandError`` from a write the
    store does not wrap, and all three mean the same thing: the write did not
    happen. None of them is a failed worktree, which the branch's existence
    proves it was not.

    Parameters
    ----------
    branch : str
        Branch that was just created.
    stack : _StackContext
        Parent the branch is stacked on and the store the record is written to.
    base : str
        Commit the branch was created at, frozen before the branch existed.

    Raises
    ------
    StackRecordRefusalError
        When the record could not be written, carrying the store's message and
        the status the process ends with.

    """
    try:
        _birth_record(
            branch=branch,
            parent=stack.parent,
            base=base,
            writer=stack.writer,
        )
    except (stack_store.StackRecordError, GitCommandError, ValueError) as exc:
        helpers._print_error(
            _GIT_DONKEY_PREFIX,
            f"the branch was created but its stack record was not written: {exc}",
        )
        # Reported before it is raised, under the same prefix and emoji every
        # other exit here is reported under, and raised under this class so a
        # handler can weigh it against the exits that report a worktree which
        # was never created.
        raise StackRecordRefusalError(1) from exc


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
        # The branch starts from the commit the base was already resolved to, so
        # the worktree and any record of it name one commit however the base
        # moved in the meantime. A base that resolved to nothing is resolved
        # again by name here, which is a lookup that can refuse the name itself
        # — GitPython's ``BadName`` — before ``git worktree add`` is reached at
        # all: both refusals are reported below as a failed ``worktree add``
        # rather than as a traceback out of this module.
        start_point = request.base_commit
        if start_point is None:
            start_point = context.repo_home.commit(request.base_branch).hexsha
        # Never track the remote default branch from a new feature branch, even
        # with branch.autoSetupMerge enabled.
        context.repo_home.git.worktree(
            "add",
            "--no-track",
            "-b",
            request.branch_name,
            str(request.target_path),
            start_point,
        )
    except (BadName, GitCommandError, ValueError) as exc:
        helpers._die(_GIT_DONKEY_PREFIX, f"worktree add failed: {exc}", 1)

    # The record is written after the branch exists, because the anchor is a
    # ref and the record's whole point is to keep the boundary reachable for as
    # long as the branch it belongs to. Its boundary is the frozen commit rather
    # than the start point above, and a stack context is only built when there
    # was one: a boundary resolved after the branch existed would be evidence
    # about a different birth.
    if request.stack is not None and request.base_commit is not None:
        _write_birth_record(
            branch=request.branch_name,
            stack=request.stack,
            base=request.base_commit,
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
        is already checked out elsewhere, when the target path exists, or when
        ``git worktree add`` fails.
    StackRecordRefusalError
        Propagated when the branch was created but its stack record could not
        be written. It derives from ``SystemExit``, and is raised under its own
        class because the worktree it belongs to does exist.
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
