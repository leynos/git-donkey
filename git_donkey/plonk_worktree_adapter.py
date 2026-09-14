"""Adapt Git operations to the ports ``git-plonk`` cleans through.

The adapter wraps the mutating calls a completed cleanup makes: reading trunk
history for completion markers, inspecting a worktree before it is discarded,
and removing a worktree or a branch once the candidate is complete. Keeping
them behind one object lets the sweep be driven over a double, while the real
behaviour stays pinned by its own tests. Every refusal is reported rather than
raised, because a worktree Git will not discard and a branch Git will not
delete are partial results the summary names, not failures that abandon the
candidates queued behind them.

"""

from __future__ import annotations

import dataclasses
import logging
import typing as typ

from git import GitCommandError, InvalidGitRepositoryError, NoSuchPathError, Repo

from git_donkey import helpers, observability
from git_donkey._constants import GIT_PLONK_PREFIX
from git_donkey.plonk_records import _SkipReason

if typ.TYPE_CHECKING:
    from pathlib import Path

_LOGGER = logging.getLogger(__name__)


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
                f"{GIT_PLONK_PREFIX}: failed to remove worktree "
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
                f"{GIT_PLONK_PREFIX}: failed to delete branch '{branch_name}': {exc}"
            )
            return False
        return True
