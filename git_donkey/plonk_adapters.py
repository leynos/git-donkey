"""Infrastructure adapters for the git-plonk cleanup workflow.

The workflow decides what to clean; these adapters are the boundary that talks
to Git and the filesystem. :class:`_GitWorktreeAdapter` reads trunk history and
a branch's upstream configuration, performs the unforced worktree removal, and
deletes a local branch only when the caller asks for hard mode.
:class:`_FilesystemCleanupAdapter` removes the generated directories soft mode
owns. Every destructive call is reported as a boolean rather than raised, so a
refusal can be reported and the sweep can continue.

Nothing in this module decides whether a worktree is complete or whether it is
safe to discard; those are policy questions answered elsewhere.
"""

from __future__ import annotations

import logging
import shutil
import typing as typ

from git import GitCommandError, InvalidGitRepositoryError, NoSuchPathError, Repo

from git_donkey import helpers, observability
from git_donkey.plonk_records import GIT_PLONK_PREFIX, UpstreamState, _SkipReason

if typ.TYPE_CHECKING:
    from pathlib import Path

_LOGGER = logging.getLogger(__name__)
# `git ls-remote --exit-code` reports "no matching refs" with this status, and
# `git config --get` reports an unset key with this one.
_LS_REMOTE_NO_MATCH_STATUS = 2
_CONFIG_KEY_UNSET_STATUS = 1
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


class _GitWorktreeAdapter:
    """GitPython-backed adapter for history, inspection, and mutation."""

    def __init__(self, repo: Repo) -> None:
        """Keep the repository every query and mutation runs against."""
        self.repo = repo

    def history_messages(self, ref: str) -> typ.Iterator[str]:
        """Return commit messages from ``ref`` history."""
        for commit in self.repo.iter_commits(ref):
            match commit.message:
                case bytes() as message:
                    yield message.decode(errors="replace")
                case message:
                    yield message

    def upstream_state(self, branch_name: str) -> UpstreamState:
        """Return whether ``branch_name``'s configured upstream exists.

        The branch's own configuration names the remote and merge ref; the
        remote is then queried with ``git ls-remote --exit-code --heads``. The
        distinctions matter to the caller: ``unset`` means the branch was never
        pushed, ``present`` means it still exists, ``gone`` means it was
        deleted, and ``unknown`` means the remote could not answer. Only
        ``gone`` is completion evidence, and even that is opt-in because a
        deleted branch cannot distinguish a merged change from an abandoned
        one.

        Parameters
        ----------
        branch_name : str
            Local branch whose upstream configuration is read.

        Returns
        -------
        UpstreamState
            The state of the configured upstream, or ``unset`` when the branch
            configures none.

        """
        try:
            remote = self.repo.git.config("--get", f"branch.{branch_name}.remote")
            merge_ref = self.repo.git.config("--get", f"branch.{branch_name}.merge")
        except GitCommandError as exc:
            return "unset" if exc.status == _CONFIG_KEY_UNSET_STATUS else "unknown"
        if not remote or not merge_ref:
            return "unset"
        if not merge_ref.startswith("refs/heads/"):
            # A merge ref outside refs/heads is not a branch this check knows
            # how to query, so the honest answer is unknown.
            return "unknown"
        merge_branch = merge_ref.removeprefix("refs/heads/")
        try:
            self.repo.git.ls_remote(
                "--exit-code",
                "--heads",
                remote,
                f"refs/heads/{merge_branch}",
            )
        except GitCommandError as exc:
            if exc.status == _LS_REMOTE_NO_MATCH_STATUS:
                return "gone"
            return "unknown"
        return "present"

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
