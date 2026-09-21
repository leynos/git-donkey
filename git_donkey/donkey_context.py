"""What a git-donkey run is described by, and what it resolved to.

The options the run was asked for, the repository state it read, and the trunk
and base it resolved that state into: the values the workflow reads, kept here
so that the module which does the work is about the doing. Nothing here opens
a repository or runs a command, so every value is one a run has already read.
"""

from __future__ import annotations

import dataclasses
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path

    from git import Repo


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


@dataclasses.dataclass(frozen=True, slots=True)
class _Base:
    """The base a new branch is created from, as this run resolved it.

    The ref and the commit travel together because they are one observation: a
    worktree started from one while the record names the other would describe a
    birth that never happened.

    Parameters
    ----------
    ref
        Ref the base was selected by, as the selection named it.
    commit
        Commit that ref resolved to, or ``None`` when no ref of that name
        exists here.

    """

    ref: str
    commit: str | None
