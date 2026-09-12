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
"""

from __future__ import annotations

import dataclasses
import logging
import os
import shutil
import typing as typ
from pathlib import Path

from git import GitCommandError, InvalidGitRepositoryError, NoSuchPathError, Repo

from git_donkey import donkey, helpers, observability, plonk_policy, remote_default
from git_donkey.plonk_records import (
    _MODE_LABELS,
    _SKIP_REASON_LABELS,
    _CandidateOutcome,
    _PlonkContext,
    _PlonkMode,
    _PlonkResult,
    _SkippedWorktree,
    _SkipReason,
    _SoftPlonkContext,
)
from git_donkey.plonk_selection import (
    _donkey_worktree_candidates,
    _donkey_worktree_paths,
)
from git_donkey.plonk_summary import _render_summary

if typ.TYPE_CHECKING:
    from git_donkey.plonk_records import _PlonkCandidate

_GIT_PLONK_PREFIX = "git-plonk"
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


@dataclasses.dataclass(frozen=True, slots=True)
class _GitWorktreeAdapter:
    """GitPython-backed adapter for history, inspection, and mutation."""

    repo: Repo

    def history_messages(self, ref: str) -> typ.Iterator[str]:
        """Return commit messages from ``ref`` history."""
        for commit in self.repo.iter_commits(ref):
            match commit.message:
                case bytes() as message:
                    yield message.decode(errors="replace")
                case message:
                    yield message

    @staticmethod
    def skip_reason(worktree_path: Path) -> _SkipReason | None:
        """Return why ``worktree_path`` cannot be discarded, if it cannot.

        The check reads the worktree's own repository rather than the main
        checkout, so it needs no state from this adapter; it is a static method
        for the same reason ``_FilesystemCleanupAdapter`` has them. It mirrors
        the check ``git worktree remove`` performs itself: a worktree with
        modified, staged, or untracked files is refused, while ignored files are
        not counted, so ignored build output never blocks cleanup. A worktree
        whose directory is gone gets its own reason, because "uncommitted
        changes" would be inaccurate for it. The whole query is timed as the
        ``worktree_preflight`` span: reading another repository crosses a
        storage boundary.

        Parameters
        ----------
        worktree_path : Path
            Worktree to inspect. It is not modified.

        Returns
        -------
        _SkipReason | None
            The reason to leave the worktree alone, or ``None`` when Git would
            remove it unprompted.

        """
        with observability.get_recorder().span("worktree_preflight"):
            if not worktree_path.is_dir():
                return _SkipReason.UNAVAILABLE
            try:
                worktree_repo = Repo(worktree_path)
            except (InvalidGitRepositoryError, NoSuchPathError):
                return _SkipReason.UNAVAILABLE
            if worktree_repo.is_dirty(untracked_files=True):
                return _SkipReason.DIRTY
            return None

    def remove_worktree(self, worktree_path: Path) -> bool:
        """Remove a linked worktree, reporting whether Git removed it.

        The removal is deliberately unforced: whatever Git refuses to discard
        stays on disk. A refusal is logged, reported on stderr, and returned so
        the caller can skip that candidate and continue the batch instead of
        abandoning worktrees that are still removable. The Git invocation is
        timed as the ``worktree_removal`` span, refusal included, because the
        subprocess runs either way.

        Parameters
        ----------
        worktree_path : Path
            Linked worktree to remove.

        Returns
        -------
        bool
            ``True`` when Git removed the worktree, otherwise ``False``.

        """
        try:
            with observability.get_recorder().span("worktree_removal"):
                self.repo.git.worktree("remove", worktree_path.as_posix())
        except GitCommandError as exc:
            _LOGGER.exception(
                "Failed to remove git-plonk worktree",
                extra={
                    "operation": "remove_worktree",
                    "worktree": worktree_path.as_posix(),
                },
            )
            helpers._eprint(
                f"{_GIT_PLONK_PREFIX}: failed to remove worktree "
                f"'{worktree_path}': {exc}"
            )
            return False
        return True

    def delete_branch(self, branch_name: str) -> bool:
        """Delete ``branch_name``, reporting whether Git deleted it.

        Like the worktree removal, a refusal is reported rather than fatal: a
        completed worktree whose branch survives is a partial result the summary
        must name, not a reason to abandon the candidates queued behind it. The
        Git invocation is timed as the ``branch_deletion`` span, refusal
        included, because the subprocess runs either way.

        Parameters
        ----------
        branch_name : str
            Local branch to delete.

        Returns
        -------
        bool
            ``True`` when Git deleted the branch, otherwise ``False``.

        """
        try:
            with observability.get_recorder().span("branch_deletion"):
                self.repo.git.branch("-D", branch_name)
        except GitCommandError as exc:
            _LOGGER.exception(
                "Failed to delete git-plonk branch",
                extra={
                    "operation": "delete_branch",
                    "branch": branch_name,
                },
            )
            helpers._eprint(
                f"{_GIT_PLONK_PREFIX}: failed to delete branch '{branch_name}': {exc}"
            )
            return False
        return True


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
    remote = remote_default.principal_remote(repo, _GIT_PLONK_PREFIX)
    branch = remote_default.discover_default_branch(repo, remote, _GIT_PLONK_PREFIX)
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
        _GIT_PLONK_PREFIX,
    )


def _load_plonk_context() -> _PlonkContext:
    """Load the main repository, worktree stanzas, root, and trunk ref."""
    repo_cwd = helpers._find_repo(_GIT_PLONK_PREFIX)
    invoking_worktree = (
        Path(repo_cwd.working_tree_dir).resolve()
        if repo_cwd.working_tree_dir is not None
        else None
    )
    stanzas = helpers._parse_worktree_porcelain(repo_cwd)
    home_dir = helpers._main_worktree_path_from_list(stanzas, _GIT_PLONK_PREFIX)
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
    repo_cwd = helpers._find_repo(_GIT_PLONK_PREFIX)
    stanzas = helpers._parse_worktree_porcelain(repo_cwd)
    home_dir = helpers._main_worktree_path_from_list(stanzas, _GIT_PLONK_PREFIX)
    return _SoftPlonkContext(
        stanzas=stanzas,
        worktrees_root=donkey._worktrees_root(home_dir),
    )


def _run_soft(
    stanzas: typ.Iterable[dict[str, object]],
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


def _log_plonk_cleanup_start(mode: _PlonkMode, context: _PlonkContext) -> None:
    """Log the start of completed git-plonk cleanup."""
    _LOGGER.info(
        "Starting git-plonk completed cleanup",
        extra={
            "mode": mode.value,
            "worktrees_root": context.worktrees_root.as_posix(),
            "trunk_ref": context.trunk_ref,
        },
    )


def _log_plonk_candidates_selected(
    mode: _PlonkMode,
    candidates: typ.Sequence[_PlonkCandidate],
    completed_candidates: typ.Sequence[_PlonkCandidate],
    removable_candidates: typ.Sequence[_PlonkCandidate],
) -> None:
    """Log candidate selection counts for completed git-plonk cleanup."""
    _LOGGER.info(
        "Selected git-plonk completion candidates",
        extra={
            "mode": mode.value,
            "candidate_count": len(candidates),
            "completed_count": len(completed_candidates),
            "excluded_invocation_count": len(completed_candidates)
            - len(removable_candidates),
        },
    )


def _log_planned_candidate(
    candidate: _PlonkCandidate,
    mode: _PlonkMode,
    *,
    dry_run: bool,
) -> None:
    """Log the planned removal of one clean completed candidate."""
    _LOGGER.info(
        "Planning completed git-plonk worktree removal"
        if dry_run
        else "Removing completed git-plonk worktree",
        extra={
            "mode": mode.value,
            "operation": "remove_worktree",
            "dry_run": dry_run,
            "branch": candidate.branch_name,
            "marker": candidate.marker,
            "worktree": candidate.worktree_path.as_posix(),
        },
    )


def _log_planned_branch_deletion(
    candidate: _PlonkCandidate,
    mode: _PlonkMode,
    *,
    dry_run: bool,
) -> None:
    """Log the planned branch deletion for one removed candidate."""
    _LOGGER.info(
        "Planning completed git-plonk branch deletion"
        if dry_run
        else "Deleting completed git-plonk branch",
        extra={
            "mode": mode.value,
            "operation": "delete_branch",
            "dry_run": dry_run,
            "branch": candidate.branch_name,
            "marker": candidate.marker,
        },
    )


def _log_skipped_candidate(
    candidate: _PlonkCandidate,
    reason: _SkipReason,
    mode: _PlonkMode,
) -> None:
    """Log a completed candidate that git-plonk left in place."""
    _LOGGER.info(
        "Skipping git-plonk candidate",
        extra={
            "mode": mode.value,
            "operation": "skip_worktree",
            "branch": candidate.branch_name,
            "marker": candidate.marker,
            "worktree": candidate.worktree_path.as_posix(),
            "reason": reason.value,
        },
    )


def _record_step(
    operation: observability.Operation,
    outcome: observability.Outcome,
    mode: _PlonkMode,
    *,
    skip_reason: observability.SkipReasonLabel | None = None,
) -> None:
    """Record one bounded cleanup step, translating ``mode`` to its label."""
    observability.get_recorder().record(
        observability.Observation(
            operation=operation,
            outcome=outcome,
            mode=_MODE_LABELS[mode],
            skip_reason=skip_reason,
        )
    )


def _record_failure(operation: observability.Operation, mode: _PlonkMode) -> None:
    """Record a step where Git refused an action git-plonk asked for."""
    observability.get_recorder().record(
        observability.Observation(
            operation=operation,
            outcome="failure",
            mode=_MODE_LABELS[mode],
            error_kind="git_command_error",
        )
    )


def _clean_completed_candidate(
    candidate: _PlonkCandidate,
    adapter: _GitWorktreeAdapter,
    mode: _PlonkMode,
    *,
    dry_run: bool,
) -> _CandidateOutcome:
    """Clean one completed candidate, reporting what held it back instead.

    A candidate is only touched when Git would discard it unprompted, so a
    completed worktree holding uncommitted or untracked work survives the run
    and is reported. Its local branch survives with it, even in hard mode: the
    branch is only safe to delete once its worktree is gone. A branch Git then
    refuses to delete is reported separately, because that candidate's worktree
    really did go.

    Parameters
    ----------
    candidate : _PlonkCandidate
        Completed candidate to clean.
    adapter : _GitWorktreeAdapter
        Git surface used for the cleanliness query and the removals.
    mode : _PlonkMode
        Cleanup mode; hard mode also deletes the local branch.
    dry_run : bool
        When true, plan the work without removing anything.

    Returns
    -------
    _CandidateOutcome
        What the candidate contributed: a skip reason, a branch-deletion
        failure, or neither when it was cleaned as planned.

    """
    reason = adapter.skip_reason(candidate.worktree_path)
    if reason is not None:
        _log_skipped_candidate(candidate, reason, mode)
        _record_step(
            "worktree_preflight",
            "skipped",
            mode,
            skip_reason=_SKIP_REASON_LABELS[reason],
        )
        return _CandidateOutcome(skip_reason=reason)

    _record_step("worktree_preflight", "success", mode)
    _log_planned_candidate(candidate, mode, dry_run=dry_run)
    if not dry_run:
        if not adapter.remove_worktree(candidate.worktree_path):
            _log_skipped_candidate(candidate, _SkipReason.REMOVAL_FAILED, mode)
            _record_failure("worktree_removal", mode)
            return _CandidateOutcome(skip_reason=_SkipReason.REMOVAL_FAILED)
        _record_step("worktree_removal", "success", mode)

    if mode is not _PlonkMode.HARD:
        return _CandidateOutcome()

    _log_planned_branch_deletion(candidate, mode, dry_run=dry_run)
    if dry_run:
        return _CandidateOutcome()
    if not adapter.delete_branch(candidate.branch_name):
        _record_failure("branch_deletion", mode)
        return _CandidateOutcome(branch_deletion_failed=True)
    _record_step("branch_deletion", "success", mode)
    return _CandidateOutcome()


def _run_completed_cleanup(
    context: _PlonkContext,
    mode: _PlonkMode,
    git_adapter: _GitWorktreeAdapter | None = None,
    *,
    dry_run: bool = False,
) -> _PlonkResult:
    """Remove completed worktrees and optionally their local branches."""
    adapter = git_adapter or _GitWorktreeAdapter(context.repo_home)
    _log_plonk_cleanup_start(mode, context)
    candidates = _donkey_worktree_candidates(context.stanzas, context.worktrees_root)
    completed_candidates = plonk_policy.completed_candidates(
        candidates,
        adapter.history_messages(context.trunk_ref),
    )
    removable_candidates = [
        candidate
        for candidate in completed_candidates
        if candidate.worktree_path != context.invoking_worktree
    ]
    _log_plonk_candidates_selected(
        mode,
        candidates,
        completed_candidates,
        removable_candidates,
    )
    removed_worktrees: list[Path] = []
    removed_branches: list[str] = []
    skipped_worktrees: list[_SkippedWorktree] = []
    failed_branch_deletions: list[str] = []
    for candidate in removable_candidates:
        outcome = _clean_completed_candidate(
            candidate,
            adapter,
            mode,
            dry_run=dry_run,
        )
        if outcome.skip_reason is not None:
            skipped_worktrees.append(
                _SkippedWorktree(candidate.worktree_path, outcome.skip_reason)
            )
            continue
        removed_worktrees.append(candidate.worktree_path)
        if outcome.branch_deletion_failed:
            failed_branch_deletions.append(candidate.branch_name)
        elif mode is _PlonkMode.HARD:
            removed_branches.append(candidate.branch_name)

    return _PlonkResult(
        mode=mode,
        is_dry_run=dry_run,
        inspected_worktrees=len(candidates),
        removed_worktrees=tuple(removed_worktrees),
        removed_branches=tuple(removed_branches),
        skipped_worktrees=tuple(skipped_worktrees),
        failed_branch_deletions=tuple(failed_branch_deletions),
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
    is left in a state the caller asked to change.

    """
    if soft and hard:
        helpers._die(_GIT_PLONK_PREFIX, "--soft and --hard are mutually exclusive", 2)

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
    return 1 if result.failed_branch_deletions else 0
