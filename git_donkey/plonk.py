"""Implement the git-plonk worktree cleanup command.

The command cleans worktrees created by ``git donkey``. Default and hard modes
use branch names to derive completion markers and then scan canonical trunk
history for matching merge messages. Soft mode only removes generated
directories from linked worktrees and leaves Git state untouched.
"""

from __future__ import annotations

import dataclasses
import enum
import logging
import os
import shutil
import typing as typ
from pathlib import Path

from git import GitCommandError, Repo

from git_donkey import donkey, helpers, plonk_policy

_GIT_PLONK_PREFIX = "git-plonk"
_REFS_HEADS_PREFIX = "refs/heads/"
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


class _PlonkMode(enum.StrEnum):
    """Supported cleanup modes for git-plonk."""

    DEFAULT = "default"
    SOFT = "soft"
    HARD = "hard"


@dataclasses.dataclass(frozen=True)
class _PlonkCandidate:
    """A linked worktree eligible for completion-marker cleanup."""

    branch_name: str
    worktree_path: Path
    marker: str


@dataclasses.dataclass(frozen=True)
class _PlonkResult:
    """Summary of filesystem and Git state removed by a git-plonk run."""

    mode: _PlonkMode
    is_dry_run: bool = False
    inspected_worktrees: int = 0
    removed_worktrees: tuple[Path, ...] = ()
    removed_branches: tuple[str, ...] = ()
    cleaned_paths: tuple[Path, ...] = ()


@dataclasses.dataclass(frozen=True)
class _PlonkContext:
    """Resolved repository state used by one git-plonk run."""

    repo_home: Repo
    stanzas: list[dict[str, object]]
    worktrees_root: Path
    trunk_ref: str
    invoking_worktree: Path | None


@dataclasses.dataclass(frozen=True)
class _SoftPlonkContext:
    """Resolved repository state used by a soft git-plonk run."""

    stanzas: list[dict[str, object]]
    worktrees_root: Path


@dataclasses.dataclass(frozen=True)
class _GitWorktreeAdapter:
    """GitPython-backed adapter for history and worktree mutation."""

    repo: Repo

    def history_messages(self, ref: str) -> typ.Iterator[str]:
        """Return commit messages from ``ref`` history."""
        for commit in self.repo.iter_commits(ref):
            match commit.message:
                case bytes() as message:
                    yield message.decode(errors="replace")
                case message:
                    yield message

    def remove_worktree(self, worktree_path: Path) -> None:
        """Remove a linked worktree with Git's worktree machinery."""
        try:
            self.repo.git.worktree("remove", "--force", worktree_path.as_posix())
        except GitCommandError as exc:
            _LOGGER.exception(
                "Failed to remove git-plonk worktree",
                extra={
                    "operation": "remove_worktree",
                    "worktree": worktree_path.as_posix(),
                },
            )
            helpers._die(
                _GIT_PLONK_PREFIX,
                f"failed to remove worktree '{worktree_path}': {exc}",
                1,
            )

    def delete_branch(self, branch_name: str) -> None:
        """Delete ``branch_name`` after its completed worktree has been removed."""
        try:
            self.repo.git.branch("-D", branch_name)
        except GitCommandError as exc:
            _LOGGER.exception(
                "Failed to delete git-plonk branch",
                extra={
                    "operation": "delete_branch",
                    "branch": branch_name,
                },
            )
            helpers._die(
                _GIT_PLONK_PREFIX,
                f"failed to delete branch '{branch_name}': {exc}",
                1,
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


def _completion_marker_for_branch(branch_name: str) -> str | None:
    """Return the completion marker implied by ``branch_name``, if recognized."""
    return plonk_policy.completion_marker_for_branch(branch_name)


def _completed_candidates(
    candidates: typ.Iterable[_PlonkCandidate],
    messages: typ.Iterable[str],
) -> list[_PlonkCandidate]:
    """Return candidates whose completion markers appear in commit history."""
    return plonk_policy.completed_candidates(candidates, messages)


def _branch_name_from_stanza(stanza: dict[str, object]) -> str | None:
    """Return a local branch name from a parsed worktree stanza."""
    branch = stanza.get("branch")
    if branch is None:
        return None
    return str(branch).removeprefix(_REFS_HEADS_PREFIX)


def _worktree_path_from_stanza(stanza: dict[str, object]) -> Path | None:
    """Return a resolved worktree path from a parsed worktree stanza."""
    worktree = stanza.get("worktree")
    if worktree is None:
        return None
    return Path(str(worktree)).expanduser().resolve()


def _is_git_donkey_worktree(worktree_path: Path, worktrees_root: Path) -> bool:
    """Return whether ``worktree_path`` is under the git-donkey worktree root."""
    return worktree_path != worktrees_root and worktree_path.is_relative_to(
        worktrees_root
    )


def _donkey_worktree_paths(
    stanzas: typ.Iterable[dict[str, object]],
    worktrees_root: Path,
) -> list[Path]:
    """Return linked worktree paths owned by git-donkey."""
    paths: list[Path] = []
    for stanza in stanzas:
        worktree_path = _worktree_path_from_stanza(stanza)
        if worktree_path is None:
            continue
        if _is_git_donkey_worktree(worktree_path, worktrees_root):
            paths.append(worktree_path)
    return paths


def _donkey_worktree_candidates(
    stanzas: typ.Iterable[dict[str, object]],
    worktrees_root: Path,
) -> list[_PlonkCandidate]:
    """Return recognized git-donkey worktrees with completion markers."""
    candidates: list[_PlonkCandidate] = []
    for stanza in stanzas:
        branch_name = _branch_name_from_stanza(stanza)
        worktree_path = _worktree_path_from_stanza(stanza)
        if branch_name is None or worktree_path is None:
            continue
        if not _is_git_donkey_worktree(worktree_path, worktrees_root):
            continue
        marker = _completion_marker_for_branch(branch_name)
        if marker is None:
            continue
        candidates.append(
            _PlonkCandidate(
                branch_name=branch_name,
                worktree_path=worktree_path,
                marker=marker,
            )
        )
    return candidates


def _empty_summary_message(result: _PlonkResult) -> str:
    """Return the no-op summary line for ``result``."""
    if result.mode is _PlonkMode.SOFT and result.inspected_worktrees > 0:
        return "No generated paths to clean in git donkey worktrees."
    return "No matching git donkey worktrees found."


def _append_summary_section(
    lines: list[str], heading: str, entries: typ.Iterable[object]
) -> None:
    """Append ``heading`` and bullet entries when ``entries`` is populated."""
    section_entries = tuple(entries)
    if not section_entries:
        return

    lines.append(heading)
    lines.extend(f"- {entry}" for entry in section_entries)


def _render_summary(result: _PlonkResult) -> str:
    """Render a deterministic human-readable summary for ``result``."""
    suffix = " dry-run" if result.is_dry_run else ""
    lines: list[str] = [f"git-plonk: mode={result.mode.value}{suffix}"]
    has_removals = any((
        result.removed_worktrees,
        result.removed_branches,
        result.cleaned_paths,
    ))
    if not has_removals:
        lines.append(_empty_summary_message(result))
        return "\n".join(lines)

    if result.is_dry_run:
        _append_summary_section(
            lines, "Planned worktree removals:", result.removed_worktrees
        )
        _append_summary_section(
            lines, "Planned branch deletions:", result.removed_branches
        )
        _append_summary_section(
            lines, "Planned generated path removals:", result.cleaned_paths
        )
        return "\n".join(lines)

    _append_summary_section(lines, "Removed worktrees:", result.removed_worktrees)
    _append_summary_section(lines, "Removed branches:", result.removed_branches)
    _append_summary_section(lines, "Removed generated paths:", result.cleaned_paths)
    return "\n".join(lines)


def _canonical_trunk_ref(repo: Repo) -> str:
    """Return the canonical trunk/default ref for completion history."""
    for remote in repo.remotes:
        remote_head_ref = f"refs/remotes/{remote.name}/HEAD"
        try:
            return repo.git.symbolic_ref("--quiet", "--short", remote_head_ref)
        except GitCommandError:
            continue

    if helpers._local_branch_exists(repo, "main"):
        return "main"

    helpers._die(
        _GIT_PLONK_PREFIX,
        "could not determine canonical trunk ref for completion history",
        2,
    )
    return ""


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
    trunk_ref = _canonical_trunk_ref(repo_home)
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


def _remove_completed_candidate(
    candidate: _PlonkCandidate,
    adapter: _GitWorktreeAdapter,
    mode: _PlonkMode,
    *,
    dry_run: bool,
) -> str | None:
    """Remove one completed candidate and return its branch when deleted."""
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
    if not dry_run:
        adapter.remove_worktree(candidate.worktree_path)
    if mode is not _PlonkMode.HARD:
        return None

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
    if not dry_run:
        adapter.delete_branch(candidate.branch_name)
    return candidate.branch_name


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
    completed_candidates = _completed_candidates(
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
    removed_worktrees = [candidate.worktree_path for candidate in removable_candidates]
    removed_branches: list[str] = []
    for candidate in removable_candidates:
        branch_name = _remove_completed_candidate(
            candidate,
            adapter,
            mode,
            dry_run=dry_run,
        )
        if branch_name is not None:
            removed_branches.append(branch_name)

    return _PlonkResult(
        mode=mode,
        is_dry_run=dry_run,
        inspected_worktrees=len(candidates),
        removed_worktrees=tuple(removed_worktrees),
        removed_branches=tuple(removed_branches),
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
        The desired process exit code.

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
    return 0
