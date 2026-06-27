"""Implement the git-plonk worktree cleanup command.

The command cleans worktrees created by ``git donkey``. Default and hard modes
use branch names to derive completion markers and then scan ``HEAD`` history
for matching merge messages. Soft mode only removes generated directories from
linked worktrees and leaves Git state untouched.
"""

from __future__ import annotations

import dataclasses
import enum
import os
import re
import shutil
import typing as typ
from pathlib import Path

from git import GitCommandError, Repo

from git_donkey import donkey, helpers

_GIT_PLONK_PREFIX = "git-plonk"
_ISSUE_BRANCH_PATTERN = re.compile(r"^issue-(\d+)-")
_ROADMAP_BRANCH_PATTERN = re.compile(r"^(?:(\w+)-)?(\d+)-(\d+)-(\d+)(\w+)?-(?:(\d+)-)?")
_REFS_HEADS_PREFIX = "refs/heads/"
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
    removed_worktrees: tuple[Path, ...] = ()
    removed_branches: tuple[str, ...] = ()
    cleaned_paths: tuple[Path, ...] = ()


def _completion_marker_for_branch(branch_name: str) -> str | None:
    """Return the completion marker implied by ``branch_name``, if recognized."""
    issue_match = _ISSUE_BRANCH_PATTERN.match(branch_name)
    if issue_match:
        return f"(#{issue_match.group(1)})"

    roadmap_match = _ROADMAP_BRANCH_PATTERN.match(branch_name)
    if roadmap_match is None:
        return None

    namespace, major, minor, patch, suffix, task = roadmap_match.groups()
    patch_reference = f"{patch}{suffix or ''}"
    parts = [major, minor, patch_reference]
    if namespace:
        parts.insert(0, namespace)
    if task:
        parts.append(task)
    return f"({'.'.join(parts)})"


def _has_completion_marker(messages: typ.Iterable[str], marker: str) -> bool:
    """Return whether any commit message contains ``marker`` or its dotted form."""
    marker_body = marker.removeprefix("(").removesuffix(")")
    marker_pattern = re.compile(rf"\({re.escape(marker_body)}\.?\)")
    return any(marker_pattern.search(message) is not None for message in messages)


def _completed_candidates(
    candidates: typ.Iterable[_PlonkCandidate],
    messages: typ.Iterable[str],
) -> list[_PlonkCandidate]:
    """Return candidates whose completion markers appear in commit history."""
    history_messages = tuple(messages)
    return [
        candidate
        for candidate in candidates
        if _has_completion_marker(history_messages, candidate.marker)
    ]


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


def _history_messages(repo: Repo) -> tuple[str, ...]:
    """Return commit messages from ``HEAD`` history."""
    messages: list[str] = []
    for commit in repo.iter_commits("HEAD"):
        message = commit.message
        if isinstance(message, bytes):
            messages.append(message.decode(errors="replace"))
            continue
        messages.append(message)
    return tuple(messages)


def _remove_soft_targets(worktree_path: Path) -> list[Path]:
    """Remove generated directories from ``worktree_path`` and return removals."""
    removed_paths: list[Path] = []
    for target_name in _SOFT_TARGET_NAMES:
        target_path = worktree_path / target_name
        if target_path.is_symlink():
            target_path.unlink()
            removed_paths.append(target_path)
            continue
        if not target_path.is_dir():
            continue
        shutil.rmtree(target_path)
        removed_paths.append(target_path)
    return removed_paths


def _render_summary(result: _PlonkResult) -> str:
    """Render a deterministic human-readable summary for ``result``."""
    lines: list[str] = [f"git-plonk: mode={result.mode.value}"]
    has_removals = any((
        result.removed_worktrees,
        result.removed_branches,
        result.cleaned_paths,
    ))
    if not has_removals:
        lines.append("No matching git donkey worktrees found.")
        return "\n".join(lines)

    if result.removed_worktrees:
        lines.append("Removed worktrees:")
        lines.extend(f"- {path}" for path in result.removed_worktrees)
    if result.removed_branches:
        lines.append("Removed branches:")
        lines.extend(f"- {branch}" for branch in result.removed_branches)
    if result.cleaned_paths:
        lines.append("Removed generated paths:")
        lines.extend(f"- {path}" for path in result.cleaned_paths)
    return "\n".join(lines)


def _load_plonk_context() -> tuple[Repo, list[dict[str, object]], Path]:
    """Load the main repository, worktree stanzas, and git-donkey root."""
    repo_cwd = helpers._find_repo(_GIT_PLONK_PREFIX)
    stanzas = helpers._parse_worktree_porcelain(repo_cwd)
    home_dir = helpers._main_worktree_path_from_list(stanzas, _GIT_PLONK_PREFIX)
    os.chdir(home_dir)
    repo_home = Repo(home_dir)
    worktrees_root = donkey._worktrees_root(home_dir)
    return repo_home, stanzas, worktrees_root


def _run_soft(
    stanzas: typ.Iterable[dict[str, object]], worktrees_root: Path
) -> _PlonkResult:
    """Run soft cleanup for every git-donkey worktree."""
    cleaned_paths: list[Path] = []
    for worktree_path in _donkey_worktree_paths(stanzas, worktrees_root):
        cleaned_paths.extend(_remove_soft_targets(worktree_path))
    return _PlonkResult(
        mode=_PlonkMode.SOFT,
        cleaned_paths=tuple(cleaned_paths),
    )


def _remove_worktree(repo: Repo, worktree_path: Path) -> None:
    """Remove a linked worktree with Git's worktree machinery."""
    try:
        repo.git.worktree("remove", "--force", worktree_path.as_posix())
    except GitCommandError as exc:
        helpers._die(
            _GIT_PLONK_PREFIX,
            f"failed to remove worktree '{worktree_path}': {exc}",
            1,
        )


def _delete_branch(repo: Repo, branch_name: str) -> None:
    """Delete ``branch_name`` after its completed worktree has been removed."""
    try:
        repo.git.branch("-D", branch_name)
    except GitCommandError as exc:
        helpers._die(
            _GIT_PLONK_PREFIX,
            f"failed to delete branch '{branch_name}': {exc}",
            1,
        )


def _run_completed_cleanup(
    repo: Repo,
    stanzas: typ.Iterable[dict[str, object]],
    worktrees_root: Path,
    mode: _PlonkMode,
) -> _PlonkResult:
    """Remove completed worktrees and optionally their local branches."""
    candidates = _donkey_worktree_candidates(stanzas, worktrees_root)
    completed_candidates = _completed_candidates(candidates, _history_messages(repo))
    removed_worktrees: list[Path] = []
    removed_branches: list[str] = []

    for candidate in completed_candidates:
        _remove_worktree(repo, candidate.worktree_path)
        removed_worktrees.append(candidate.worktree_path)
        if mode is _PlonkMode.HARD:
            _delete_branch(repo, candidate.branch_name)
            removed_branches.append(candidate.branch_name)

    return _PlonkResult(
        mode=mode,
        removed_worktrees=tuple(removed_worktrees),
        removed_branches=tuple(removed_branches),
    )


def run_git_plonk(
    *,
    soft: bool = False,
    hard: bool = False,
) -> int:
    """Run the git-plonk cleanup workflow.

    Parameters
    ----------
    soft : bool, optional
        Remove generated directories from git-donkey worktrees without removing
        worktrees or branches.
    hard : bool, optional
        Remove completed git-donkey worktrees and delete their local branches.

    Returns
    -------
    int
        The desired process exit code.

    """
    if soft and hard:
        helpers._die(_GIT_PLONK_PREFIX, "--soft and --hard are mutually exclusive", 2)

    repo_home, stanzas, worktrees_root = _load_plonk_context()
    if soft:
        result = _run_soft(stanzas, worktrees_root)
    else:
        mode = _PlonkMode.HARD if hard else _PlonkMode.DEFAULT
        result = _run_completed_cleanup(repo_home, stanzas, worktrees_root, mode)

    print(_render_summary(result))
    return 0
