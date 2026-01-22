"""Implement the git-donkey worktree workflow.

Provides the workflow for managing linked worktrees for feature branches with
automatic base-branch synchronisation.

Usage
-----
Run with::

    git-donkey feature/my-branch

Key utilities include worktree creation, base branch resolution, pull/rebase
helpers, and upstream tracking.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

from git import Git, GitCommandError, Repo

from git_donkey import donkey_worktrees, helpers, templates

_GIT_DONKEY_PREFIX = helpers._GIT_DONKEY_PREFIX


@dataclasses.dataclass(frozen=True, slots=True)
class _DonkeyContext:
    """Container for resolved git-donkey repository state."""

    repo_home: Repo
    remote: str
    branch_to_worktree: dict[str, Path]
    worktrees_root: Path


def choose_base_branch(saved_cwd_branch: str, origin_arg: str | None) -> str:
    """Choose the base branch for a new worktree.

    Parameters
    ----------
    saved_cwd_branch : str
        The branch checked out in the current working directory.
    origin_arg : str | None
        The origin argument provided by the user.

    Returns
    -------
    str
        The resolved base branch.

    """
    if origin_arg is None:
        return "main"
    if origin_arg == ".":
        return saved_cwd_branch
    return origin_arg


def _pull_rebase_in_worktree(worktree_dir: Path, remote: str, branch: str) -> None:
    """Pull and rebase the specified branch inside a worktree."""
    git = Git(str(worktree_dir))
    git.pull("--rebase", remote, branch)


def _ahead_behind(repo: Repo, base: str, compare_ref: str) -> tuple[int, int]:
    """Return ahead and behind commit counts between two refs."""
    out = repo.git.rev_list("--left-right", "--count", f"{base}...{compare_ref}")
    ahead, behind = out.split()
    return int(ahead), int(behind)


def _base_branch_behind_count(
    context: _DonkeyContext,
    *,
    base_branch: str,
    prefix: str,
) -> int:
    """Return how many commits the base branch is behind its remote counterpart."""
    if not helpers._remote_branch_exists(
        context.repo_home, context.remote, base_branch
    ):
        if helpers._local_branch_exists(context.repo_home, base_branch):
            return 0
        helpers._die(
            prefix,
            f"base branch '{base_branch}' not found locally or on "
            f"'{context.remote}/{base_branch}'",
            1,
        )

    helpers._ensure_local_tracking_branch(
        context.repo_home,
        context.remote,
        base_branch,
        prefix,
    )

    _ahead, behind = _ahead_behind(
        context.repo_home,
        base_branch,
        f"{context.remote}/{base_branch}",
    )
    return behind


def _update_base_branch_in_worktree(
    context: _DonkeyContext,
    *,
    base_branch: str,
    prefix: str,
) -> None:
    """Update the base branch by pulling with rebase in its worktree."""
    worktree = context.branch_to_worktree.get(base_branch)
    if worktree is not None:
        helpers._eprint(f"Updating existing worktree at: {worktree}")
        try:
            _pull_rebase_in_worktree(worktree, context.remote, base_branch)
        except GitCommandError as exc:
            helpers._die(prefix, f"update failed (pull --rebase): {exc}", 1)
        return

    helpers._eprint(f"Updating main worktree for branch: {base_branch}")
    try:
        _pull_rebase_in_worktree(
            Path(context.repo_home.working_tree_dir or "."),
            context.remote,
            base_branch,
        )
    except GitCommandError as exc:
        helpers._die(prefix, f"update failed (pull --rebase): {exc}", 1)


def _maybe_update_base_branch(
    context: _DonkeyContext,
    *,
    base_branch: str,
    no_pull: bool,
    prefix: str,
) -> None:
    """Conditionally update the base branch if behind and user confirms."""
    if no_pull:
        return

    behind = _base_branch_behind_count(
        context,
        base_branch=base_branch,
        prefix=prefix,
    )
    if behind <= 0:
        return

    if not helpers._prompt_yes_no(
        f"Base branch '{base_branch}' is behind "
        f"'{context.remote}/{base_branch}' by {behind} commit(s). Pull --rebase "
        "it first?"
    ):
        return

    _update_base_branch_in_worktree(
        context,
        base_branch=base_branch,
        prefix=prefix,
    )


def _worktrees_root(home_dir: Path) -> Path:
    """Return the directory path where worktrees are stored."""
    return (home_dir.parent / f"{home_dir.name}.worktrees").resolve()


def _create_worktree(
    context: _DonkeyContext,
    *,
    branch_name: str,
    base_branch: str,
    target_path: Path,
) -> None:
    """Create a new worktree for the specified branch."""
    worktree_context = donkey_worktrees._WorktreeContext(
        repo_home=context.repo_home,
        remote=context.remote,
        branch_to_worktree=context.branch_to_worktree,
    )
    request = donkey_worktrees._WorktreeRequest(
        branch_name=branch_name,
        base_branch=base_branch,
        target_path=target_path,
    )
    donkey_worktrees.create_worktree(
        context=worktree_context,
        request=request,
    )


def _load_donkey_context() -> tuple[_DonkeyContext, str]:
    """Load and return the git-donkey repository context and current branch."""
    repo_cwd = helpers._find_repo(_GIT_DONKEY_PREFIX)
    saved_cwd_branch = helpers._get_checked_out_branch_name(
        repo_cwd, _GIT_DONKEY_PREFIX
    )

    stanzas = helpers._parse_worktree_porcelain(repo_cwd)
    home_dir = helpers._main_worktree_path_from_list(stanzas, _GIT_DONKEY_PREFIX)
    branch_to_worktree = helpers._branch_to_worktree_map(stanzas)

    os.chdir(home_dir)
    repo_home = Repo(home_dir)

    remote = helpers._first_remote_name(repo_home, _GIT_DONKEY_PREFIX)
    helpers._eprint(f"Using remote: {remote}")
    helpers._fetch_remote(repo_home, remote, _GIT_DONKEY_PREFIX)

    worktrees_root = _worktrees_root(home_dir)
    context = _DonkeyContext(
        repo_home=repo_home,
        remote=remote,
        branch_to_worktree=branch_to_worktree,
        worktrees_root=worktrees_root,
    )
    return context, saved_cwd_branch


def run_git_donkey(
    branch_name: str,
    origin_branch: str | None = None,
    *,
    no_pull: bool = False,
) -> int:
    """Run the git-donkey workflow.

    Parameters
    ----------
    branch_name : str
        Branch name for the new worktree.
    origin_branch : str | None
        Base branch (default: main). Use '.' for the CWD branch.
    no_pull : bool
        If True, do not prompt to pull --rebase the base branch.

    Returns
    -------
    int
        The desired process exit code.

    """
    context, saved_cwd_branch = _load_donkey_context()
    base_branch = choose_base_branch(saved_cwd_branch, origin_branch)

    _maybe_update_base_branch(
        context,
        base_branch=base_branch,
        no_pull=no_pull,
        prefix=_GIT_DONKEY_PREFIX,
    )

    context.worktrees_root.mkdir(parents=True, exist_ok=True)
    target_path = (context.worktrees_root / branch_name).resolve()

    _create_worktree(
        context,
        branch_name=branch_name,
        base_branch=base_branch,
        target_path=target_path,
    )

    # Apply template overlay if available
    template_dir = templates.get_template_dir(context.repo_home)
    if template_dir is not None:
        helpers._eprint(f"Applying template overlay from: {template_dir}")
        templates.apply_template(
            template_dir,
            target_path,
            prefix=_GIT_DONKEY_PREFIX,
        )

    print(f"🫏 Worktree created: {target_path}")
    return 0
