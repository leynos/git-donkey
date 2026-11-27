"""Implement the git-plonk worktree cleanup command.

The command cleans worktrees created by ``git donkey``. Default and hard modes
resolve completion in tiers: offline branch-name markers and issue-history
signals first, then a read-only GitHub merged-pull-request lookup for the
principal remote, then the opt-in check that a branch's upstream is gone. Soft
mode only removes generated directories from linked worktrees and leaves Git
state untouched.

A completed worktree is only discarded when Git would discard it unprompted:
worktrees with modified, staged, or untracked files are skipped and reported, so
one dirty candidate cannot abandon the rest of the batch. Nothing here forces a
removal, and hard mode deletes a local branch only after its worktree is gone. A
branch Git refuses to delete is likewise reported, and the sweep continues. A
candidate no tier can resolve is reported as unverified and left in place.

Completion history is read from the principal remote's advertised default
branch, resolved through :mod:`git_donkey.remote_default`, which is the same
trunk `git donkey` creates worktrees from.

The implementation is split by responsibility:
:mod:`git_donkey.plonk_adapters` owns the Git and filesystem boundaries,
:mod:`git_donkey.plonk_completion` owns the completion tiers,
:mod:`git_donkey.plonk_cleanup` performs the planned removals, and
:mod:`git_donkey.plonk_workflow` owns options, logs, and bounded observations.
This module resolves the repository state and orchestrates one run.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from git import GitCommandError, Repo

from git_donkey import helpers, layout, remote_default
from git_donkey.plonk_adapters import _FilesystemCleanupAdapter, _GitWorktreeAdapter
from git_donkey.plonk_cleanup import _clean_removable
from git_donkey.plonk_completion import (
    _offline_evidence,
    _resolve_fallbacks,
    _SweepState,
)
from git_donkey.plonk_records import (
    GIT_PLONK_PREFIX,
    _PlonkCandidate,
    _PlonkContext,
    _PlonkMode,
    _PlonkResult,
    _RemovableCandidate,
    _SoftPlonkContext,
    _UnverifiedWorktree,
)
from git_donkey.plonk_selection import (
    _donkey_worktree_candidates,
    _donkey_worktree_paths,
)
from git_donkey.plonk_summary import _render_summary
from git_donkey.plonk_workflow import (
    _DEFAULT_CLEANUP_OPTIONS,
    _DEFAULT_RUN_OPTIONS,
    _CleanupOptions,
    _log_plonk_candidates_selected,
    _log_plonk_cleanup_start,
    _PlonkRunOptions,
)

_LOGGER = logging.getLogger(__name__)


def _advertised_trunk(repo: Repo) -> tuple[str, str]:
    """Return the principal remote and the default branch it advertises.

    The local ``refs/remotes/<remote>/HEAD`` alias is never consulted: a fetch
    can leave it naming a branch the remote no longer advertises. Neither is
    local ``main``, which is not trunk in repositories that default elsewhere.
    Reading the advertisement is the query half of trunk resolution; the fetch
    belongs to :func:`_resolve_trunk`.

    Parameters
    ----------
    repo : Repo
        Repository whose principal remote is queried.

    Returns
    -------
    tuple[str, str]
        The remote name and the branch its ``HEAD`` advertises.

    Raises
    ------
    SystemExit
        If no remote is configured or it advertises no default branch.

    """
    remote = remote_default.principal_remote(repo, GIT_PLONK_PREFIX)
    branch = remote_default.discover_default_branch(repo, remote, GIT_PLONK_PREFIX)
    return remote, branch


def _resolve_trunk(repo: Repo) -> tuple[str, str, str]:
    """Return the principal remote, its advertised branch, and the fetched ref.

    Parameters
    ----------
    repo : Repo
        Repository whose principal remote is queried and fetched from.

    Returns
    -------
    tuple[str, str, str]
        The remote name, the branch its ``HEAD`` advertises, and that branch's
        fetched remote-tracking ref.

    """
    remote, branch = _advertised_trunk(repo)
    trunk_ref = remote_default.fetch_default_branch_ref(
        repo,
        remote,
        branch,
        GIT_PLONK_PREFIX,
    )
    return remote, branch, trunk_ref


def _configured_remote_url(repo: Repo, remote: str) -> str:
    """Return the raw configured URL for ``remote``, without ``insteadOf`` rewrite.

    The GitHub fallback must see the URL the user configured, not the endpoint
    a URL rewrite sends traffic to: a repository whose remote is rewritten to a
    local mirror is still a GitHub repository. A remote may configure several
    URLs; Git fetches from the first, so that is the one parsed here. An
    unreadable or unset value disables the fallback rather than guessing a
    repository.

    Returns
    -------
    str
        The first raw configured URL, or an empty string when it cannot be
        read.

    """
    try:
        values = repo.git.config("--get-all", f"remote.{remote}.url").splitlines()
    except GitCommandError:
        return ""
    return values[0] if values else ""


def _resolve_layout(
    repo: Repo,
    stanzas: list[dict[str, object]],
) -> layout.RepositoryLayout:
    """Resolve the repository layout, exiting with a usage error on failure."""
    try:
        return layout.resolve_layout(repo, stanzas)
    except layout.LayoutError as exc:
        helpers._die(GIT_PLONK_PREFIX, str(exc), 2)


def _load_plonk_context() -> _PlonkContext:
    """Load the anchor repository, worktree stanzas, root, and trunk ref."""
    repo_cwd = helpers._find_repo(GIT_PLONK_PREFIX)
    invoking_worktree = (
        Path(repo_cwd.working_tree_dir).resolve()
        if repo_cwd.working_tree_dir is not None
        else None
    )
    stanzas = helpers._parse_worktree_porcelain(repo_cwd)
    resolved = _resolve_layout(repo_cwd, stanzas)
    os.chdir(resolved.anchor_dir)
    repo_home = Repo(resolved.anchor_dir)
    remote, branch, trunk_ref = _resolve_trunk(repo_home)
    return _PlonkContext(
        repo_home=repo_home,
        stanzas=stanzas,
        worktrees_root=resolved.worktrees_root,
        anchor_dir=resolved.anchor_dir,
        trunk_ref=trunk_ref,
        invoking_worktree=invoking_worktree,
        remote=remote,
        branch=branch,
        remote_url=_configured_remote_url(repo_home, remote),
    )


def _load_soft_plonk_context() -> _SoftPlonkContext:
    """Load only worktree state required by soft cleanup."""
    repo_cwd = helpers._find_repo(GIT_PLONK_PREFIX)
    stanzas = helpers._parse_worktree_porcelain(repo_cwd)
    resolved = _resolve_layout(repo_cwd, stanzas)
    return _SoftPlonkContext(
        stanzas=stanzas,
        worktrees_root=resolved.worktrees_root,
        anchor_dir=resolved.anchor_dir,
    )


def _run_soft(
    context: _SoftPlonkContext,
    filesystem: _FilesystemCleanupAdapter | None = None,
    *,
    dry_run: bool = False,
) -> _PlonkResult:
    """Run soft cleanup for every git-donkey worktree."""
    worktree_paths = _donkey_worktree_paths(
        context.stanzas, context.worktrees_root, context.anchor_dir
    )
    _LOGGER.info(
        "Starting git-plonk soft cleanup",
        extra={
            "mode": _PlonkMode.SOFT.value,
            "worktrees_root": context.worktrees_root.as_posix(),
            "inspected_worktrees": len(worktree_paths),
        },
    )
    cleanup = filesystem or _FilesystemCleanupAdapter()
    cleaned_paths: list[Path] = []
    for worktree_path in worktree_paths:
        removed_paths = (
            cleanup.soft_targets(worktree_path)
            if dry_run
            else cleanup.remove_soft_targets(worktree_path)
        )
        cleaned_paths.extend(removed_paths)
        _LOGGER.info(
            "Planned generated path cleanup"
            if dry_run
            else "Cleaned generated paths from git-plonk worktree",
            extra={
                "mode": _PlonkMode.SOFT.value,
                "operation": "soft_cleanup",
                "dry_run": dry_run,
                "worktree": worktree_path.as_posix(),
                "removed_count": len(removed_paths),
            },
        )
    return _PlonkResult(
        mode=_PlonkMode.SOFT,
        is_dry_run=dry_run,
        inspected_worktrees=len(worktree_paths),
        cleaned_paths=tuple(cleaned_paths),
    )


def _run_completed_cleanup(
    context: _PlonkContext,
    mode: _PlonkMode,
    git_adapter: _GitWorktreeAdapter | None = None,
    *,
    options: _CleanupOptions = _DEFAULT_CLEANUP_OPTIONS,
) -> _PlonkResult:
    """Remove completed worktrees and optionally their local branches.

    Completion is resolved in tiers: offline history evidence first, then the
    GitHub merged-pull-request fallback, then the opt-in upstream check. The
    invoking worktree is excluded after history resolution and before either
    fallback, so no lookup is spent on a worktree the sweep may not touch.
    Candidates no tier resolves are reported as unverified and survive.

    Returns
    -------
    _PlonkResult
        What the sweep removed, skipped, left unverified, or failed to delete.

    """
    adapter = git_adapter or _GitWorktreeAdapter(context.repo_home)
    state = _SweepState(
        context=context,
        adapter=adapter,
        mode=mode,
        options=options,
    )
    _log_plonk_cleanup_start(mode, context)
    candidate_list: list[_PlonkCandidate] = _donkey_worktree_candidates(
        context.stanzas, context.worktrees_root, context.anchor_dir
    )
    state.resolutions.update(
        _offline_evidence(candidate_list, adapter, context.trunk_ref)
    )
    offline_completed = [
        candidate
        for candidate in candidate_list
        if candidate.branch_name in state.resolutions
    ]
    removable_offline = [
        candidate
        for candidate in offline_completed
        if candidate.worktree_path != context.invoking_worktree
    ]
    active_candidates = [
        candidate
        for candidate in candidate_list
        if candidate.worktree_path != context.invoking_worktree
    ]
    _log_plonk_candidates_selected(
        mode,
        candidate_list,
        offline_completed,
        removable_offline,
    )
    unverified: list[_UnverifiedWorktree] = _resolve_fallbacks(state, active_candidates)
    removable: list[_RemovableCandidate] = [
        _RemovableCandidate(candidate, state.resolutions[candidate.branch_name])
        for candidate in active_candidates
        if candidate.branch_name in state.resolutions
    ]
    tally = _clean_removable(state, removable)

    return _PlonkResult(
        mode=mode,
        is_dry_run=options.dry_run,
        inspected_worktrees=len(candidate_list),
        removed_worktrees=tuple(tally.removed_worktrees),
        removal_evidence=tuple(tally.removal_evidence),
        removed_branches=tuple(tally.removed_branches),
        skipped_worktrees=tuple(tally.skipped_worktrees),
        unverified_worktrees=tuple(unverified),
        failed_branch_deletions=tuple(tally.failed_branch_deletions),
    )


def run_git_plonk(
    *,
    soft: bool = False,
    hard: bool = False,
    dry_run: bool = False,
    options: _PlonkRunOptions = _DEFAULT_RUN_OPTIONS,
) -> int:
    """Run the git-plonk cleanup workflow.

    Parameters
    ----------
    soft : bool, optional
        Remove generated directories from git-donkey worktrees without removing
        worktrees or branches.
    hard : bool, optional
        Remove completed git-donkey worktrees and delete their local branches.
    dry_run : bool, optional
        Print planned cleanup actions without removing paths or branches.
    options : _PlonkRunOptions, optional
        Completion tiers to enable or disable. Neither option is valid with
        ``soft``, which does not judge completion at all.

    Returns
    -------
    int
        The desired process exit code: 0 when the sweep did everything it
        planned, otherwise 1.

    Notes
    -----
    Completed worktrees holding uncommitted, staged, or untracked files are
    skipped and reported instead of being force-removed, and their local
    branches are kept even in hard mode. Ignored files, such as build output
    under a gitignored directory, do not block cleanup. A skip, like an
    unverified worktree, is a decision about that worktree rather than a
    failure of the run, so neither changes the exit code; a branch Git refused
    to delete does, because the repository is left in a state the caller asked
    to change.

    """
    if soft and hard:
        helpers._die(
            GIT_PLONK_PREFIX,
            "--soft and --hard are mutually exclusive",
            2,
        )
    if soft and options.no_github:
        helpers._die(
            GIT_PLONK_PREFIX,
            "--soft and --no-github are mutually exclusive",
            2,
        )
    if soft and options.upstream_gone:
        helpers._die(
            GIT_PLONK_PREFIX,
            "--soft and --upstream-gone are mutually exclusive",
            2,
        )

    if soft:
        soft_context = _load_soft_plonk_context()
        result = _run_soft(soft_context, dry_run=dry_run)
    else:
        context = _load_plonk_context()
        mode = _PlonkMode.HARD if hard else _PlonkMode.DEFAULT
        result = _run_completed_cleanup(
            context,
            mode,
            options=_CleanupOptions(
                dry_run=dry_run,
                no_github=options.no_github,
                upstream_gone=options.upstream_gone,
            ),
        )

    print(_render_summary(result))
    return 1 if result.failed_branch_deletions else 0
