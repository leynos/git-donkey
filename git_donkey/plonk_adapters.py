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
import re
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]  # bounded ls-remote
import typing as typ

from git import GitCommandError, InvalidGitRepositoryError, NoSuchPathError, Repo

from git_donkey import helpers, observability
from git_donkey.plonk_records import (
    GIT_PLONK_PREFIX,
    UpstreamState,
    _HistoryMessage,
    _SkipReason,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

_LOGGER = logging.getLogger(__name__)
# `git ls-remote --exit-code` reports "no matching refs" with this status, and
# `git config --get-all` reports an unset key with this one.
_LS_REMOTE_NO_MATCH_STATUS = 2
# A query that answered the advertisement, whether or not a ref matched.
_LS_REMOTE_QUERY_STATUSES = frozenset({0, _LS_REMOTE_NO_MATCH_STATUS})
_CONFIG_KEY_UNSET_STATUS = 1
_LS_REMOTE_TIMEOUT_SECONDS = 10.0
_GIT_TERMINAL_PROMPT_ENV = "GIT_TERMINAL_PROMPT"
# Characters Git forbids in ref names, plus the ones `ls-remote` treats as a
# pattern. A merge ref containing any of them cannot be queried literally.
_INVALID_REF_CHARS = frozenset(" ~^:?*[\\")
# Multi-character sequences Git also forbids in a ref name.
_FORBIDDEN_REF_SEQUENCES = ("..", "//", "@{")
_FIRST_PRINTABLE_CODE_POINT = 0x20
_DELETE_CODE_POINT = 0x7F
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


def _matches_recorded_identity(
    worktree_repo: Repo,
    *,
    expected_branch: str | None,
    expected_commit: str | None,
) -> bool:
    """Return whether ``worktree_repo`` still matches the recorded identity.

    Returns
    -------
    bool
        ``True`` when the checked-out branch and commit, where supplied, still
        equal the recorded values.

    """
    head = worktree_repo.head
    branch_matches = expected_branch is None or (
        not head.is_detached and head.ref.name == expected_branch
    )
    if expected_commit is None:
        return branch_matches
    try:
        commit_matches = head.commit.hexsha == expected_commit
    except ValueError:
        # An unborn HEAD has no commit, so it cannot match a recorded one.
        return False
    return branch_matches and commit_matches


class _GitWorktreeAdapter:
    """GitPython-backed adapter for history, inspection, and mutation."""

    def __init__(self, repo: Repo) -> None:
        """Keep the repository every query and mutation runs against."""
        self.repo = repo

    def _ls_remote(
        self,
        arguments: typ.Sequence[str],
    ) -> subprocess.CompletedProcess[str] | None:
        """Run a bounded ``ls-remote``, timing it and recording the outcome.

        Statuses 0 and 2 are successful advertisement queries: one found the
        ref, the other reports that no ref matched. Any other non-zero status
        is a failed query, so it is recorded as a failure rather than being
        reported as a successful answer.

        Returns
        -------
        subprocess.CompletedProcess[str] | None
            The completed query, or ``None`` when it could not run.

        """
        with observability.get_recorder().span("upstream_lookup"):
            completed = self._run_ls_remote_process(arguments)
        error_kind: observability.ErrorKind | None = None
        if completed is None:
            outcome: observability.Outcome = "failure"
            error_kind = "os_error"
        elif completed.returncode in _LS_REMOTE_QUERY_STATUSES:
            outcome = "success"
        else:
            outcome = "failure"
            error_kind = "git_command_error"
        observability.get_recorder().record(
            observability.Observation(
                operation="upstream_lookup",
                outcome=outcome,
                error_kind=error_kind,
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
    def _has_control_character(ref: str) -> bool:
        """Return whether ``ref`` contains an ASCII control character."""
        return any(
            ord(character) < _FIRST_PRINTABLE_CODE_POINT
            or ord(character) == _DELETE_CODE_POINT
            for character in ref
        )

    @staticmethod
    def _has_git_ref_structure_error(ref: str) -> bool:
        """Return whether ``ref`` uses structure Git forbids in ref names."""
        if not ref.startswith("refs/heads/") or ref.endswith(("/", ".")):
            return True
        if any(pattern in ref for pattern in _FORBIDDEN_REF_SEQUENCES):
            return True
        # Git rejects a component that starts with a dot or ends in ``.lock``.
        return any(
            component.startswith(".") or component.endswith(".lock")
            for component in ref.split("/")
        )

    @classmethod
    def _is_exact_branch_ref(cls, ref: str) -> bool:
        """Return whether ``ref`` is a full branch ref that names one ref exactly.

        ``git ls-remote`` matches its argument as a pattern, so a wildcard would
        answer for a different ref than the one the branch configured. This
        check accepts only complete ``refs/heads/`` names that contain no
        pattern metacharacter, control character, or structure Git forbids.

        Returns
        -------
        bool
            ``True`` only for a literal, complete branch ref.

        """
        if cls._has_git_ref_structure_error(ref) or cls._has_control_character(ref):
            return False
        return not any(character in _INVALID_REF_CHARS for character in ref)

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
        if not self._is_exact_branch_ref(merge_ref):
            # `ls-remote` treats its argument as a pattern. A wildcard or an
            # invalid ref could match a ref it does not name and make a deleted
            # branch look present, or vice versa, so fail closed.
            return "unknown"
        return self._ls_remote_state(remote, merge_ref)

    def _worktree_config_enabled(self) -> bool:
        """Return whether Git's per-worktree configuration extension is on."""
        try:
            value = self.repo.git.config("--get", "extensions.worktreeConfig")
        except GitCommandError:
            return False
        return value.strip().casefold() in {"true", "yes", "on", "1"}

    @staticmethod
    def skip_reason(
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
        with (
            observability.get_recorder().span("worktree_preflight"),
            _clean_git_environment(),
        ):
            try:
                # A stat that the process is not allowed to make raises
                # PermissionError; the sweep must report it as unreadable
                # rather than abandon the remaining candidates.
                if not worktree_path.is_dir():
                    return _SkipReason.UNAVAILABLE
                with Repo(worktree_path) as worktree_repo:
                    if worktree_repo.is_dirty(untracked_files=True):
                        return _SkipReason.DIRTY
                    if not _matches_recorded_identity(
                        worktree_repo,
                        expected_branch=expected_branch,
                        expected_commit=expected_commit,
                    ):
                        return _SkipReason.CHANGED
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
