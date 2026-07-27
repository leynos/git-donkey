"""Shared helpers for git-donkey command workflows.

Provides utility functions for error handling, Git discovery, worktree parsing,
and slug validation that are reused across the CLI modules.

Usage:
    from git_donkey import helpers
    helpers.validate_slug("feature/foo", label="branch", prefix="git-donkey")
"""

from __future__ import annotations

import contextlib
import io
import re
import shutil
import sys
import typing as typ
from pathlib import Path

from git import GitCommandError, InvalidGitRepositoryError, NoSuchPathError, Repo

_GIT_DONKEY_PREFIX = "git-donkey"
_GIT_DONKEY_EMOJI = "💀"
_GIT_CONFLICT_EMOJI = "⚔️"
_SLUG_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]+$")


def _eprint(*args: object) -> None:
    """Print to stderr."""
    print(*args, file=sys.stderr)


def _stream_or_none(stream: typ.TextIO) -> typ.TextIO | None:
    """Return the stream if it is usable, otherwise None."""
    with contextlib.suppress(AttributeError, io.UnsupportedOperation, ValueError):
        stream.fileno()
        return stream
    return None


def _die(
    prefix: str,
    msg: str,
    code: int = 2,
    *,
    emoji: str | None = None,
) -> typ.NoReturn:
    """Exit with a formatted error message."""
    if emoji is None and prefix == _GIT_DONKEY_PREFIX:
        emoji = _GIT_DONKEY_EMOJI
    if emoji:
        _eprint(f"{prefix}: {emoji} {msg}")
    else:
        _eprint(f"{prefix}: {msg}")
    raise SystemExit(code)


def _die_conflict(prefix: str, msg: str, code: int = 2) -> typ.NoReturn:
    """Exit with a conflict error message."""
    _die(prefix, msg, code, emoji=_GIT_CONFLICT_EMOJI)


def _find_repo(prefix: str) -> Repo:
    """Return the repo rooted at the current working directory."""
    try:
        return Repo(Path.cwd(), search_parent_directories=True)
    except (InvalidGitRepositoryError, NoSuchPathError):
        _die(prefix, "not inside a Git repository", 2)


def _get_checked_out_branch_name(repo: Repo, prefix: str) -> str:
    """Return the checked out branch name or exit if detached."""
    if repo.head.is_detached:
        _die(
            prefix,
            "HEAD is detached in the current directory; checkout a branch first",
            2,
        )
    return repo.active_branch.name


def _parse_worktree_porcelain(repo: Repo) -> list[dict[str, object]]:
    """Parse `git worktree list --porcelain` output into stanzas."""
    try:
        out = repo.git.worktree("list", "--porcelain", "-z")
        parts = out.split("\0")
    except GitCommandError:
        out = repo.git.worktree("list", "--porcelain")
        parts = out.splitlines()

    stanzas: list[dict[str, object]] = []
    current: dict[str, object] = {}
    for part in parts:
        if not part:
            if current:
                stanzas.append(current)
                current = {}
            continue
        if " " in part:
            key, value = part.split(" ", 1)
            current[key] = value
        else:
            current[part] = True
    if current:
        stanzas.append(current)
    return stanzas


def _main_worktree_path_from_list(
    stanzas: list[dict[str, object]],
    prefix: str,
) -> Path:
    """Return the main worktree path from parsed worktree stanzas."""
    if not stanzas or "worktree" not in stanzas[0]:
        _die(prefix, "could not parse `git worktree list --porcelain` output", 2)

    if stanzas[0].get("bare", False):
        _die(
            prefix,
            "repository appears to be bare; this command expects a non-bare repo "
            "with a main worktree",
            2,
        )

    return Path(str(stanzas[0]["worktree"])).expanduser().resolve()


def _branch_to_worktree_map(stanzas: list[dict[str, object]]) -> dict[str, Path]:
    """Return a mapping of branch names to worktree paths."""
    out: dict[str, Path] = {}
    for stanza in stanzas:
        worktree = stanza.get("worktree")
        branch = stanza.get("branch")
        if not (worktree and branch):
            continue
        branch_name = str(branch)
        prefix = "refs/heads/"
        branch_name = branch_name.removeprefix(prefix)
        out[branch_name] = Path(str(worktree)).expanduser().resolve()
    return out


def _ref_exists(repo: Repo, ref: str) -> bool:
    """Return True if a Git ref exists."""
    try:
        repo.git.show_ref("--verify", "--quiet", ref)
    except GitCommandError:
        return False
    return True


def _local_branch_exists(repo: Repo, branch: str) -> bool:
    """Return True if the local branch exists."""
    return _ref_exists(repo, f"refs/heads/{branch}")


def _remote_branch_exists(repo: Repo, remote: str, branch: str) -> bool:
    """Return True if the remote branch exists."""
    return _ref_exists(repo, f"refs/remotes/{remote}/{branch}")


def _ensure_local_tracking_branch(
    repo: Repo,
    remote: str,
    branch: str,
    prefix: str,
) -> None:
    """Ensure a local tracking branch exists for the remote branch."""
    if _local_branch_exists(repo, branch):
        return
    if not _remote_branch_exists(repo, remote, branch):
        _die(
            prefix,
            f"branch '{branch}' does not exist locally, and '{remote}/{branch}' "
            "does not exist on the remote",
            1,
        )
    repo.git.branch("--track", branch, f"{remote}/{branch}")


def _first_remote_name(repo: Repo, prefix: str) -> str:
    """Return the first configured remote name."""
    try:
        return repo.remotes[0].name
    except IndexError:
        _die(prefix, "no remotes configured", 2)


def _fetch_remote(repo: Repo, remote: str, prefix: str) -> None:
    """Fetch updates from the specified remote."""
    try:
        repo.remote(remote).fetch()
    except GitCommandError as exc:
        _die(prefix, f"fetch failed: {exc}", 1)


def _prompt_yes_no(question: str, *, default: bool = False) -> bool:
    """Prompt for a yes/no response and return the chosen value."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        _eprint("Non-interactive stdin/stdout; skipping prompt (treating as 'no').")
        return False

    suffix = " [Y/n] " if default else " [y/N] "
    while True:
        try:
            response = input(question + suffix).strip().lower()
        except (EOFError, KeyboardInterrupt):
            _eprint("Prompt cancelled; treating as 'no'.")
            return False
        if not response:
            return default
        if response in {"y", "yes"}:
            return True
        if response in {"n", "no"}:
            return False
        _eprint("Please answer y or n.")


def validate_slug(value: str, *, label: str, prefix: str) -> str:
    """Validate slug-like inputs for git-fafo arguments.

    Parameters
    ----------
    value : str
        The input value to validate.
    label : str
        A human-friendly label for error messages.
    prefix : str
        The CLI name used for error messages.

    Returns
    -------
    str
        The validated input value.

    Raises
    ------
    SystemExit
        If ``value`` contains characters outside the allowed slug set.

    """
    if _SLUG_PATTERN.fullmatch(value):
        return value

    _eprint(f"{prefix}: invalid {label}: {value}")
    _eprint("  (allowed characters: a-z A-Z 0-9 _ - .)")
    raise SystemExit(1)


def _require_command(name: str, prefix: str) -> None:
    """Exit if the named executable is not available."""
    if shutil.which(name) is None:
        _die(prefix, f"required command not found: {name}", 1)
