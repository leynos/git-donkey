"""Implement the git-plonk worktree cleanup command.

The command cleans worktrees created by ``git donkey``. Default and hard modes
use branch names to derive completion markers and then scan canonical trunk
history for matching merge messages. Soft mode only removes generated
directories from linked worktrees and leaves Git state untouched.

A completed worktree is only discarded when Git would discard it unprompted:
worktrees with modified, staged, or untracked files are skipped and reported, so
one dirty candidate cannot abandon the rest of the batch. Nothing here forces a
removal, and hard mode deletes a local branch only after its worktree is gone. A
branch Git refuses to delete is likewise reported, and the sweep continues.

Completion history is read from the principal remote's advertised default
branch, resolved through :mod:`git_donkey.remote_default`, which is the same
trunk `git donkey` creates worktrees from.

The completed-cleanup workflow is :mod:`git_donkey.plonk_cleanup`, which owns
the record lifecycle and the removals; this module resolves the repository
state, runs soft mode, and renders the summary both modes report through.
"""

from __future__ import annotations

import logging
import os
import shutil
import typing as typ
from pathlib import Path

from git import Repo

from git_donkey import donkey, helpers, remote_default
from git_donkey._constants import GIT_PLONK_PREFIX
from git_donkey.plonk_cleanup import _run_completed_cleanup
from git_donkey.plonk_records import (
    _PlonkContext,
    _PlonkMode,
    _PlonkResult,
    _SoftPlonkContext,
)
from git_donkey.plonk_selection import _donkey_worktree_paths
from git_donkey.plonk_summary import _render_summary

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_LOGGER = logging.getLogger(__name__)
_SOFT_TARGET_NAMES = (
    "target",
    "node_modules",
    ".venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "htmlcov",
    "dist",
    "build",
    "coverage",
)


class _FilesystemCleanupAdapter:
    """Filesystem-backed adapter for generated-directory removal."""

    @staticmethod
    def soft_targets(worktree_path: Path) -> list[Path]:
        """Return generated paths present in ``worktree_path``."""
        paths: list[Path] = []
        for target_name in _SOFT_TARGET_NAMES:
            target_path = worktree_path / target_name
            if target_path.is_symlink() or target_path.is_dir():
                paths.append(target_path)
        return paths

    @staticmethod
    def remove_soft_targets(worktree_path: Path) -> list[Path]:
        """Remove generated directories from ``worktree_path`` and return removals."""
        removed_paths: list[Path] = []
        for target_path in _FilesystemCleanupAdapter.soft_targets(worktree_path):
            if target_path.is_symlink():
                target_path.unlink()
                removed_paths.append(target_path)
                continue
            shutil.rmtree(target_path)
            removed_paths.append(target_path)
        return removed_paths


def _advertised_trunk(repo: Repo) -> tuple[str, str]:
    """Return the principal remote and the default branch it advertises.

    The local ``refs/remotes/<remote>/HEAD`` alias is never consulted: a fetch
    can leave it naming a branch the remote no longer advertises. Neither is
    local ``main``, which is not trunk in repositories that default elsewhere.
    Reading the advertisement is the query half of trunk resolution; the fetch
    belongs to :func:`_fetch_canonical_trunk_ref`.

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


def _fetch_canonical_trunk_ref(repo: Repo) -> str:
    """Fetch the advertised trunk into its remote-tracking ref and return it.

    Completion history is read from this ref, so it is the same trunk
    `git donkey` created the worktree from, resolved through
    :mod:`git_donkey.remote_default`.

    Parameters
    ----------
    repo : Repo
        Repository whose principal remote is queried and fetched from.

    Returns
    -------
    str
        The fully qualified remote-tracking ref of the fetched default branch.

    Raises
    ------
    SystemExit
        If no remote is configured, the remote advertises no default branch, or
        the advertised branch cannot be fetched.

    """
    remote, branch = _advertised_trunk(repo)
    return remote_default.fetch_default_branch_ref(
        repo,
        remote,
        branch,
        GIT_PLONK_PREFIX,
    )


def _load_plonk_context() -> _PlonkContext:
    """Load the main repository, worktree stanzas, root, and trunk ref."""
    repo_cwd = helpers._find_repo(GIT_PLONK_PREFIX)
    invoking_worktree = (
        Path(repo_cwd.working_tree_dir).resolve()
        if repo_cwd.working_tree_dir is not None
        else None
    )
    stanzas = helpers._parse_worktree_porcelain(repo_cwd)
    home_dir = helpers._main_worktree_path_from_list(stanzas, GIT_PLONK_PREFIX)
    os.chdir(home_dir)
    repo_home = Repo(home_dir)
    worktrees_root = donkey._worktrees_root(home_dir)
    trunk_ref = _fetch_canonical_trunk_ref(repo_home)
    return _PlonkContext(
        repo_home=repo_home,
        stanzas=stanzas,
        worktrees_root=worktrees_root,
        trunk_ref=trunk_ref,
        invoking_worktree=invoking_worktree,
    )


def _load_soft_plonk_context() -> _SoftPlonkContext:
    """Load only worktree state required by soft cleanup."""
    repo_cwd = helpers._find_repo(GIT_PLONK_PREFIX)
    stanzas = helpers._parse_worktree_porcelain(repo_cwd)
    home_dir = helpers._main_worktree_path_from_list(stanzas, GIT_PLONK_PREFIX)
    return _SoftPlonkContext(
        stanzas=stanzas,
        worktrees_root=donkey._worktrees_root(home_dir),
    )


def _run_soft(
    stanzas: cabc.Iterable[dict[str, object]],
    worktrees_root: Path,
    filesystem: _FilesystemCleanupAdapter | None = None,
    *,
    dry_run: bool = False,
) -> _PlonkResult:
    """Run soft cleanup for every git-donkey worktree."""
    worktree_paths = _donkey_worktree_paths(stanzas, worktrees_root)
    _LOGGER.info(
        "Starting git-plonk soft cleanup",
        extra={
            "mode": _PlonkMode.SOFT.value,
            "worktrees_root": worktrees_root.as_posix(),
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


def run_git_plonk(
    *,
    soft: bool = False,
    hard: bool = False,
    dry_run: bool = False,
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
    under a gitignored directory, do not block cleanup. A skip is a decision
    about that worktree rather than a failure of the run, so it does not change
    the exit code; a branch Git refused to delete does, because the repository
    is left in a state the caller asked to change. A branch whose tip could not
    be preserved as a tombstone is left in place and reported the same way, so
    a hard run never deletes a branch it cannot first record.

    """
    if soft and hard:
        helpers._die(GIT_PLONK_PREFIX, "--soft and --hard are mutually exclusive", 2)

    if soft:
        soft_context = _load_soft_plonk_context()
        result = _run_soft(
            soft_context.stanzas,
            soft_context.worktrees_root,
            dry_run=dry_run,
        )
    else:
        context = _load_plonk_context()
        mode = _PlonkMode.HARD if hard else _PlonkMode.DEFAULT
        result = _run_completed_cleanup(context, mode, dry_run=dry_run)

    print(_render_summary(result))
    return 1 if result.is_incomplete() else 0
