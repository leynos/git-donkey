"""Records and vocabulary shared across the git-plonk workflow.

The command's modes, its reasons for leaving a worktree in place, the
candidates it selects, and the result it reports are values rather than
behaviour, so they live here: the workflow, the selection rules, and the
summary rendering all speak the same language without importing each other.
The bounded observability labels for the modes and skip reasons belong here
too, because they translate this vocabulary into the one owned by
:mod:`git_donkey.observability`.

Nothing in this module reads Git state or touches the filesystem; the
repository handles the records hold are annotations only.
"""

from __future__ import annotations

import dataclasses
import enum
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

    from git import Repo

    from git_donkey import observability


class _PlonkMode(enum.StrEnum):
    """Supported cleanup modes for git-plonk."""

    DEFAULT = "default"
    SOFT = "soft"
    HARD = "hard"


class _SkipReason(enum.StrEnum):
    """Why a completed candidate was left in place.

    Every member names something a user can act on, so the summary can explain
    each skip without quoting Git output or losing the distinction between a
    worktree that was deliberately kept and one that could not be discarded.
    """

    DIRTY = "uncommitted changes"
    UNAVAILABLE = "worktree directory is missing"
    REMOVAL_FAILED = "worktree removal failed"


# The enum values are user-facing sentences and the vocabularies live in
# ``git_donkey.observability``, so the bounded labels are mapped explicitly
# rather than derived from the values.
_SKIP_REASON_LABELS: dict[_SkipReason, observability.SkipReasonLabel] = {
    _SkipReason.DIRTY: "dirty",
    _SkipReason.UNAVAILABLE: "unavailable",
    _SkipReason.REMOVAL_FAILED: "removal_failed",
}
_MODE_LABELS: dict[_PlonkMode, observability.CleanupModeLabel] = {
    _PlonkMode.DEFAULT: "default",
    _PlonkMode.SOFT: "soft",
    _PlonkMode.HARD: "hard",
}


@dataclasses.dataclass(frozen=True, slots=True)
class _PlonkCandidate:
    """A linked worktree eligible for completion-marker cleanup."""

    branch_name: str
    worktree_path: Path
    marker: str


@dataclasses.dataclass(frozen=True, slots=True)
class _SkippedWorktree:
    """A completed candidate git-plonk left alone, and why."""

    worktree_path: Path
    reason: _SkipReason


@dataclasses.dataclass(frozen=True, slots=True)
class _CandidateOutcome:
    """What one completed candidate contributed to a cleanup run.

    A candidate can be skipped, removed, or removed with its branch left
    behind. The failure fields are mutually exclusive: a branch is only ever
    deleted once its worktree is gone.
    """

    skip_reason: _SkipReason | None = None
    branch_deletion_failed: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class _PlonkResult:
    """Summary of filesystem and Git state removed by a git-plonk run."""

    mode: _PlonkMode
    is_dry_run: bool = False
    inspected_worktrees: int = 0
    removed_worktrees: tuple[Path, ...] = ()
    removed_branches: tuple[str, ...] = ()
    cleaned_paths: tuple[Path, ...] = ()
    skipped_worktrees: tuple[_SkippedWorktree, ...] = ()
    failed_branch_deletions: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class _PlonkContext:
    """Resolved repository state used by one git-plonk run."""

    repo_home: Repo
    stanzas: list[dict[str, object]]
    worktrees_root: Path
    trunk_ref: str
    invoking_worktree: Path | None


@dataclasses.dataclass(frozen=True, slots=True)
class _SoftPlonkContext:
    """Resolved repository state used by a soft git-plonk run."""

    stanzas: list[dict[str, object]]
    worktrees_root: Path
