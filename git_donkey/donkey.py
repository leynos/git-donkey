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
from git.exc import BadName

from git_donkey import (
    donkey_worktrees,
    helpers,
    observability,
    remote_default,
    stack_records,
    stack_store,
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


def _record_outcome(
    operation: observability.Operation,
    outcome: observability.Outcome,
    error_kind: observability.ErrorKind | None = None,
) -> None:
    """Record one observation of ``operation`` on the active recorder."""
    _record(Observation(operation=operation, outcome=outcome, error_kind=error_kind))


def _record_base_update(
    outcome: observability.Outcome,
    pull_mode: observability.PullModeLabel,
    base_kind: observability.BaseKind,
    *,
    error_kind: observability.ErrorKind | None = None,
) -> None:
    """Record one base-update observation on the active recorder."""
    _record(
        Observation(
            operation="base_update",
            outcome=outcome,
            pull_mode=pull_mode,
            base_kind=base_kind,
            error_kind=error_kind,
        )
    )


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


@dataclasses.dataclass(frozen=True, slots=True)
class _Trunk:
    """The principal remote's default branch, as selected and as resolved.

    Parameters
    ----------
    ref
        Fully qualified remote-tracking ref of the default branch. This is
        always the remote-tracking form rather than whichever form the base
        happened to be named by, so the two refs compared when deciding
        whether a branch is stacked are directly comparable.
    commit
        Commit the ref resolved to, frozen before the branch is created.

    """

    ref: str
    commit: str


def _fetch_remote_default_ref(context: _DonkeyContext) -> tuple[str, str]:
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
    tuple[str, str]
        The advertised branch name, and the fully qualified remote-tracking ref
        it was fetched into.

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
    return branch, remote_default.fetch_default_branch_ref(
        context.repo_home,
        context.remote,
        branch,
        _GIT_DONKEY_PREFIX,
    )


def _local_trunk(
    context: _DonkeyContext,
    *,
    default_branch: str,
    base_branch: str,
) -> _Trunk | None:
    """Return the trunk from local refs only, or ``None`` when it is unknown.

    No remote is contacted. The default branch arrives from the advertised
    default when the workflow selected the base itself, and from the remote's
    own ``HEAD`` alias otherwise, so that a branch created by an explicit base
    costs no network beyond the fetch every run already performs.

    An unknown trunk is not an error. It is the absence of the comparison that
    decides whether a branch is stacked, and an unverifiable record is worse
    than no record, so the branch is still created and simply goes unrecorded.
    The same is true of a trunk whose commit will not resolve: a repository
    this cannot reason about is not one to write evidence about.

    Parameters
    ----------
    context : _DonkeyContext
        Resolved repository state, including the repository holding the trunk.
    default_branch : str
        Name of the branch that would be the trunk, as discovered or as the
        remote's ``HEAD`` alias names it.
    base_branch : str
        Ref the new branch's base was selected by. A base that is the default
        branch by name needs no further comparison.

    Returns
    -------
    _Trunk | None
        The trunk and its commit, or ``None`` when either is unknown.

    """
    if not default_branch or base_branch == default_branch:
        return None
    trunk_ref = f"refs/remotes/{context.remote}/{default_branch}"
    if not helpers._ref_exists(context.repo_home, trunk_ref):
        return None
    try:
        return _Trunk(ref=trunk_ref, commit=context.repo_home.commit(trunk_ref).hexsha)
    except (GitCommandError, ValueError):
        # GitPython reports an unresolvable revision as either, and a ref that
        # resolves to something that is not a commit is one this cannot reason
        # about either.
        return None


def _remote_head_alias(context: _DonkeyContext) -> str:
    """Return the branch the remote's own ``HEAD`` alias names, if any.

    This is a local symbolic ref, never a query, and it can be stale: the plan
    records that ``git donkey`` deliberately does not trust it in place of the
    advertised default, and the explicit-base path deliberately avoids the
    advertised default so that a remote which advertises nothing is still
    usable. A stale alias costs nothing here: the only question asked of it is
    whether the base a caller named is the branch the remote treats as its
    default, and the range comparison is what decides the rest.

    ``--short`` renders the target as ``<remote>/<branch>``, so the remote
    prefix is removed rather than the branch name being split off after the
    first slash: a branch may itself contain slashes, as ``feature/deep`` does,
    and only the leading ``<remote>/`` is known to be a separator.

    Returns
    -------
    str
        The default branch name, or the empty string when the repository has no
        alias to read.

    """
    prefix = f"{context.remote}/"
    try:
        target = context.repo_home.git.symbolic_ref(
            "--short", f"refs/remotes/{context.remote}/HEAD"
        ).strip()
    except GitCommandError:
        # No alias: a clone that never had one, or a remote added by hand.
        return ""
    if not target.startswith(prefix):
        # The alias names a branch of some other remote, which this context is
        # in no position to compare a base against.
        return ""
    return target.removeprefix(prefix)


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


def _base_branch_behind_count(
    context: _DonkeyContext,
    *,
    base_branch: str,
    prefix: str,
) -> int:
    """Return how many commits the base branch is behind its remote counterpart.

    A base that exists only locally has no remote counterpart to measure
    against, and a base with no local branch has nothing to update. Creating a
    tracking branch to measure the latter would leave behind a branch that no
    worktree holds and that this command cannot pull into.

    Parameters
    ----------
    context : _DonkeyContext
        Resolved repository state, including the repository and remote used to
        compare the base branch.
    base_branch : str
        Local branch name to measure against its remote counterpart.
    prefix : str
        CLI name used for error messages.

    Returns
    -------
    int
        The number of commits the local base branch is behind its remote
        counterpart, or zero when there is nothing to compare: the base exists
        only locally, or only on the remote.

    Raises
    ------
    SystemExit
        If the base branch exists neither locally nor on the remote.

    """
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

    if not helpers._local_branch_exists(context.repo_home, base_branch):
        return 0

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
    pull_mode: _PullMode,
    base_kind: observability.BaseKind,
) -> None:
    """Update only the worktree that actually holds the selected base branch."""
    label = _pull_mode_label(pull_mode)
    worktree = context.branch_to_worktree.get(base_branch)
    if worktree is None:
        _record_base_update(
            "failure", label, base_kind, error_kind="base_not_in_worktree"
        )
        helpers._die(
            _GIT_DONKEY_PREFIX,
            f"cannot pull '{base_branch}': it is not checked out in a worktree; "
            "check out that base explicitly or omit the pull option",
            1,
        )
    _record_base_update("started", label, base_kind)
    helpers._eprint(f"Updating existing worktree at: {worktree}")
    with observability.get_recorder().span("pull_execution"):
        try:
            _pull_in_worktree(worktree, context.remote, base_branch, pull_mode)
        except GitCommandError as exc:
            _record_base_update(
                "failure", label, base_kind, error_kind="git_command_error"
            )
            helpers._die(
                _GIT_DONKEY_PREFIX, f"update failed (pull {pull_mode}): {exc}", 1
            )
        _record_base_update("success", label, base_kind)


def _maybe_update_base_branch(
    context: _DonkeyContext,
    *,
    base_branch: str,
    pull_mode: _PullMode | None,
    base_kind: observability.BaseKind,
) -> None:
    """Update an opted-in, behind local base only after confirmation."""
    if pull_mode is None:
        _record_base_update("not_requested", "none", base_kind)
        return

    label = _pull_mode_label(pull_mode)
    local_branch = base_branch.removeprefix(f"refs/remotes/{context.remote}/")
    behind = _base_branch_behind_count(
        context,
        base_branch=local_branch,
        prefix=_GIT_DONKEY_PREFIX,
    )
    if behind <= 0:
        _record_base_update("not_behind", label, base_kind)
        return

    if not helpers._prompt_yes_no(
        f"Base branch '{local_branch}' is behind "
        f"'{context.remote}/{local_branch}' by {behind} commit(s). Pull "
        f"{pull_mode} it first?"
    ):
        _record_base_update("declined", label, base_kind)
        return

    _update_base_branch_in_worktree(
        context,
        base_branch=local_branch,
        pull_mode=pull_mode,
        base_kind=base_kind,
    )


def _worktrees_root(home_dir: Path) -> Path:
    """Return the directory path where worktrees are stored."""
    return (home_dir.parent / f"{home_dir.name}.worktrees").resolve()


def _base_commit(context: _DonkeyContext, base_branch: str) -> str | None:
    """Return the commit ``base_branch`` resolves to, or ``None`` when it does not.

    The base may be named in either form the workflow accepts: a local branch,
    or the remote-tracking form the base selection discovered. A name that
    resolves to neither is not a start point this can freeze, and it is not
    this function's job to report it: the worktree creation that follows puts
    the name to Git, which refuses it in Git's own words rather than in a
    traceback from a revision lookup.

    Parameters
    ----------
    context : _DonkeyContext
        Resolved repository state.
    base_branch : str
        Ref the new branch's base was selected by.

    Returns
    -------
    str | None
        The commit the base resolves to, or ``None`` when no ref of that name
        is in this repository.

    """
    for candidate in (base_branch, f"refs/remotes/{context.remote}/{base_branch}"):
        try:
            return context.repo_home.commit(candidate).hexsha
        except (BadName, GitCommandError, ValueError):
            continue
    return None


def _stack_context(
    context: _DonkeyContext,
    *,
    trunk: _Trunk | None,
    base_branch: str,
) -> donkey_worktrees._StackContext | None:
    """Return what to record for a branch created from ``base_branch``.

    A branch created at the trunk commit is not stacked (INV-11), so it is not
    recorded and no writer is constructed for it. A base that resolves to no
    commit at all is not recorded either: there is no start point to compare
    against the trunk, and the creation step is about to refuse the name.
    Every other base is the parent the record names, as the caller selected it.

    Parameters
    ----------
    context : _DonkeyContext
        Resolved repository state, including the repository the record is
        written to.
    trunk : _Trunk | None
        The resolved trunk, or ``None`` when it could not be resolved.
    base_branch : str
        Ref the new branch's base was selected by.

    Returns
    -------
    donkey_worktrees._StackContext | None
        The record to write, or ``None`` when the branch is not stacked.

    """
    if trunk is None:
        return None
    base_commit = _base_commit(context, base_branch)
    if base_commit is None:
        return None
    if not stack_records.should_record(
        base_branch, base_commit, trunk.ref, trunk.commit
    ):
        return None
    return donkey_worktrees._StackContext(
        parent=base_branch,
        writer=stack_store.GitStackRecordWriter(context.repo_home),
    )


def _create_worktree(
    context: _DonkeyContext,
    *,
    branch_name: str,
    base_branch: str,
    trunk: _Trunk | None,
) -> Path:
    """Create the worktree for ``branch_name`` and return where it was created.

    The target path is resolved once, here, and returned: the template overlay
    that follows this step acts on the directory this call created, and
    computing the path a second time in the caller would let the two disagree
    about where the worktree is.

    The stack record to write is decided here, from the resolved trunk and the
    base's resolved commit. Deciding it here rather than inside the creation
    step keeps the one place that resolves the base and the one place that
    freezes the start point adjacent, so neither can observe a different commit
    from the other.

    Parameters
    ----------
    context : _DonkeyContext
        Resolved repository state, including the root the new worktree is
        created under.
    branch_name : str
        Branch the new worktree checks out.
    base_branch : str
        Ref the new branch is created from.
    trunk : _Trunk | None
        Trunk the base is compared against to decide whether a record is
        written, or ``None`` when the trunk could not be identified.

    Returns
    -------
    Path
        Directory the worktree was created at.

    Raises
    ------
    SystemExit
        Propagated from the creation step when the branch is already checked
        out elsewhere, the target path exists, ``git worktree add`` fails, or
        the branch was created but its stack record could not be written.

    """
    target_path = (context.worktrees_root / branch_name).resolve()
    request = donkey_worktrees._WorktreeRequest(
        branch_name=branch_name,
        base_branch=base_branch,
        target_path=target_path,
        stack=_stack_context(context, trunk=trunk, base_branch=base_branch),
    )
    worktree_context = donkey_worktrees._WorktreeContext(
        repo_home=context.repo_home,
        remote=context.remote,
        branch_to_worktree=context.branch_to_worktree,
    )
    _record_outcome("worktree_creation", "started")
    with observability.get_recorder().span("worktree_creation"):
        try:
            donkey_worktrees.create_worktree(
                context=worktree_context,
                request=request,
            )
        except SystemExit:
            # Creation reports conflicts and failed Git commands by exiting;
            # record that outcome and preserve the exit for the caller.
            _record_outcome("worktree_creation", "failure", "worktree_creation_error")
            raise
        _record_outcome("worktree_creation", "success")
    return target_path


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
        _record_outcome("template_overlay", "unavailable", "selection_error")
        helpers._eprint(f"{_GIT_DONKEY_PREFIX}: {exc}")
        return True
    if template_dir is None:
        _record_outcome("template_overlay", "unavailable")
        return True

    _record_outcome("template_overlay", "started")
    helpers._eprint(f"Applying template overlay from: {template_dir}")
    try:
        templates.apply_template(
            template_dir,
            target_path,
            prefix=_GIT_DONKEY_PREFIX,
        )
    except OSError as e:
        _record_outcome("template_overlay", "failure", "os_error")
        helpers._eprint(
            f"{_GIT_DONKEY_PREFIX}: Error applying template overlay from "
            f"{template_dir}: {e}"
        )
        helpers._eprint(
            f"{_GIT_DONKEY_PREFIX}: Worktree created but template overlay failed"
        )
        return False
    _record_outcome("template_overlay", "success")
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
    base_kind: observability.BaseKind = (
        "implicit_remote_default" if origin_branch is None else "explicit"
    )
    if origin_branch is None:
        # The base selection already discovered the default and fetched it, so
        # the trunk is that ref and the remote is not consulted a second time.
        default_branch, base_branch = _fetch_remote_default_ref(context)
    else:
        base_branch = choose_base_branch(saved_cwd_branch, origin_branch)
        default_branch = _remote_head_alias(context)
    trunk = _local_trunk(
        context,
        default_branch=default_branch,
        base_branch=base_branch,
    )

    _maybe_update_base_branch(
        context,
        base_branch=base_branch,
        pull_mode=pull_mode,
        base_kind=base_kind,
    )

    context.worktrees_root.mkdir(parents=True, exist_ok=True)

    target_path = _create_worktree(
        context,
        branch_name=branch_name,
        base_branch=base_branch,
        trunk=trunk,
    )

    if not _apply_template_overlay(context, target_path):
        return 1

    print(f"🫏 Worktree created: {target_path}")
    return 0
