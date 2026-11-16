"""Select the git-donkey worktrees git-plonk may clean.

``git worktree list --porcelain`` output is parsed into stanzas by
:mod:`git_donkey.helpers`; this module turns those stanzas into the linked
worktrees `git donkey` created, and into the completed candidates whose branch
encodes a completion marker the repository's policy recognises.

Selection is pure. It reads stanza values and path objects, never Git state or
the filesystem, and nothing here removes or mutates anything.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

from git_donkey import plonk_policy
from git_donkey.plonk_records import _PlonkCandidate

_REFS_HEADS_PREFIX = "refs/heads/"


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


def _is_cleanup_target(
    worktree_path: Path,
    worktrees_root: Path,
    anchor_dir: Path,
) -> bool:
    """Return whether git-plonk may treat ``worktree_path`` as donkey's own.

    The anchor is excluded by identity rather than by its position in the
    listing. git-donkey never owns the main worktree or the bare repository, so
    neither may be cleaned even when a configured root happens to contain it,
    and a caller that supplies only linked-worktree stanzas still keeps every
    one of them.

    Returns
    -------
    bool
        ``True`` when ``worktree_path`` is a linked worktree under the root and
        is not the anchor.

    """
    return worktree_path != anchor_dir and _is_git_donkey_worktree(
        worktree_path, worktrees_root
    )


def _donkey_worktree_paths(
    stanzas: typ.Iterable[dict[str, object]],
    worktrees_root: Path,
    anchor_dir: Path,
) -> list[Path]:
    """Return linked worktree paths owned by git-donkey, never the anchor."""
    paths: list[Path] = []
    for stanza in stanzas:
        worktree_path = _worktree_path_from_stanza(stanza)
        if worktree_path is None:
            continue
        if _is_cleanup_target(worktree_path, worktrees_root, anchor_dir):
            paths.append(worktree_path)
    return paths


def _donkey_worktree_candidates(
    stanzas: typ.Iterable[dict[str, object]],
    worktrees_root: Path,
    anchor_dir: Path,
) -> list[_PlonkCandidate]:
    """Return recognized git-donkey worktrees with completion markers."""
    candidates: list[_PlonkCandidate] = []
    for stanza in stanzas:
        branch_name = _branch_name_from_stanza(stanza)
        worktree_path = _worktree_path_from_stanza(stanza)
        if branch_name is None or worktree_path is None:
            continue
        if not _is_cleanup_target(worktree_path, worktrees_root, anchor_dir):
            continue
        marker = plonk_policy.completion_marker_for_branch(branch_name)
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
