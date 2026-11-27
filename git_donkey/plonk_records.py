"""Records and vocabulary shared across the git-plonk workflow.

The command's modes, its completion and skip evidence, the candidates it
selects, and the result it reports are values rather than behaviour, so they
live here: the workflow, the selection rules, and the summary rendering all
speak the same language without importing each other. The bounded
observability labels for the modes, signals, skip reasons, and unverified
reasons belong here too, because they translate this vocabulary into the one
owned by :mod:`git_donkey.observability`.

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


GIT_PLONK_PREFIX = "git-plonk"
"""Command name every git-plonk module uses in its diagnostics."""


class _PlonkMode(enum.StrEnum):
    """Supported cleanup modes for git-plonk."""

    DEFAULT = "default"
    SOFT = "soft"
    HARD = "hard"


type CompletionSignal = typ.Literal[
    "marker",
    "closing_keyword",
    "merge_commit",
    "branch_name",
    "github_merged_pr",
    "upstream_gone",
]
"""Fixed vocabulary for the evidence that completed a candidate."""

type UpstreamState = typ.Literal["unset", "present", "gone", "unknown"]
"""Fixed vocabulary for a branch's configured upstream."""


class _UnverifiedReason(enum.StrEnum):
    """Why a candidate could not be shown to be complete.

    Every member names something the user can act on, so the summary can
    explain each unverified worktree without quoting Git or GitHub output.
    """

    NO_SIGNAL = "no completion signal found"
    GITHUB_UNKNOWN = "GitHub completion lookup failed"
    UPSTREAM_UNSET = "no upstream branch configured"
    UPSTREAM_PRESENT = "upstream branch still exists"
    UPSTREAM_UNKNOWN = "upstream state unknown"


class _SkipReason(enum.StrEnum):
    """Why a completed candidate was left in place.

    Every member names something a user can act on, so the summary can explain
    each skip without quoting Git output or losing the distinction between a
    worktree that was deliberately kept and one that could not be discarded.
    """

    DIRTY = "uncommitted changes"
    UNAVAILABLE = "worktree directory is missing"
    UNREADABLE = "worktree state could not be read"
    REMOVAL_FAILED = "worktree removal failed"


# The enum values are user-facing sentences and the vocabularies live in
# ``git_donkey.observability``, so the bounded labels are mapped explicitly
# rather than derived from the values.
_SKIP_REASON_LABELS: dict[_SkipReason, observability.SkipReasonLabel] = {
    _SkipReason.DIRTY: "dirty",
    _SkipReason.UNAVAILABLE: "unavailable",
    _SkipReason.UNREADABLE: "unreadable",
    _SkipReason.REMOVAL_FAILED: "removal_failed",
}
_MODE_LABELS: dict[_PlonkMode, observability.CleanupModeLabel] = {
    _PlonkMode.DEFAULT: "default",
    _PlonkMode.SOFT: "soft",
    _PlonkMode.HARD: "hard",
}
_UNVERIFIED_REASON_LABELS: dict[
    _UnverifiedReason, observability.UnverifiedReasonLabel
] = {
    _UnverifiedReason.NO_SIGNAL: "no_signal",
    _UnverifiedReason.GITHUB_UNKNOWN: "github_unknown",
    _UnverifiedReason.UPSTREAM_UNSET: "upstream_unset",
    _UnverifiedReason.UPSTREAM_PRESENT: "upstream_present",
    _UnverifiedReason.UPSTREAM_UNKNOWN: "upstream_unknown",
}
_SIGNAL_LABELS: dict[CompletionSignal, observability.SignalLabel] = {
    "marker": "marker",
    "closing_keyword": "closing_keyword",
    "merge_commit": "merge_commit",
    "branch_name": "branch_name",
    "github_merged_pr": "github_merged_pr",
    "upstream_gone": "upstream_gone",
}


@dataclasses.dataclass(frozen=True, slots=True)
class _CompletionEvidence:
    """Why git-plonk judged one candidate complete.

    Attributes
    ----------
    signal : CompletionSignal
        Tier that supplied the evidence.
    detail : str
        User-facing detail the signal carries, such as the marker, the closing
        keyword, the merge-commit branch, or the pull-request number. Empty
        when the signal itself says everything.

    """

    signal: CompletionSignal
    detail: str = ""


@dataclasses.dataclass(frozen=True, slots=True)
class _PlonkCandidate:
    """A branched linked worktree the sweep may consider.

    Attributes
    ----------
    branch_name : str
        Local branch checked out in the worktree.
    worktree_path : Path
        Registered worktree path.
    marker : str | None
        Completion marker implied by the branch name, when the name follows a
        recognized convention.
    issue_number : int | None
        Issue number the branch name names, when it follows the issue
        convention. It enables the issue-only history matchers.

    """

    branch_name: str
    worktree_path: Path
    marker: str | None = None
    issue_number: int | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class _RemovableCandidate:
    """A candidate whose completion evidence makes it eligible for cleanup."""

    candidate: _PlonkCandidate
    evidence: _CompletionEvidence


@dataclasses.dataclass(frozen=True, slots=True)
class _RemovalEvidence:
    """The completion evidence behind one removed or planned worktree."""

    worktree_path: Path
    evidence: _CompletionEvidence


@dataclasses.dataclass(frozen=True, slots=True)
class _SkippedWorktree:
    """A completed candidate git-plonk left alone, and why."""

    worktree_path: Path
    reason: _SkipReason


@dataclasses.dataclass(frozen=True, slots=True)
class _UnverifiedWorktree:
    """A candidate no completion tier could resolve, and why."""

    worktree_path: Path
    reason: _UnverifiedReason


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
    """Summary of what a git-plonk run removed, skipped, or left unverified."""

    mode: _PlonkMode
    is_dry_run: bool = False
    inspected_worktrees: int = 0
    removed_worktrees: tuple[Path, ...] = ()
    removal_evidence: tuple[_RemovalEvidence, ...] = ()
    removed_branches: tuple[str, ...] = ()
    cleaned_paths: tuple[Path, ...] = ()
    skipped_worktrees: tuple[_SkippedWorktree, ...] = ()
    unverified_worktrees: tuple[_UnverifiedWorktree, ...] = ()
    failed_branch_deletions: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True, slots=True)
class _PlonkContext:
    """Resolved repository state used by one git-plonk run.

    Attributes
    ----------
    repo_home : Repo
        Repository Git commands run against.
    stanzas : list[dict[str, object]]
        Parsed ``git worktree list --porcelain`` output.
    worktrees_root : Path
        Directory whose linked worktrees the sweep may clean.
    anchor_dir : Path
        Main worktree, or bare repository directory, the command anchors on.
    trunk_ref : str
        Fetched remote-tracking ref of the advertised default branch.
    invoking_worktree : Path | None
        Worktree the command was invoked from, which it never removes.
    remote : str
        Name of the principal remote.
    branch : str
        Default branch the principal remote advertises.
    remote_url : str
        Raw URL configured for ``remote``, read without applying ``insteadOf``.
        The GitHub fallback is derived from it; an empty value disables one
        that would otherwise be available.

    """

    repo_home: Repo
    stanzas: list[dict[str, object]]
    worktrees_root: Path
    anchor_dir: Path
    trunk_ref: str
    invoking_worktree: Path | None
    remote: str
    branch: str
    remote_url: str


@dataclasses.dataclass(frozen=True, slots=True)
class _SoftPlonkContext:
    """Resolved repository state used by a soft git-plonk run."""

    stanzas: list[dict[str, object]]
    worktrees_root: Path
    anchor_dir: Path
