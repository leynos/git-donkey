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

import dataclasses
import logging
import os
import re
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]  # bounded status
import typing as typ

from git import GitCommandError, Repo

from git_donkey import helpers, observability, plonk_remote
from git_donkey.plonk_records import (
    GIT_PLONK_PREFIX,
    UpstreamState,
    _HistoryMessage,
    _SkipReason,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

_LOGGER = logging.getLogger(__name__)
# `git config --get-all` reports an unset key with this status.
_CONFIG_KEY_UNSET_STATUS = 1
_LOCAL_GIT_TIMEOUT_SECONDS = 10.0
_GIT_TERMINAL_PROMPT_ENV = "GIT_TERMINAL_PROMPT"
# One query returns the branch, its commit, and every uncommitted path.
_WORKTREE_STATUS_ARGUMENTS = (
    "status",
    "--porcelain=v2",
    "--branch",
    "--untracked-files=normal",
)
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


class _LocalGitRunner(typ.Protocol):
    """Callable that runs one local Git query against a worktree."""

    def __call__(
        self, arguments: typ.Sequence[str], cwd: Path, /
    ) -> subprocess.CompletedProcess[str]:
        """Run ``arguments`` in ``cwd`` and return the completed process."""


@dataclasses.dataclass(frozen=True, slots=True)
class _WorktreeState:
    """The part of a worktree's status the preflight needs.

    Attributes
    ----------
    is_dirty : bool
        Whether the worktree has modified, staged, or untracked files.
    branch : str | None
        Checked-out branch, or ``None`` when HEAD is detached.
    commit : str | None
        Checked-out commit, or ``None`` when HEAD is unborn.

    """

    is_dirty: bool
    branch: str | None
    commit: str | None


def _run_local_git(
    arguments: typ.Sequence[str],
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    """Run a bounded local Git query with a clean, non-interactive environment.

    Repository-location variables from the calling hook are removed from this
    process's copy of the environment only, so the query cannot be redirected
    to another repository without the process state being mutated.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The completed process.

    Raises
    ------
    FileNotFoundError
        When no ``git`` executable can be found.
    OSError
        When the process cannot start.
    subprocess.SubprocessError
        When the query exceeds its deadline.

    """
    executable = shutil.which("git")
    if executable is None:
        msg = "git is not installed"
        raise FileNotFoundError(msg)
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in _REPOSITORY_LOCATION_ENV
    }
    environment[_GIT_TERMINAL_PROMPT_ENV] = "0"
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [executable, *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=_LOCAL_GIT_TIMEOUT_SECONDS,
        check=False,
        stdin=subprocess.DEVNULL,
        env=environment,
    )


def _status_failure(completed: subprocess.CompletedProcess[str]) -> _SkipReason:
    """Classify a failed status query as an unavailable or unreadable worktree."""
    if "not a git repository" in completed.stderr:
        return _SkipReason.UNAVAILABLE
    return _SkipReason.UNREADABLE


def _identity_value(line: str, prefix: str, unset: str) -> str | None:
    """Return the value after ``prefix``, or ``None`` when it is ``unset``."""
    value = line.removeprefix(prefix)
    return None if value == unset else value


def _branch_identity(
    lines: typ.Iterable[str],
) -> tuple[str | None, str | None]:
    """Return the checked-out branch and commit from porcelain header lines."""
    branch: str | None = None
    commit: str | None = None
    for line in lines:
        if line.startswith("# branch.oid "):
            commit = _identity_value(line, "# branch.oid ", "(initial)")
        elif line.startswith("# branch.head "):
            branch = _identity_value(line, "# branch.head ", "(detached)")
    return branch, commit


def _parse_worktree_status(
    completed: subprocess.CompletedProcess[str],
) -> _WorktreeState | _SkipReason:
    """Interpret ``git status --porcelain=v2 --branch`` output."""
    if completed.returncode != 0:
        return _status_failure(completed)
    lines = completed.stdout.splitlines()
    branch, commit = _branch_identity(lines)
    is_dirty = any(not line.startswith("#") for line in lines)
    return _WorktreeState(is_dirty=is_dirty, branch=branch, commit=commit)


def _matches_worktree_state(
    state: _WorktreeState,
    *,
    expected_branch: str | None,
    expected_commit: str | None,
) -> bool:
    """Return whether ``state`` still matches the recorded identity."""
    branch_matches = expected_branch is None or state.branch == expected_branch
    commit_matches = expected_commit is None or state.commit == expected_commit
    return branch_matches and commit_matches


class _GitWorktreeAdapter:
    """GitPython-backed adapter for history, inspection, and mutation."""

    def __init__(
        self,
        repo: Repo,
        *,
        remote_probe: plonk_remote._RemoteRefProbe | None = None,
        local_git: _LocalGitRunner = _run_local_git,
    ) -> None:
        """Keep the repository and the runners every query uses."""
        self.repo = repo
        working_directory = (
            getattr(repo, "working_tree_dir", None)
            or getattr(repo, "git_dir", None)
            or "."
        )
        self.remote_probe = remote_probe or plonk_remote._RemoteRefProbe(
            str(working_directory)
        )
        self.local_git = local_git

    def history_messages(self, ref: str) -> typ.Iterator[_HistoryMessage]:
        """Return commit messages from ``ref`` history with their merge status.

        The parent count travels with the message because classic merge
        evidence is only valid for a real merge commit; a one-parent commit
        whose body copies the merge syntax must not complete a branch.

        Yields
        ------
        _HistoryMessage
            One entry per commit, newest first, with its merge status.
        """
        for commit in self.repo.iter_commits(ref):
            message = commit.message
            text = (
                message.decode(errors="replace")
                if isinstance(message, bytes)
                else message
            )
            yield _HistoryMessage(text, is_merge_commit=len(commit.parents) > 1)

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
        if self._worktree_config_enabled():
            # With per-worktree configuration, the effective upstream can
            # differ per worktree. This adapter reads the shared configuration,
            # so it cannot answer honestly and leaves the candidate unverified.
            return "unknown"
        try:
            # `--get` returns the effective (last) remote; Git reads the first
            # merge value for @{upstream}, so that entry decides the answer.
            remote = self.repo.git.config("--get", f"branch.{branch_name}.remote")
            # `-z` keeps embedded newlines inside one value; splitting on LF
            # would truncate a malformed value into a plausible ref.
            raw_merges = self.repo.git.config(
                "-z", "--get-all", f"branch.{branch_name}.merge"
            )
        except GitCommandError as exc:
            return "unset" if exc.status == _CONFIG_KEY_UNSET_STATUS else "unknown"
        merges = raw_merges.split("\0")
        if raw_merges.endswith("\0"):
            merges.pop()
        merge_ref = merges[0] if merges else ""
        if not remote:
            return "unset"
        if not merge_ref:
            # Git does not fall through to a later merge value when the first
            # one is empty, so this branch has no resolvable upstream.
            return "unknown"
        if not plonk_remote.is_exact_branch_ref(merge_ref):
            # `ls-remote` treats its argument as a pattern. A wildcard or an
            # invalid ref could match a ref it does not name and make a deleted
            # branch look present, or vice versa, so fail closed.
            return "unknown"
        return self.remote_probe.branch_state(remote, merge_ref)

    def _worktree_config_enabled(self) -> bool:
        """Return whether Git's per-worktree configuration extension is on.

        ``--bool`` interprets the setting the way Git does: a valueless entry
        is true, an explicit false is false, and an unset key exits 1. A value
        Git cannot interpret makes the check fail closed, because the
        extension's state is then unknown and a per-worktree override could
        still be in effect.

        Returns
        -------
        bool
            ``True`` when per-worktree configuration may be in effect or the
            setting cannot be interpreted.

        """
        try:
            value = self.repo.git.config("--bool", "--get", "extensions.worktreeConfig")
        except GitCommandError as exc:
            return exc.status != _CONFIG_KEY_UNSET_STATUS
        return value.strip() == "true"

    def skip_reason(
        self,
        worktree_path: Path,
        *,
        expected_branch: str | None = None,
        expected_commit: str | None = None,
    ) -> _SkipReason | None:
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

        When the caller supplies the branch and commit the sweep recorded, the
        worktree must still match them. Completion evidence describes the
        candidate as it was selected, so work committed or checked out during
        the sweep invalidates that evidence and the candidate is skipped.

        Parameters
        ----------
        worktree_path : Path
            Worktree to inspect. It is not modified.
        expected_branch : str | None, optional
            Branch the sweep recorded for this worktree.
        expected_commit : str | None, optional
            Commit the sweep recorded for this worktree.

        Returns
        -------
        _SkipReason | None
            The reason to leave the worktree alone, or ``None`` when Git would
            remove it unprompted.

        """
        with observability.get_recorder().span("worktree_preflight"):
            try:
                # A stat that the process is not allowed to make raises
                # PermissionError; the sweep must report it as unreadable
                # rather than abandon the remaining candidates.
                if not worktree_path.is_dir():
                    return _SkipReason.UNAVAILABLE
                completed = self.local_git(_WORKTREE_STATUS_ARGUMENTS, worktree_path)
            except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
                return _SkipReason.UNREADABLE
            state = _parse_worktree_status(completed)
            if isinstance(state, _SkipReason):
                return state
            if state.is_dirty:
                return _SkipReason.DIRTY
            if not _matches_worktree_state(
                state,
                expected_branch=expected_branch,
                expected_commit=expected_commit,
            ):
                return _SkipReason.CHANGED
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

    def _branch_in_use(self, branch_name: str) -> bool:
        """Return whether another worktree may still be using the branch.

        ``git branch -D`` refuses to delete a branch that a worktree has
        checked out, and also protects a branch being rebased or bisected
        there. A conditional ``update-ref -d`` performs none of those checks,
        so they are repeated here. A detached worktree does not name the
        branch it may be rebasing or bisecting, so it counts as "in use". A
        listing failure is treated the same way: the branch is kept when its
        usage cannot be proven absent.

        Returns
        -------
        bool
            ``True`` when a worktree still has the branch checked out, has a
            detached HEAD, or when that cannot be determined.

        """
        try:
            listing = self.repo.git.worktree("list", "--porcelain")
        except GitCommandError:
            return True
        expected = f"branch refs/heads/{branch_name}"
        for stanza in listing.split("\n\n"):
            lines = stanza.splitlines()
            if expected in lines:
                return True
            has_detached_head = any(line.startswith("HEAD ") for line in lines) and (
                not any(line.startswith("branch ") for line in lines)
            )
            if has_detached_head:
                return True
        return False

    def _remove_branch_config(self, branch_name: str) -> None:
        """Remove a deleted branch's configuration, ignoring a missing section.

        The ref is already gone, so a failure here is reported but does not
        turn the deletion into a failure. A branch that never had configuration
        is the normal case and produces no output.
        """
        prefix = f"branch.{re.escape(branch_name)}."
        try:
            self.repo.git.config("--get-regexp", f"^{prefix}")
        except GitCommandError:
            return
        try:
            self.repo.git.config("--remove-section", f"branch.{branch_name}")
        except GitCommandError as exc:
            _LOGGER.warning(
                "Deleted branch but could not remove its configuration",
                extra={
                    "operation": "delete_branch",
                    "branch": branch_name,
                },
            )
            helpers._eprint(
                f"{GIT_PLONK_PREFIX}: deleted branch '{branch_name}' "
                f"but could not remove its tracking configuration: {exc}"
            )

    def delete_branch(
        self,
        branch_name: str,
        *,
        expected_commit: str | None = None,
    ) -> bool:
        """Delete ``branch_name``, reporting whether Git deleted it.

        Like the worktree removal, a refusal is reported rather than fatal: a
        completed worktree whose branch survives is a partial result the summary
        must name, not a reason to abandon the candidates queued behind it. The
        Git invocation is timed as the ``branch_deletion`` span, refusal
        included, because the subprocess runs either way.

        When ``expected_commit`` is supplied, the deletion is conditional: Git
        deletes the ref only while it still points at that commit, and only
        when no other worktree still has the branch checked out. A commit made
        to the branch after the sweep recorded it therefore leaves the branch
        in place instead of losing its only reference, and a branch another
        worktree still uses survives. Because the ref deletion bypasses
        ``git branch -D``, the branch's stale tracking configuration is removed
        explicitly after a successful delete.

        Parameters
        ----------
        branch_name : str
            Local branch to delete.
        expected_commit : str | None, optional
            Commit the sweep recorded for the branch. When supplied, deletion
            fails if the ref has moved.

        Returns
        -------
        bool
            ``True`` when Git deleted the branch, otherwise ``False``.

        """
        try:
            with observability.get_recorder().span("branch_deletion"):
                if expected_commit is None:
                    self.repo.git.branch("-D", branch_name)
                else:
                    if self._branch_in_use(branch_name):
                        helpers._eprint(
                            f"{GIT_PLONK_PREFIX}: branch '{branch_name}' is still "
                            "in use by another worktree; leaving it in place"
                        )
                        return False
                    self.repo.git.update_ref(
                        "-d",
                        f"refs/heads/{branch_name}",
                        expected_commit,
                    )
                    self._remove_branch_config(branch_name)
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
