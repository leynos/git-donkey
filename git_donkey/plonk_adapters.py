"""Infrastructure adapters for the git-plonk cleanup workflow.

The workflow decides what to clean; these adapters are the boundary that talks
to Git and the filesystem. :class:`_GitWorktreeAdapter` reads trunk history and
a branch's upstream configuration, performs the unforced worktree removal, and
deletes a local branch only when the caller asks for hard mode.
:class:`_FilesystemCleanupAdapter` removes the generated directories soft mode
owns. The Git mutation methods report a refusal as a boolean so the sweep can
report it and continue; filesystem removal is not caught, so a soft-cleanup
error stops the run before any summary is printed.

Nothing in this module decides whether a worktree is complete or whether it is
safe to discard; those are policy questions answered elsewhere.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]  # bounded ls-remote
import typing as typ

from git import GitCommandError, InvalidGitRepositoryError, NoSuchPathError, Repo

from git_donkey import helpers, observability
from git_donkey.plonk_records import GIT_PLONK_PREFIX, UpstreamState, _SkipReason

if typ.TYPE_CHECKING:
    from pathlib import Path

_LOGGER = logging.getLogger(__name__)
# `git ls-remote --exit-code` reports "no matching refs" with this status, and
# `git config --get-all` reports an unset key with this one.
_LS_REMOTE_NO_MATCH_STATUS = 2
_CONFIG_KEY_UNSET_STATUS = 1
_LS_REMOTE_TIMEOUT_SECONDS = 10.0
_GIT_TERMINAL_PROMPT_ENV = "GIT_TERMINAL_PROMPT"
# GitPython reads these from the process environment when it constructs a
# repository. A hook that exports them for the invoking worktree would make the
# preflight inspect that worktree instead of the candidate.
_REPOSITORY_LOCATION_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)
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


@contextlib.contextmanager
def _clean_git_environment() -> typ.Iterator[None]:
    """Temporarily remove repository-location variables from the environment."""
    saved = {
        key: os.environ.pop(key)
        for key in _REPOSITORY_LOCATION_ENV
        if key in os.environ
    }
    try:
        yield
    finally:
        os.environ.update(saved)


class _GitWorktreeAdapter:
    """GitPython-backed adapter for history, inspection, and mutation."""

    def __init__(self, repo: Repo) -> None:
        """Keep the repository every query and mutation runs against."""
        self.repo = repo

    def _ls_remote(
        self,
        arguments: typ.Sequence[str],
    ) -> subprocess.CompletedProcess[str] | None:
        """Run a bounded ``ls-remote``, timing it and recording the outcome."""
        with observability.get_recorder().span("upstream_lookup"):
            completed = self._run_ls_remote_process(arguments)
        observability.get_recorder().record(
            observability.Observation(
                operation="upstream_lookup",
                outcome="failure" if completed is None else "success",
                error_kind="os_error" if completed is None else None,
            )
        )
        return completed

    def _run_ls_remote_process(
        self,
        arguments: typ.Sequence[str],
    ) -> subprocess.CompletedProcess[str] | None:
        """Run ``git ls-remote`` with a deadline, or return ``None`` if it cannot.

        GitPython offers no timeout, so a remote that accepts a connection and
        then stalls could block the sweep indefinitely. This helper runs the
        same Git command directly, closes standard input, disables terminal
        prompts, and maps a timeout, decoding failure, or execution error to
        ``None`` so the caller can report ``unknown`` rather than hang.

        Returns
        -------
        subprocess.CompletedProcess[str] | None
            The completed process, or ``None`` when the deadline or an execution
            error prevented an answer.

        """
        executable = shutil.which("git")
        if executable is None:
            return None
        environment = os.environ.copy()
        environment[_GIT_TERMINAL_PROMPT_ENV] = "0"
        try:
            return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
                [executable, "ls-remote", *arguments],
                cwd=self.repo.working_tree_dir or self.repo.git_dir,
                capture_output=True,
                text=True,
                timeout=_LS_REMOTE_TIMEOUT_SECONDS,
                check=False,
                stdin=subprocess.DEVNULL,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
            return None

    @staticmethod
    def _mentions_exact_ref(output: str, ref: str) -> bool:
        """Return whether ``git ls-remote`` output names ``ref`` exactly."""
        return any(line.rpartition("\t")[2] == ref for line in output.split("\n"))

    def _ls_remote_state(self, remote: str, merge_ref: str) -> UpstreamState:
        """Return the upstream state a bounded ``ls-remote`` reports."""
        completed = self._ls_remote(
            ("--exit-code", "--heads", remote, merge_ref),
        )
        if completed is None:
            return "unknown"
        if completed.returncode == _LS_REMOTE_NO_MATCH_STATUS:
            return "gone"
        if completed.returncode != 0:
            return "unknown"
        # `ls-remote` patterns match the tail of a ref, so a longer branch such
        # as `other/refs/heads/<name>` can make the command exit 0 without the
        # configured ref existing. Require the exact ref before saying present.
        return (
            "present"
            if self._mentions_exact_ref(completed.stdout, merge_ref)
            else "gone"
        )

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
        remote is then queried with a bounded ``git ls-remote --exit-code
        --heads``. Git uses the last ``branch.<name>.remote`` value and the
        first ``branch.<name>.merge`` value for ``@{upstream}``, so those are
        the entries checked. The distinctions matter to the caller: ``unset``
        means the branch was never pushed, ``present`` means it still exists,
        ``gone`` means it was deleted, and ``unknown`` means the remote could
        not answer. Only ``gone`` is completion evidence, and even that is
        opt-in because a deleted branch cannot distinguish a merged change from
        an abandoned one.

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
            # `--get` returns the effective (last) remote; Git reads the first
            # merge value for @{upstream}, so that list keeps its order.
            remote = self.repo.git.config("--get", f"branch.{branch_name}.remote")
            merges = [
                line
                for line in self.repo.git.config(
                    "--get-all", f"branch.{branch_name}.merge"
                ).split("\n")
                if line
            ]
        except GitCommandError as exc:
            return "unset" if exc.status == _CONFIG_KEY_UNSET_STATUS else "unknown"
        merge_ref = merges[0] if merges else ""
        if not remote or not merge_ref:
            return "unset"
        if not merge_ref.startswith("refs/heads/"):
            # A merge ref outside refs/heads is not a branch this check knows
            # how to query, so the honest answer is unknown.
            return "unknown"
        return self._ls_remote_state(remote, merge_ref)

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
        with (
            observability.get_recorder().span("worktree_preflight"),
            _clean_git_environment(),
        ):
            if not worktree_path.is_dir():
                return _SkipReason.UNAVAILABLE
            try:
                with Repo(worktree_path) as worktree_repo:
                    if worktree_repo.is_dirty(untracked_files=True):
                        return _SkipReason.DIRTY
            except (InvalidGitRepositoryError, NoSuchPathError):
                return _SkipReason.UNAVAILABLE
            except (GitCommandError, UnicodeError, OSError):
                # An unreadable index, worktree state, or path name must not
                # abort the batch; report it as a skip and continue.
                return _SkipReason.UNREADABLE
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
