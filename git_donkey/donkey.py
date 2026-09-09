"""Implement the git-donkey worktree workflow.

New branches use the principal remote's default branch unless a base is supplied.
Existing base checkouts are only updated when a pull mode is explicitly enabled.

Usage
-----
Run with::

    git-donkey feature/my-branch

Key utilities include worktree creation, base branch resolution, optional pull
helpers, and upstream tracking.
"""

from __future__ import annotations

import dataclasses
import os
import typing as typ

from git import Git, GitCommandError, Repo

from git_donkey import donkey_worktrees, helpers, templates
from git_donkey.helpers import _GIT_DONKEY_PREFIX as _GIT_DONKEY_PREFIX

if typ.TYPE_CHECKING:
    from pathlib import Path

type _PullMode = typ.Literal["--rebase", "--ff-only"]


@dataclasses.dataclass(frozen=True, slots=True)
class _PullOptions:
    """Explicit, mutually exclusive base-checkout update options."""

    pull_rebase: bool = False
    pull_ff: bool = False


_DEFAULT_PULL_OPTIONS = _PullOptions()


@dataclasses.dataclass(frozen=True, slots=True)
class _DonkeyContext:
    """Container for resolved git-donkey repository state."""

    repo_home: Repo
    remote: str
    branch_to_worktree: dict[str, Path]
    worktrees_root: Path


def choose_base_branch(saved_cwd_branch: str, origin_arg: str) -> str:
    """Resolve an explicitly supplied base branch.

    Parameters
    ----------
    saved_cwd_branch : str
        The branch checked out in the current working directory.
    origin_arg : str
        The explicit base argument, or '.' for the current branch.

    Returns
    -------
    str
        The resolved explicit base branch. Implicit remote-default discovery
        is performed separately by the workflow.

    """
    if origin_arg == ".":
        return saved_cwd_branch
    return origin_arg


def _advertised_default_branch(advertisement: str) -> str | None:
    """Extract the branch targeted by HEAD from ls-remote --symref output."""
    for line in advertisement.splitlines():
        match line.split():
            case ["ref:", ref, "HEAD"] if ref.startswith("refs/heads/"):
                return ref.removeprefix("refs/heads/")
    return None


def _remote_default_base(context: _DonkeyContext) -> str:
    """Discover and fetch the principal remote's advertised default branch."""
    try:
        advertisement = context.repo_home.git.ls_remote(
            "--symref", context.remote, "HEAD"
        )
    except GitCommandError as exc:
        helpers._die(
            _GIT_DONKEY_PREFIX,
            f"cannot discover the default branch on '{context.remote}': {exc}",
            1,
        )
    branch = _advertised_default_branch(advertisement)
    if branch is None:
        helpers._die(
            _GIT_DONKEY_PREFIX,
            f"remote '{context.remote}' does not advertise a default branch; "
            "specify a base branch explicitly",
            1,
        )

    remote_ref = f"refs/remotes/{context.remote}/{branch}"
    # A narrow fetch configuration may omit the advertised default branch.
    # Fetch it explicitly rather than trusting a stale local remote/HEAD alias.
    try:
        context.repo_home.git.fetch(
            context.remote, f"+refs/heads/{branch}:{remote_ref}"
        )
    except GitCommandError as exc:
        helpers._die(
            _GIT_DONKEY_PREFIX,
            f"cannot fetch default branch '{context.remote}/{branch}': {exc}",
            1,
        )
    return remote_ref


def _pull_mode(options: _PullOptions, *, no_pull: bool) -> _PullMode | None:
    """Validate the pull flags before repository discovery or mutation."""
    if sum((options.pull_rebase, options.pull_ff, no_pull)) > 1:
        helpers._die(
            _GIT_DONKEY_PREFIX,
            "--pull-rebase, --pull-ff, and --no-pull are mutually exclusive",
            2,
        )
    if options.pull_rebase:
        return "--rebase"
    if options.pull_ff:
        return "--ff-only"
    return None


def _pull_in_worktree(
    worktree_dir: Path,
    remote: str,
    branch: str,
    mode: _PullMode,
) -> None:
    """Pull the selected branch using an explicit integration strategy."""
    git = Git(str(worktree_dir))
    if mode == "--ff-only":
        # Override pull.rebase as well as pull.ff; never rebase or merge here.
        git.pull("--no-rebase", "--ff-only", remote, branch)
    else:
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
    pull_mode: _PullMode,
) -> None:
    """Update only the worktree that actually holds the selected base branch."""
    worktree = context.branch_to_worktree.get(base_branch)
    if worktree is None:
        helpers._die(
            prefix,
            f"cannot pull '{base_branch}': it is not checked out in a worktree; "
            "check out that base explicitly or omit the pull option",
            1,
        )
    helpers._eprint(f"Updating existing worktree at: {worktree}")
    try:
        _pull_in_worktree(worktree, context.remote, base_branch, pull_mode)
    except GitCommandError as exc:
        helpers._die(prefix, f"update failed (pull {pull_mode}): {exc}", 1)


def _maybe_update_base_branch(
    context: _DonkeyContext,
    *,
    base_branch: str,
    pull_mode: _PullMode | None,
    prefix: str,
) -> None:
    """Update an opted-in, behind local base only after confirmation."""
    if pull_mode is None:
        return

    local_branch = base_branch.removeprefix(f"refs/remotes/{context.remote}/")
    behind = _base_branch_behind_count(
        context,
        base_branch=local_branch,
        prefix=prefix,
    )
    if behind <= 0:
        return

    if not helpers._prompt_yes_no(
        f"Base branch '{local_branch}' is behind "
        f"'{context.remote}/{local_branch}' by {behind} commit(s). Pull "
        f"{pull_mode} it first?"
    ):
        return

    _update_base_branch_in_worktree(
        context,
        base_branch=local_branch,
        prefix=prefix,
        pull_mode=pull_mode,
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

def _apply_template_overlay(context: _DonkeyContext, target_path: Path) -> bool:
    """Apply the repository's template overlay, returning False on failure.

    A missing overlay, or a repository whose template directory cannot be
    selected, is not a failure.
    """
    try:
        template_dir = templates.get_template_dir(context.repo_home)
    except ValueError as exc:
        helpers._eprint(f"{_GIT_DONKEY_PREFIX}: {exc}")
        return True
    if template_dir is None:
        return True

    helpers._eprint(f"Applying template overlay from: {template_dir}")
    try:
        templates.apply_template(
            template_dir,
            target_path,
            prefix=_GIT_DONKEY_PREFIX,
        )
    except OSError as e:
        helpers._eprint(
            f"{_GIT_DONKEY_PREFIX}: Error applying template overlay from "
            f"{template_dir}: {e}"
        )
        helpers._eprint(
            f"{_GIT_DONKEY_PREFIX}: Worktree created but template overlay failed"
        )
        return False
    return True
def run_git_donkey(
    branch_name: str,
    origin_branch: str | None = None,
    *,
    no_pull: bool = False,
    options: _PullOptions = _DEFAULT_PULL_OPTIONS,
) -> int:
    """Run the git-donkey workflow.

    Parameters
    ----------
    branch_name : str
        Branch name for the new worktree.
    origin_branch : str | None
        Explicit base branch, or '.' for the CWD branch. When omitted, use
        the fetched default branch on the first configured remote.
    no_pull : bool
        Backwards-compatible explicit no-pull flag. Cannot be combined with
        an enabled pull option.
    options : _PullOptions
        Optional pull strategy. Pulling is disabled by default. An enabled
        strategy retains the confirmation prompt for a behind local base.

    Returns
    -------
    int
        The desired process exit code.

    """
    pull_mode = _pull_mode(options, no_pull=no_pull)
    context, saved_cwd_branch = _load_donkey_context()
    base_branch = (
        _remote_default_base(context)
        if origin_branch is None
        else choose_base_branch(saved_cwd_branch, origin_branch)
    )

    _maybe_update_base_branch(
        context,
        base_branch=base_branch,
        pull_mode=pull_mode,
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

    if not _apply_template_overlay(context, target_path):
        return 1

    print(f"🫏 Worktree created: {target_path}")
    return 0
