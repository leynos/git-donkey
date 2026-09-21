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
    deleted once its worktree is gone, and only ever entombed once its tip has
    been preserved. ``entombed`` is set for a dry run too, where it means the
    tombstone the run planned rather than one it wrote.
    """

    skip_reason: _SkipReason | None = None
    branch_deletion_failed: bool = False
    entombed: bool = False
    entomb_failed: bool = False


@dataclasses.dataclass(frozen=True, slots=True)
class _PlonkResult:
    """Summary of filesystem and Git state removed by a git-plonk run.

    The record-lifecycle fields are empty for a soft run, which leaves Git
    state untouched, and ``tombstone_expire`` names the retention window the
    run applied, so a summary that pruned anything can say what by.
    """

    mode: _PlonkMode
    is_dry_run: bool = False
    inspected_worktrees: int = 0
    removed_worktrees: tuple[Path, ...] = ()
    removed_branches: tuple[str, ...] = ()
    cleaned_paths: tuple[Path, ...] = ()
    skipped_worktrees: tuple[_SkippedWorktree, ...] = ()
    failed_branch_deletions: tuple[str, ...] = ()
    entombed_branches: tuple[str, ...] = ()
    failed_entombments: tuple[str, ...] = ()
    swept_records: tuple[str, ...] = ()
    unrescuable_records: tuple[str, ...] = ()
    pruned_tombstones: tuple[str, ...] = ()
    tombstone_expire: str | None = None

    def is_incomplete(self) -> bool:
        """Return whether an action the run was asked for did not happen."""
        return bool(self.failed_branch_deletions or self.failed_entombments)


@dataclasses.dataclass(slots=True)
class _CleanupTally:
    """Running totals of what one completed cleanup has done so far.

    Folding each :class:`_CandidateOutcome` into these lists is fiddlier than
    it looks — a candidate whose tip was preserved but whose branch Git refused
    to delete appears in two of them, and a candidate whose tombstone could not
    be written keeps its branch — so the rule lives here rather than in the
    loop that drives the candidates.
    """

    removed_worktrees: list[Path] = dataclasses.field(default_factory=list)
    removed_branches: list[str] = dataclasses.field(default_factory=list)
    skipped_worktrees: list[_SkippedWorktree] = dataclasses.field(default_factory=list)
    failed_branch_deletions: list[str] = dataclasses.field(default_factory=list)
    entombed_branches: list[str] = dataclasses.field(default_factory=list)
    failed_entombments: list[str] = dataclasses.field(default_factory=list)

    def add(
        self,
        candidate: _PlonkCandidate,
        outcome: _CandidateOutcome,
        mode: _PlonkMode,
    ) -> None:
        """Fold one cleaned candidate's ``outcome`` into the totals.

        Parameters
        ----------
        candidate : _PlonkCandidate
            Candidate the outcome came from.
        outcome : _CandidateOutcome
            What cleaning that candidate contributed.
        mode : _PlonkMode
            Cleanup mode; only hard mode deletes branches at all.

        """
        if outcome.skip_reason is not None:
            self.skipped_worktrees.append(
                _SkippedWorktree(candidate.worktree_path, outcome.skip_reason)
            )
            return
        self.removed_worktrees.append(candidate.worktree_path)
        if outcome.entombed:
            self.entombed_branches.append(candidate.branch_name)
        if outcome.entomb_failed:
            self.failed_entombments.append(candidate.branch_name)
            return
        if outcome.branch_deletion_failed:
            self.failed_branch_deletions.append(candidate.branch_name)
        elif mode is _PlonkMode.HARD:
            self.removed_branches.append(candidate.branch_name)


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
