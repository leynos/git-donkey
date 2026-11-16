"""Resolve where a repository's Git commands run and its donkey worktrees live.

``git donkey`` creates worktrees and ``git plonk`` retires them, so both must
agree on two directories: the *anchor*, which Git commands run from, and the
*worktrees root*, which holds the checkouts ``git donkey`` creates. A
repository with a main worktree anchors on that worktree. A bare repository has
none, as in a collection that keeps ``bare.git`` beside a directory of linked
worktrees, so it anchors on the bare directory itself.

The root is ``donkey.worktreesRoot`` when that is configured, and otherwise a
``.worktrees`` sibling of the anchor. Scope in ``git plonk`` is decided by path
under this root, so the default for a bare repository deliberately avoids any
directory another tool fills with its own worktrees.

Policy and infrastructure are kept apart. :func:`select_layout` and
:func:`default_worktrees_root` are pure: they take plain values and perform no
Git, filesystem, or process work. :func:`resolve_layout` is the Git adapter that
supplies those values. Neither layer exits the process; an unresolvable layout
raises :class:`LayoutError`, and the command boundaries in
:mod:`git_donkey.donkey` and :mod:`git_donkey.plonk` turn that into a usage
exit.

Usage
-----
Resolve the layout from any directory inside the repository::

    from git_donkey import helpers, layout

    repo = helpers._find_repo("git-donkey")
    stanzas = helpers._parse_worktree_porcelain(repo)
    try:
        resolved = layout.resolve_layout(repo, stanzas)
    except layout.LayoutError as exc:
        helpers._die("git-donkey", str(exc), 2)
    print(resolved.worktrees_root)
"""

from __future__ import annotations

import dataclasses
import enum
import os
from pathlib import Path

from git import GitCommandError, Repo

WORKTREES_ROOT_CONFIG_KEY = "donkey.worktreesRoot"
_WORKTREES_SUFFIX = ".worktrees"
_BARE_DIRECTORY_SUFFIX = ".git"
# `git config --get` exits 1 when the key is unset, and with other statuses
# when the configuration cannot be read or the value cannot be converted.
_CONFIG_KEY_UNSET_STATUS = 1


class LayoutError(Exception):
    """An unresolvable repository layout, carrying a caller-ready message.

    The policy and adapter layers raise this instead of exiting so they stay
    usable from tests and other callers. Only the command boundaries translate
    it into a process exit.
    """


class LayoutKind(enum.StrEnum):
    """How a repository's checkouts are arranged."""

    MAIN_WORKTREE = "main_worktree"
    BARE = "bare"


@dataclasses.dataclass(frozen=True, slots=True)
class RepositoryLayout:
    """The directories a donkey workflow operates on.

    Attributes
    ----------
    anchor_dir : Path
        Directory Git commands run from: the main worktree, or the bare
        repository when no main worktree exists.
    kind : LayoutKind
        Whether the repository has a main worktree or is bare.
    worktrees_root : Path
        Directory that holds the worktrees ``git donkey`` creates and
        ``git plonk`` considers.

    """

    anchor_dir: Path
    kind: LayoutKind
    worktrees_root: Path


def default_worktrees_root(anchor_dir: Path, kind: LayoutKind) -> Path:
    """Return the worktrees root used when none is configured.

    The root is a sibling of the anchor named ``<anchor>.worktrees``. A bare
    repository's conventional ``.git`` suffix is dropped first, so ``repo.git``
    and a non-bare ``repo`` in the same directory would share
    ``repo.worktrees``.

    Parameters
    ----------
    anchor_dir : Path
        The main worktree, or the bare repository directory.
    kind : LayoutKind
        Whether ``anchor_dir`` is a main worktree or a bare repository.

    Returns
    -------
    Path
        The default worktrees root. It is not created.

    Examples
    --------
    >>> default_worktrees_root(Path("/src/repo"), LayoutKind.MAIN_WORKTREE)
    PosixPath('/src/repo.worktrees')
    >>> default_worktrees_root(Path("/lody/project/bare.git"), LayoutKind.BARE)
    PosixPath('/lody/project/bare.worktrees')

    """
    name = anchor_dir.name
    if kind is LayoutKind.BARE:
        # A directory named exactly ".git" has no stem to fall back on.
        name = name.removesuffix(_BARE_DIRECTORY_SUFFIX) or name
    return anchor_dir.parent / f"{name}{_WORKTREES_SUFFIX}"


def select_layout(
    anchor_dir: Path,
    kind: LayoutKind,
    configured_root: str | None,
) -> RepositoryLayout:
    """Return the layout the precedence and naming rules imply.

    This is the policy layer: it takes plain values, reads no Git state, and
    performs no filesystem resolution or exit. Callers that need canonical
    paths resolve the result, as :func:`resolve_layout` does.

    Parameters
    ----------
    anchor_dir : Path
        The main worktree, or the bare repository directory.
    kind : LayoutKind
        Whether ``anchor_dir`` is a main worktree or a bare repository.
    configured_root : str | None
        The ``donkey.worktreesRoot`` value, or ``None`` when it is unset. A
        relative value resolves against ``anchor_dir``; the Git adapter has
        already expanded a leading ``~``.

    Returns
    -------
    RepositoryLayout
        The layout the rules imply, with lexical path normalisation only.

    Raises
    ------
    LayoutError
        If ``configured_root`` is set but empty.

    Examples
    --------
    >>> select_layout(
    ...     Path("/src/repo"), LayoutKind.MAIN_WORKTREE, None
    ... ).worktrees_root
    PosixPath('/src/repo.worktrees')
    >>> select_layout(
    ...     Path("/src/repo"), LayoutKind.MAIN_WORKTREE, "../trees"
    ... ).worktrees_root
    PosixPath('/src/trees')

    """
    if configured_root is not None and not configured_root:
        msg = (
            f"{WORKTREES_ROOT_CONFIG_KEY} is set but empty; unset it or give it "
            "a directory"
        )
        raise LayoutError(msg)
    worktrees_root = (
        default_worktrees_root(anchor_dir, kind)
        if configured_root is None
        else anchor_dir / configured_root
    )
    return RepositoryLayout(
        anchor_dir=anchor_dir,
        kind=kind,
        worktrees_root=Path(os.path.normpath(worktrees_root)),
    )


def _anchor_from_stanzas(
    stanzas: list[dict[str, object]],
) -> tuple[Path, LayoutKind]:
    """Return the anchor directory and kind from a parsed worktree listing."""
    if not stanzas or "worktree" not in stanzas[0]:
        msg = "could not parse `git worktree list --porcelain` output"
        raise LayoutError(msg)
    anchor_dir = Path(str(stanzas[0]["worktree"])).expanduser().resolve()
    kind = (
        LayoutKind.BARE if stanzas[0].get("bare", False) else LayoutKind.MAIN_WORKTREE
    )
    return anchor_dir, kind


def _configured_worktrees_root(repo: Repo) -> str | None:
    """Return the configured root string, or ``None`` when the key is unset."""
    try:
        # `--type=path` makes Git expand a leading `~`, as it does for its own
        # path-valued settings.
        value = repo.git.config("--type=path", "--get", WORKTREES_ROOT_CONFIG_KEY)
    except GitCommandError as exc:
        if exc.status == _CONFIG_KEY_UNSET_STATUS:
            return None
        msg = f"could not read {WORKTREES_ROOT_CONFIG_KEY}: {exc}"
        raise LayoutError(msg) from exc
    return value


def resolve_layout(
    repo: Repo,
    stanzas: list[dict[str, object]],
) -> RepositoryLayout:
    """Resolve the anchor directory and worktrees root for ``repo``.

    This is the Git adapter for the rules in :func:`select_layout`. Git lists
    the main worktree first, or the bare repository when there is no main
    worktree, so the first stanza names the anchor whichever worktree the
    command was invoked from. A relative ``donkey.worktreesRoot`` is resolved
    against the anchor, so one shared setting means the same directory from
    every worktree.

    Parameters
    ----------
    repo : Repo
        Repository whose configuration is consulted for
        ``donkey.worktreesRoot``.
    stanzas : list[dict[str, object]]
        Parsed ``git worktree list --porcelain`` output for ``repo``.

    Returns
    -------
    RepositoryLayout
        The resolved layout, with absolute, symlink-free paths.

    Raises
    ------
    LayoutError
        If the listing names no anchor, ``donkey.worktreesRoot`` cannot be
        read, or it is set but empty.

    """
    anchor_dir, kind = _anchor_from_stanzas(stanzas)
    selected = select_layout(anchor_dir, kind, _configured_worktrees_root(repo))
    return dataclasses.replace(
        selected,
        worktrees_root=selected.worktrees_root.resolve(),
    )
