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

from git_donkey import (
    donkey_worktrees,
    fetch_refspec,
    helpers,
    incoming_outgoing_policy,
    layout,
    observability,
    remote_default,
    templates,
)
from git_donkey.helpers import _GIT_DONKEY_PREFIX as _GIT_DONKEY_PREFIX
from git_donkey.observability import Observation

if typ.TYPE_CHECKING:
    from pathlib import Path

type _PullMode = typ.Literal["--rebase", "--ff-only"]


@dataclasses.dataclass(frozen=True, slots=True)
class _PullOptions:
    """Explicit, mutually exclusive base-checkout update options."""

    pull_rebase: bool = False
    pull_ff: bool = False


_DEFAULT_PULL_OPTIONS = _PullOptions()


def _record(observation: Observation) -> None:
    """Record one bounded workflow observation on the active recorder."""
    observability.get_recorder().record(observation)


@dataclasses.dataclass(frozen=True, slots=True)
class _DonkeyContext:
    """Container for resolved git-donkey repository state."""

    repo_home: Repo
    remote: str
    branch_to_worktree: dict[str, Path]
    worktrees_root: Path
    caller_repo: Repo


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


def _normalize_explicit_base(context: _DonkeyContext, base_branch: str) -> str:
    """Return the canonical remote-tracking ref a qualified base names.

    Resolution preserves the precedence every existing input relies on. A
    ``refs/`` value and a literal local branch name are used as written, and a
    configured ``<remote>/<branch>`` prefix is read as a remote-tracking ref
    only when that ref exists. Any other input is left for Git to resolve in
    the calling checkout.

    Parameters
    ----------
    context : _DonkeyContext
        Resolved repository state, including the repository whose remotes and
        refs decide the interpretation.
    base_branch : str
        The explicit base supplied on the command line, or the calling branch
        for ``.``.

    Returns
    -------
    str
        The canonical ``refs/remotes/<remote>/<branch>`` ref when the input
        names one, or ``base_branch`` unchanged.

    """
    if base_branch.startswith("refs/") or helpers._local_branch_exists(
        context.repo_home, base_branch
    ):
        return base_branch

    remote_names = helpers._configured_remote_names(context.repo_home)
    if incoming_outgoing_policy.remote_name_for_ref(remote_names, base_branch) is None:
        return base_branch

    canonical = f"refs/remotes/{base_branch}"
    if not helpers._ref_exists(context.repo_home, canonical):
        return base_branch
    return canonical


def _fetch_remote_default_ref(context: _DonkeyContext) -> str:
    """Fetch the principal remote's advertised default branch.

    This is a command, not a query: it discovers the branch the remote's
    ``HEAD`` names and fetches it into that branch's fully qualified
    remote-tracking ref. Both steps live in :mod:`git_donkey.remote_default`,
    which `git plonk` resolves its completion history through as well.

    Parameters
    ----------
    context : _DonkeyContext
        Resolved repository state, including the repository and remote whose
        advertised default branch is fetched.

    Returns
    -------
    str
        The fully qualified remote-tracking ref of the fetched default branch.

    Raises
    ------
    SystemExit
        If the remote does not advertise a default branch, or if the branch it
        names cannot be fetched.

    """
    branch = remote_default.discover_default_branch(
        context.repo_home,
        context.remote,
        _GIT_DONKEY_PREFIX,
        missing_advice="specify a base branch explicitly",
    )
    return remote_default.fetch_default_branch_ref(
        context.repo_home,
        context.remote,
        branch,
        _GIT_DONKEY_PREFIX,
    )


def _pull_mode_label(pull_mode: _PullMode | None) -> observability.PullModeLabel:
    """Return the bounded record label for an internal pull-mode flag."""
    match pull_mode:
        case "--rebase":
            return "rebase"
        case "--ff-only":
            return "ff_only"
        case None:
            return "none"


def _pull_mode(options: _PullOptions, *, no_pull: bool) -> _PullMode | None:
    """Validate the pull flags before repository discovery or mutation."""
    if sum((options.pull_rebase, options.pull_ff, no_pull)) > 1:
        _record(
            Observation(
                operation="pull_mode_selection", outcome="rejected", pull_mode="none"
            )
        )
        helpers._die(
            _GIT_DONKEY_PREFIX,
            "--pull-rebase, --pull-ff, and --no-pull are mutually exclusive",
            2,
        )
    if options.pull_rebase:
        _record(
            Observation(
                operation="pull_mode_selection", outcome="selected", pull_mode="rebase"
            )
        )
        return "--rebase"
    if options.pull_ff:
        _record(
            Observation(
                operation="pull_mode_selection", outcome="selected", pull_mode="ff_only"
            )
        )
        return "--ff-only"
    _record(
        Observation(
            operation="pull_mode_selection", outcome="not_requested", pull_mode="none"
        )
    )
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


def _local_branch_for_base(
    base_branch: str,
    *,
    remote: str,
    remote_names: typ.Iterable[str],
) -> str:
    """Return the local branch name a selected base would update.

    A base qualified by the principal remote names the branch of the same
    name, and a directly supplied local ref names its branch. Only one
    namespace is removed, so a branch whose own name contains
    ``refs/heads/`` keeps it. A base qualified by another remote names no
    local branch this command knows how to update, and is returned unchanged.

    Parameters
    ----------
    base_branch : str
        The base as selected, which may be a branch name, a canonical
        remote-tracking ref, a tag, or another commit-ish.
    remote : str
        Principal remote whose tracking namespace may be stripped.
    remote_names : typing.Iterable[str]
        Configured remote names, used to find the remote that owns the base.
        A nested remote such as ``origin/fork`` owns ``origin/fork/main`` even
        though ``origin`` prefixes it.

    Returns
    -------
    str
        The local branch name to compare with its remote counterpart, or the
        base unchanged when it names no principal-remote branch.

    """
    owner = incoming_outgoing_policy.remote_name_for_ref(remote_names, base_branch)
    remote_prefix = f"refs/remotes/{remote}/"
    if owner is not None:
        if owner == remote and base_branch.startswith(remote_prefix):
            return base_branch.removeprefix(remote_prefix)
        return base_branch
    return base_branch.removeprefix("refs/heads/")


def _base_branch_behind_count(
    context: _DonkeyContext,
    *,
    base_branch: str,
    local_branch: str,
    prefix: str,
) -> int:
    """Return how many commits the base branch is behind its remote counterpart.

    A base that exists only locally has no remote counterpart to measure
    against, and a base with no local branch has nothing to update. Creating a
    tracking branch to measure the latter would leave behind a branch that no
    worktree holds and that this command cannot pull into. A base that names a
    tag or commit instead of a branch has no branch to update either, and is
    accepted once Git resolves it, so such a base never prompts.

    Parameters
    ----------
    context : _DonkeyContext
        Resolved repository state, including the repository and remote used to
        compare the base branch.
    base_branch : str
        The base as selected, which may be a branch, a canonical
        remote-tracking ref, a tag, or another commit-ish.
    local_branch : str
        Local branch name to measure against its remote counterpart, after
        stripping a principal-remote prefix from ``base_branch``.
    prefix : str
        CLI name used for error messages.

    Returns
    -------
    int
        The number of commits the local base branch is behind its remote
        counterpart, or zero when there is nothing to compare: the base exists
        only locally, only on the remote, or names no branch at all.

    Raises
    ------
    SystemExit
        If the base names neither a branch nor a commit Git can resolve.

    """
    if not helpers._remote_branch_exists(
        context.repo_home, context.remote, local_branch
    ):
        if helpers._local_branch_exists(context.repo_home, local_branch):
            return 0
        if helpers._resolve_commit(context.caller_repo, base_branch) is not None:
            return 0
        helpers._die(
            prefix,
            helpers._base_not_found_message(
                context.repo_home, base_branch, context.remote
            ),
            1,
        )

    if not helpers._local_branch_exists(context.repo_home, local_branch):
        return 0

    # Fully qualified refs, so a tag named like the branch cannot shadow it in
    # Git's ambiguous short-name resolution.
    _ahead, behind = _ahead_behind(
        context.repo_home,
        f"refs/heads/{local_branch}",
        f"refs/remotes/{context.remote}/{local_branch}",
    )
    return behind


def _update_base_branch_in_worktree(
    context: _DonkeyContext,
    *,
    base_branch: str,
    pull_mode: _PullMode,
    base_kind: observability.BaseKind,
) -> None:
    """Update only the worktree that actually holds the selected base branch."""
    label = _pull_mode_label(pull_mode)
    worktree = context.branch_to_worktree.get(base_branch)
    if worktree is None:
        _record(
            Observation(
                operation="base_update",
                outcome="failure",
                pull_mode=label,
                base_kind=base_kind,
                error_kind="base_not_in_worktree",
            )
        )
        helpers._die(
            _GIT_DONKEY_PREFIX,
            f"cannot pull '{base_branch}': it is not checked out in a worktree; "
            "check out that base explicitly or omit the pull option",
            1,
        )
    _record(
        Observation(
            operation="base_update",
            outcome="started",
            pull_mode=label,
            base_kind=base_kind,
        )
    )
    helpers._eprint(f"Updating existing worktree at: {worktree}")
    with observability.get_recorder().span("pull_execution"):
        try:
            _pull_in_worktree(worktree, context.remote, base_branch, pull_mode)
        except GitCommandError as exc:
            _record(
                Observation(
                    operation="base_update",
                    outcome="failure",
                    pull_mode=label,
                    base_kind=base_kind,
                    error_kind="git_command_error",
                )
            )
            helpers._die(
                _GIT_DONKEY_PREFIX, f"update failed (pull {pull_mode}): {exc}", 1
            )
        _record(
            Observation(
                operation="base_update",
                outcome="success",
                pull_mode=label,
                base_kind=base_kind,
            )
        )


def _maybe_update_base_branch(
    context: _DonkeyContext,
    *,
    base_branch: str,
    pull_mode: _PullMode | None,
    base_kind: observability.BaseKind,
) -> None:
    """Update an opted-in, behind local base only after confirmation."""
    if pull_mode is None:
        _record(
            Observation(
                operation="base_update",
                outcome="not_requested",
                pull_mode="none",
                base_kind=base_kind,
            )
        )
        return

    label = _pull_mode_label(pull_mode)
    local_branch = _local_branch_for_base(
        base_branch,
        remote=context.remote,
        remote_names=helpers._configured_remote_names(context.repo_home),
    )
    behind = _base_branch_behind_count(
        context,
        base_branch=base_branch,
        local_branch=local_branch,
        prefix=_GIT_DONKEY_PREFIX,
    )
    if behind <= 0:
        _record(
            Observation(
                operation="base_update",
                outcome="not_behind",
                pull_mode=label,
                base_kind=base_kind,
            )
        )
        return

    if not helpers._prompt_yes_no(
        f"Base branch '{local_branch}' is behind "
        f"'{context.remote}/{local_branch}' by {behind} commit(s). Pull "
        f"{pull_mode} it first?"
    ):
        _record(
            Observation(
                operation="base_update",
                outcome="declined",
                pull_mode=label,
                base_kind=base_kind,
            )
        )
        return

    _update_base_branch_in_worktree(
        context,
        base_branch=local_branch,
        pull_mode=pull_mode,
        base_kind=base_kind,
    )


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
        caller_repo=context.caller_repo,
    )
    request = donkey_worktrees._WorktreeRequest(
        branch_name=branch_name,
        base_branch=base_branch,
        target_path=target_path,
    )
    _record(Observation(operation="worktree_creation", outcome="started"))
    with observability.get_recorder().span("worktree_creation"):
        try:
            donkey_worktrees.create_worktree(
                context=worktree_context,
                request=request,
            )
        except SystemExit:
            # Creation reports conflicts and failed Git commands by exiting;
            # record that outcome and preserve the exit for the caller.
            _record(
                Observation(
                    operation="worktree_creation",
                    outcome="failure",
                    error_kind="worktree_creation_error",
                )
            )
            raise
        _record(Observation(operation="worktree_creation", outcome="success"))


def _resolve_layout(
    repo: Repo,
    stanzas: list[dict[str, object]],
) -> layout.RepositoryLayout:
    """Resolve the repository layout, exiting with a usage error on failure."""
    try:
        return layout.resolve_layout(repo, stanzas)
    except layout.LayoutError as exc:
        helpers._die(_GIT_DONKEY_PREFIX, str(exc), 2)


def _require_fetch_refspec(repo: Repo, remote: str) -> None:
    """Exit unless ``remote`` can track the branches its fetch updates."""
    try:
        refspecs = helpers._read_fetch_refspecs(repo, remote)
    except fetch_refspec.FetchRefspecError as exc:
        helpers._die(_GIT_DONKEY_PREFIX, str(exc), 2)
    refusal = fetch_refspec.refusal_message(remote, refspecs)
    if refusal is not None:
        helpers._die(_GIT_DONKEY_PREFIX, refusal, 2)


def _load_donkey_context() -> tuple[_DonkeyContext, str]:
    """Load and return the git-donkey repository context and current branch."""
    repo_cwd = helpers._find_repo(_GIT_DONKEY_PREFIX)
    saved_cwd_branch = helpers._get_checked_out_branch_name(
        repo_cwd, _GIT_DONKEY_PREFIX
    )

    stanzas = helpers._parse_worktree_porcelain(repo_cwd)
    resolved = _resolve_layout(repo_cwd, stanzas)
    branch_to_worktree = helpers._branch_to_worktree_map(stanzas)

    # The anchor is the main worktree, or the bare repository when every
    # checkout is a linked worktree; Git runs worktree commands from either.
    os.chdir(resolved.anchor_dir)
    repo_home = Repo(resolved.anchor_dir)

    remote = helpers._first_remote_name(repo_home, _GIT_DONKEY_PREFIX)
    helpers._eprint(f"Using remote: {remote}")
    _require_fetch_refspec(repo_home, remote)
    helpers._fetch_remote(repo_home, remote, _GIT_DONKEY_PREFIX)

    context = _DonkeyContext(
        repo_home=repo_home,
        remote=remote,
        branch_to_worktree=branch_to_worktree,
        worktrees_root=resolved.worktrees_root,
        caller_repo=repo_cwd,
    )
    return context, saved_cwd_branch


def _apply_template_overlay(context: _DonkeyContext, target_path: Path) -> bool:
    """Apply the repository's template overlay, returning False on failure.

    A missing overlay, or a repository whose template directory cannot be
    selected, is not a failure.

    Parameters
    ----------
    context : _DonkeyContext
        Resolved repository state, including the repository whose template
        directory is consulted.
    target_path : Path
        Filesystem path of the newly created worktree.

    Returns
    -------
    bool
        ``True`` when the overlay is absent, skipped, or applied; ``False``
        when applying it failed.

    """
    try:
        template_dir = templates.get_template_dir(context.repo_home)
    except ValueError as exc:
        _record(
            Observation(
                operation="template_overlay",
                outcome="unavailable",
                error_kind="selection_error",
            )
        )
        helpers._eprint(f"{_GIT_DONKEY_PREFIX}: {exc}")
        return True
    if template_dir is None:
        _record(Observation(operation="template_overlay", outcome="unavailable"))
        return True

    _record(Observation(operation="template_overlay", outcome="started"))
    helpers._eprint(f"Applying template overlay from: {template_dir}")
    try:
        templates.apply_template(
            template_dir,
            target_path,
            prefix=_GIT_DONKEY_PREFIX,
        )
    except OSError as e:
        _record(
            Observation(
                operation="template_overlay",
                outcome="failure",
                error_kind="os_error",
            )
        )
        helpers._eprint(
            f"{_GIT_DONKEY_PREFIX}: Error applying template overlay from "
            f"{template_dir}: {e}"
        )
        helpers._eprint(
            f"{_GIT_DONKEY_PREFIX}: Worktree created but template overlay failed"
        )
        return False
    _record(Observation(operation="template_overlay", outcome="success"))
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
        Explicit base, or '.' for the CWD branch. A base may be a local
        branch name, a remote-qualified branch such as ``origin/main``, a tag,
        or any revision Git resolves to a commit. When omitted, use the
        fetched default branch on the first configured remote.
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
    base_kind: observability.BaseKind = (
        "implicit_remote_default" if origin_branch is None else "explicit"
    )
    if origin_branch is None:
        base_branch = _fetch_remote_default_ref(context)
    else:
        base_branch = _normalize_explicit_base(
            context,
            choose_base_branch(saved_cwd_branch, origin_branch),
        )

    _maybe_update_base_branch(
        context,
        base_branch=base_branch,
        pull_mode=pull_mode,
        base_kind=base_kind,
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
