"""The reading the read-only suites compare, and the one difference it allows.

``test_wheresat_read_only.py`` and the behavioural journeys each compare two
readings of a whole repository and claim they agree. What a reading is lives
here: the fingerprint is deliberately wider than any one test's interest,
because INV-1 is a claim about a whole repository — every ref and the commit it
names, the index and the working tree, the stash, the local configuration, and
``FETCH_HEAD`` — so the suites compare a reading of all of it rather than the
handful of refs a test happened to think of. Its sensitivity is checked
separately, in ``test_wheresat_fingerprint.py``, by mutating a repository one
reading at a time and requiring the comparison to report it.

The one difference a read-only run is permitted, a ref under ``refs/wheresat/``,
is named here rather than at each comparison, so every suite that weighs a
reading weighs the same allowance.
"""

from __future__ import annotations

import dataclasses
import hashlib
import typing as typ
from pathlib import Path

from git import Repo

if typ.TYPE_CHECKING:
    import collections.abc as cabc

EVIDENCE_NAMESPACE: typ.Final = "refs/wheresat/"
"""The one namespace a run without ``--record`` may add refs to (INV-1)."""

_GIT_ENTRY: typ.Final = ".git"
"""Git's own directory, which a fingerprint skips."""

_FETCH_HEAD: typ.Final = "FETCH_HEAD"
"""The file a fetch writes, which INV-1 promises is unchanged."""


@dataclasses.dataclass(frozen=True, slots=True)
class Fingerprint:
    """Everything about a repository and its working trees that a run may not change.

    Attributes
    ----------
    refs : tuple[str, ...]
        Every ref and the commit it names, as ``git for-each-ref`` lists them.
    status : tuple[str, ...]
        ``git status --porcelain=v2 --branch`` for each working tree read, with
        the tree's own directory name prefixed so two trees cannot be confused
        for one.
    stashes : tuple[str, ...]
        ``git stash list``.
    config : tuple[str, ...]
        ``git config --local --list``.
    fetch_head : tuple[str, ...]
        A digest of each working tree's ``FETCH_HEAD``, or that it has none,
        with the tree's own directory name prefixed like ``status``, because
        each tree has a file of its own to leave alone.
    files : tuple[str, ...]
        Every file each working tree holds, with its digest, named by the tree
        it belongs to.

    """

    refs: tuple[str, ...]
    status: tuple[str, ...]
    stashes: tuple[str, ...]
    config: tuple[str, ...]
    fetch_head: tuple[str, ...]
    files: tuple[str, ...]

    def differences(
        self,
        other: Fingerprint,
        *,
        allowed: str = EVIDENCE_NAMESPACE,
    ) -> tuple[str, ...]:
        """Return how ``other`` differs from this fingerprint.

        Parameters
        ----------
        other : Fingerprint
            Reading taken later — after a run, or after whatever else the
            caller is measuring.
        allowed : str, optional
            Ref namespace a change to which is not a difference. A run without
            ``--record`` may write evidence refs of its own, and nothing else:
            this is the whole of INV-1's permitted difference.

        Returns
        -------
        tuple[str, ...]
            One description per reading that differs, naming what was removed
            and what was added — or that the reading holds the same entries in
            a different order, which is a difference neither list shows — so a
            failure says which part of the repository a run touched rather
            than only that something did.

        """
        differences = []
        for label, mine, theirs in (
            ("refs", _outside(self.refs, allowed), _outside(other.refs, allowed)),
            ("status", self.status, other.status),
            ("stashes", self.stashes, other.stashes),
            ("config", self.config, other.config),
            ("fetch-head", self.fetch_head, other.fetch_head),
            ("files", self.files, other.files),
        ):
            if mine == theirs:
                continue
            removed = sorted(set(mine) - set(theirs))
            added = sorted(set(theirs) - set(mine))
            if not removed and not added:
                differences.append(f"{label}: reordered")
                continue
            differences.append(f"{label}: removed {removed}, added {added}")
        return tuple(differences)


def fingerprint(*roots: Path, repo: Repo) -> Fingerprint:
    """Return everything about ``repo`` and ``roots`` a read-only run must leave alone.

    Parameters
    ----------
    roots : Path
        Working trees to measure. Each one's index, working tree, and files are
        read separately, because a linked worktree is a second working tree of
        the same repository and a run could disturb either.
    repo : git.Repo
        Repository the refs, stash, and local configuration are read from. It is
        named rather than derived from a root, because several working trees
        share one repository.

    Returns
    -------
    Fingerprint
        The readings, comparable with :meth:`Fingerprint.differences`.

    """
    statuses: list[str] = []
    files: list[str] = []
    fetch_heads: list[str] = []
    for root in roots:
        # Each working tree is opened for the reads it owes and closed again:
        # this is called twice per scenario by suites that compare a reading
        # before a run with the one after it, and an unclosed repository holds
        # its object database open for as long as the reading lives.
        with Repo(root) as working:
            statuses += [
                f"{root.name}: {line}"
                for line in _lines(
                    working.git.status("--porcelain=v2", "--branch"),
                )
            ]
            fetch_heads += [f"{root.name}: {_fetch_head(working)}"]
            files += [f"{root.name}/{name} {digest}" for name, digest in _files(root)]
    return Fingerprint(
        refs=_lines(repo.git.for_each_ref("--format=%(refname) %(objectname)")),
        status=tuple(statuses),
        stashes=_lines(repo.git.stash("list")),
        config=_lines(repo.git.config("--local", "--list")),
        fetch_head=tuple(fetch_heads),
        files=tuple(files),
    )


def _outside(refs: cabc.Iterable[str], namespace: str) -> tuple[str, ...]:
    """Return the refs that are not inside ``namespace``."""
    return tuple(ref for ref in refs if not ref.startswith(namespace))


def _lines(output: str) -> tuple[str, ...]:
    """Return Git's output as the non-empty lines it holds."""
    return tuple(line for line in str(output).splitlines() if line)


def _files(root: Path) -> cabc.Iterator[tuple[str, str]]:
    """Yield the path and digest of every file in the working tree at ``root``.

    Git's own directory is skipped. The index and its caches are rewritten by
    reads as well as by writes, so comparing them would report changes that hold
    nothing; everything else in the tree is digested, tracked or not, because a
    run that reads must not leave a file behind either.

    Yields
    ------
    tuple[str, str]
        The path relative to ``root``, and the digest of its contents.

    """
    for directory, subdirectories, names in root.walk():
        subdirectories[:] = sorted(
            name for name in subdirectories if name != _GIT_ENTRY
        )
        for name in sorted(names):
            if name == _GIT_ENTRY:
                continue
            path = directory / name
            yield path.relative_to(root).as_posix(), _digest(path)


def _digest(path: Path) -> str:
    """Return the digest of the file at ``path``."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fetch_head(repo: Repo) -> str:
    """Return a digest of ``repo``'s ``FETCH_HEAD``, or that it has none.

    A fetch writes this file even when its refspec names a destination, so it is
    part of what INV-1 promises is unchanged. It is read from the Git directory
    the repository resolves to, which for a linked worktree is that worktree's
    own directory rather than the main checkout's: a run that fetched in one
    working tree would otherwise leave the other's file untouched and pass.
    :func:`fingerprint` therefore reads it once per working tree it measures,
    beside that tree's status and files, rather than once for the repository.

    Returns
    -------
    str
        The digest, or ``absent`` when the repository has no ``FETCH_HEAD``.

    """
    path = Path(repo.git.rev_parse("--absolute-git-dir")) / _FETCH_HEAD
    return _digest(path) if path.is_file() else "absent"
