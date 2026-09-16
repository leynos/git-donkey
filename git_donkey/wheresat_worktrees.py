"""Read what the worktree holding a branch is in the middle of.

The read-only Git port answers two questions: what a branch's history says
about where it came from, and whether the worktree holding it is in a state a
replay could be run in. Only the second is here. It shares nothing with the
history reader but the failure vocabulary in
:mod:`git_donkey.wheresat_errors`, and it is kept apart from it because its
subject is the working tree rather than the commit graph: a branch read with
``--branch`` need not be checked out anywhere, and a worktree can be stopped in
an operation that has detached from it.

Nothing in this module can write. The worktree Git lists is read through its
own ``.git`` entry and its own ``status``, so the answer is about the worktree
holding the branch rather than about the repository the run started in.

"""

from __future__ import annotations

import typing as typ
from pathlib import Path

from git import InvalidGitRepositoryError, NoSuchPathError, Repo

from git_donkey._constants import GIT_ANSWERED_YES
from git_donkey.wheresat_errors import WheresatGraphError, failure_line
from git_donkey.wheresat_records import GitOperation, WorktreeState

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_WORKTREE_LISTING: typ.Final = "--porcelain"
"""Format asking ``git worktree list`` for one block of fields per worktree."""

_GHOST_WORKTREE: typ.Final = "prunable"
"""Field Git prints for a worktree whose directory is no longer there.

Such a worktree holds no checkout, so it is skipped rather than opened: the
branch it once held is not one a replay command could be run in, and the
``Repo`` that path names no longer exists.
"""

_TRACKED_ONLY: typ.Final = "--untracked-files=no"
"""What keeps the dirtiness answer about what a replay refuses to run over.

An untracked file stops a rebase only when the rebase would overwrite it, so
counting every untracked file would warn about build output the replay would
run over happily. A change to a tracked file stops it either way.
"""

_NO_OPTIONAL_LOCKS: typ.Final = "--no-optional-locks"
"""Git's opt-out from the optional work a read would take a lock to do.

Reading a worktree's status is a question, and Git answers it by refreshing the
index and writing it back when it may — so a read the run takes to decide
whether it may write would be a write of its own, and one that contends with
whoever else is using that worktree.

The opt-out is a *global* option rather than a ``status`` one: ``git status
--no-optional-locks`` is not a command Git accepts and ``git
--no-optional-locks status`` is. GitPython's attribute-call form has nowhere to
put an option that precedes the subcommand, so the read is made through
:meth:`git.cmd.Git.execute`, which takes the argument vector whole.
"""

_OPERATIONS: typ.Final[tuple[tuple[GitOperation, tuple[str, ...]], ...]] = (
    (GitOperation.REBASE, ("rebase-merge", "rebase-apply")),
    (GitOperation.MERGE, ("MERGE_HEAD",)),
    (GitOperation.CHERRY_PICK, ("CHERRY_PICK_HEAD",)),
    (GitOperation.REVERT, ("REVERT_HEAD",)),
    (GitOperation.BISECT, ("BISECT_LOG",)),
)
"""Marker files, in the order read, that name an operation stopped mid-flight.

The marker directory is the worktree's own Git directory rather than the
common one, because each worktree can be stopped somewhere different: a rebase
started in the child's worktree leaves ``rebase-merge`` beside that worktree's
``HEAD`` and not beside the main checkout's. A rebase is checked first because
it leaves two markers of its own and, on a conflict, a ``MERGE_MSG`` a reader
could mistake for a merge.
"""

_LEFT_BEHIND: typ.Final[tuple[str, ...]] = (
    "rebase-merge/head-name",
    "rebase-apply/head-name",
    "BISECT_START",
)
"""Files naming the branch a worktree detached from to start an operation.

A worktree stopped in a rebase or a bisect has its ``HEAD`` detached, so the
porcelain listing names no branch for it — although the branch is exactly what
the stopped operation is going to put back. The branch it left is recorded in
the operation's own state instead: a rebase writes the ref it replays into
``head-name`` and a bisect writes the branch it started from into
``BISECT_START``. Reading them is what lets the command warn about a replay it
prints under a stopped rebase, because the worktree holding the child branch
is precisely the one that no longer names it.
"""

_BRANCH_SPELLINGS: typ.Final[tuple[str, ...]] = ("refs/heads/{branch}", "{branch}")
"""How a detached worktree's recorded branch may be written."""

_GIT_ENTRY: typ.Final = ".git"
"""The entry a working tree holds its Git directory or its path in."""

_GITDIR_PREFIX: typ.Final = "gitdir:"
"""What a linked worktree's ``.git`` file holds in place of a directory."""


class _GitExecute(typ.Protocol):
    """The argument-vector Git surface a read needing a global option calls.

    :meth:`git.cmd.Git.execute` takes the whole argument vector, which is the
    only way through GitPython to place an option Git accepts *before* the
    subcommand. Its declared overloads leave no shape for a read that wants the
    status, both streams, and exceptions turned off, so that shape is named
    here and the call is made through it, as the package's other Git surfaces
    are.

    """

    GIT_PYTHON_GIT_EXECUTABLE: str
    """Name of the Git executable, which such a vector opens with."""

    def execute(
        self, command: cabc.Sequence[str], **kwargs: object
    ) -> tuple[int, str, str]:
        """Run ``command`` whole, returning its status and both streams."""


def worktree_state(repo: Repo, branch: str) -> WorktreeState:
    """Return what the worktree holding ``branch`` is in the middle of.

    The answer is what the command warns about and never what it decides
    from: the replay command the report prints is safe to *read* in either
    state and refuses to *run* in both, so a dirty worktree or a rebase in
    progress changes what the report says and never the verdict or the exit
    status.

    Parameters
    ----------
    repo : Repo
        Repository to read. It is used to list the worktrees and nothing else;
        once the worktree holding the branch is named, that worktree is opened
        in its own right.
    branch : str
        Branch to look for. A branch no worktree has checked out reports an
        idle state rather than a fault, because there is then no working
        tree for a replay to be run in.

    Returns
    -------
    WorktreeState
        The operation the worktree is stopped in, if any, and whether it
        holds changes to tracked files.

    Raises
    ------
    WheresatGraphError
        If Git cannot list the worktrees, open the one holding the branch,
        or read its state.

    """
    path = _listing(repo, branch)
    if path is None:
        return WorktreeState(operation=None, dirty=False)
    # The worktree is opened in its own right and read inside the block, so the
    # handles that opening it takes are released once both reads have answered.
    # Nothing outside reads the repository, which is why it is not returned.
    with _opened(path) as worktree:
        return WorktreeState(
            operation=_operation(worktree),
            dirty=_dirty(worktree),
        )


def _listing(repo: Repo, branch: str) -> str | None:
    """Return the path of the worktree that has ``branch`` checked out.

    Parameters
    ----------
    repo : Repo
        Repository to list the worktrees of.
    branch : str
        Branch the wanted worktree holds.

    Returns
    -------
    str | None
        The path Git listed for the worktree holding the branch, or
        ``None`` when no live worktree holds it.

    Raises
    ------
    WheresatGraphError
        If Git cannot list the worktrees.

    """
    status, output, stderr = repo.git.worktree(
        "list",
        _WORKTREE_LISTING,
        with_extended_output=True,
        with_exceptions=False,
    )
    if status != GIT_ANSWERED_YES:
        reported = failure_line(stderr, status)
        msg = f"cannot list the worktrees to find {branch!r}: {reported}"
        raise WheresatGraphError(msg)
    return _holding(str(output), branch)


def _fields(block: str) -> dict[str, str]:
    """Return one ``git worktree list --porcelain`` block as its named fields.

    Returns
    -------
    dict[str, str]
        The block's fields by name. A field Git prints with no value, such as
        ``bare``, maps to the empty string.

    """
    fields: dict[str, str] = {}
    for line in block.splitlines():
        name, _, value = line.partition(" ")
        fields[name] = value
    return fields


def _holding(listing: str, branch: str) -> str | None:
    """Return the path of the worktree that holds ``branch``.

    A worktree holds the branch when Git says it has it checked out, or when it
    is stopped in an operation that detached from it: a rebase and a bisect
    both leave ``HEAD`` detached, so the worktree a replay could not run in is
    the one whose porcelain block names no branch at all.

    Returns
    -------
    str | None
        The worktree's path, or ``None`` when no worktree holds the branch: a
        branch read with ``--branch`` need not be checked out anywhere.

    """
    wanted = f"refs/heads/{branch}"
    for block in listing.split("\n\n"):
        fields = _fields(block)
        path = fields.get("worktree")
        if path is None or _GHOST_WORKTREE in fields:
            continue
        if fields.get("branch") == wanted or _left_behind(path, branch):
            return path
    return None


def _left_behind(path: str, branch: str) -> bool:
    """Return whether the worktree at ``path`` is stopped having left ``branch``.

    Returns
    -------
    bool
        Whether an operation in progress there records ``branch`` as the one it
        detached from. A worktree whose own state cannot be read is reported as
        not holding the branch rather than as a fault: the state is missing
        when the worktree is gone, and a worktree that is gone holds nothing.

    """
    directory = _state_directory(Path(path))
    return any(
        _records_branch(directory.joinpath(name), branch) for name in _LEFT_BEHIND
    )


def _state_directory(path: Path) -> Path:
    """Return the directory the worktree at ``path`` keeps its own state in.

    The path is read from the worktree's own ``.git`` entry rather than asked
    of Git, because this runs for every worktree Git listed and only the one
    holding the branch is needed: a linked worktree's entry is a file naming
    its Git directory, and the main checkout's is that directory.

    Returns
    -------
    pathlib.Path
        The worktree's Git directory, which does not exist when the worktree
        is gone.

    """
    entry = path / _GIT_ENTRY
    if entry.is_dir():
        return entry
    try:
        recorded = entry.read_text(encoding="utf-8")
    except OSError:
        return entry
    _, separator, target = recorded.strip().partition(_GITDIR_PREFIX)
    if not separator:
        return entry
    # ``git worktree add --relative-paths`` writes a Git directory relative to
    # the worktree, so a relative target is read from where the ``.git`` entry
    # naming it lives, not from whatever directory the run was started in.
    resolved = Path(target.strip())
    return resolved if resolved.is_absolute() else path / resolved


def _records_branch(state: Path, branch: str) -> bool:
    """Return whether the operation-state file ``state`` records ``branch``.

    Returns
    -------
    bool
        Whether the file names the branch, written either as the ref the
        operation detached from or as the bare branch name.

    """
    try:
        recorded = state.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return recorded in {
        spelling.format(branch=branch) for spelling in _BRANCH_SPELLINGS
    }


def _opened(path: str) -> Repo:
    """Return the repository rooted at ``path``, a path Git listed a worktree at.

    Returns
    -------
    Repo
        The worktree, opened in its own right, so that reads of its status and
        its Git directory answer about that worktree rather than about the
        repository the run was started in.

    Raises
    ------
    WheresatGraphError
        If the path no longer holds a worktree, which a concurrent ``git
        worktree prune`` can arrange between the listing and this read.

    """
    try:
        return Repo(path)
    except (InvalidGitRepositoryError, NoSuchPathError) as exc:
        msg = f"cannot read the worktree at {path}: {exc}"
        raise WheresatGraphError(msg) from exc


def _absolute_git_dir(worktree: Repo) -> Path:
    """Return the directory Git keeps ``worktree``'s own state in.

    Returns
    -------
    pathlib.Path
        The worktree's Git directory. It is the per-worktree one for a linked
        worktree, which is where an operation's marker files are written.

    Raises
    ------
    WheresatGraphError
        If Git cannot name the directory.

    """
    status, output, stderr = worktree.git.rev_parse(
        "--absolute-git-dir",
        with_extended_output=True,
        with_exceptions=False,
    )
    if status != GIT_ANSWERED_YES:
        reported = failure_line(stderr, status)
        msg = f"cannot locate the worktree's Git directory: {reported}"
        raise WheresatGraphError(msg)
    return Path(str(output).strip())


def _operation(worktree: Repo) -> GitOperation | None:
    """Return the operation ``worktree`` is stopped in the middle of, if any.

    Returns
    -------
    GitOperation | None
        The operation whose marker the worktree's Git directory holds, or
        ``None`` when it holds none and the worktree is idle.

    """
    directory = _absolute_git_dir(worktree)
    for operation, markers in _OPERATIONS:
        if any(directory.joinpath(marker).exists() for marker in markers):
            return operation
    return None


def _dirty(worktree: Repo) -> bool:
    """Return whether ``worktree`` holds changes a replay would refuse to run over.

    Reading the status is a question, and it is asked with Git's optional locks
    turned off (:data:`_NO_OPTIONAL_LOCKS`), so the answer is not paid for by
    writing the index of a worktree somebody else may be using.

    Returns
    -------
    bool
        Whether a tracked file is added, modified, deleted, or in conflict.

    Raises
    ------
    WheresatGraphError
        If Git cannot read the worktree's status.

    """
    git = typ.cast("_GitExecute", worktree.git)
    status, output, stderr = git.execute(
        [
            git.GIT_PYTHON_GIT_EXECUTABLE,
            _NO_OPTIONAL_LOCKS,
            "status",
            "--porcelain",
            _TRACKED_ONLY,
        ],
        with_extended_output=True,
        with_exceptions=False,
    )
    if status != GIT_ANSWERED_YES:
        reported = failure_line(stderr, status)
        msg = f"cannot read the worktree's status: {reported}"
        raise WheresatGraphError(msg)
    return bool(str(output).strip())
