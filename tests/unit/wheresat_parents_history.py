"""The graph double the ladder's history questions are put to.

The ladder asks the graph for a window of the child's history, so the double
answers with the commits a test supplied and records every read: the revision it
was asked about, the limit it was given, and the window it answered with. Which
reads were made is half of what the suite asserts, so they are kept in the order
they were made rather than counted, and the window is taken from the tip as the
real reader takes it.

Nothing here opens a repository or a socket.
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True, slots=True)
class History:
    """A graph that answers history questions, and records what it was asked.

    Only ``history`` is implemented, because it is the only graph question the
    ladder puts: the double is cast to the port rather than completed, so a
    ladder that reached for another question would fail against it.

    Attributes
    ----------
    commits : tuple[str, ...]
        Commits the history holds, oldest first, as the real reader returns
        them.
    refusal : Exception | None
        Failure the read raises instead of answering.
    limits : list[int | None]
        The ``limit`` of every read, in the order the reads were made.
    revisions : list[str]
        The ``rev`` of every read, in the order the reads were made, so a test
        can hold the ladder to the commit it was asked about rather than to
        whichever one the history happens to hold.
    windows : list[tuple[str, ...]]
        The commits every read returned, in the order the reads were made.

    """

    commits: tuple[str, ...] = ()
    refusal: Exception | None = None
    limits: list[int | None] = dataclasses.field(default_factory=list)
    revisions: list[str] = dataclasses.field(default_factory=list)
    windows: list[tuple[str, ...]] = dataclasses.field(default_factory=list)

    def history(self, rev: str, *, limit: int | None = None) -> tuple[str, ...]:
        """Return the newest commits of the history this double holds.

        The window is taken from the tip, as the real reader takes it, so a
        read of a history longer than the bound answers with the commits a
        squash could have landed rather than with the oldest ones. Which
        commits that leaves is decided by ``_history_window``.

        Parameters
        ----------
        rev : str
            Revision the history is read from, which this double records
            without reading: the commits it holds stand for that revision.
        limit : int | None
            How many of the newest commits to keep, or ``None`` for all of
            them.

        Returns
        -------
        tuple[str, ...]
            The commits kept, oldest first.

        """
        self.revisions.append(rev)
        self.limits.append(limit)
        if self.refusal is not None:
            raise self.refusal
        window = _history_window(self.commits, limit)
        self.windows.append(window)
        return window


def _history_window(commits: tuple[str, ...], limit: int | None) -> tuple[str, ...]:
    """Return the window a read of ``commits`` bounded by ``limit`` answers with.

    Parameters
    ----------
    commits : tuple[str, ...]
        Commits the history holds, oldest first.
    limit : int | None
        How many of the newest commits to keep, or ``None`` for all of them. A
        limit of zero keeps no commits at all, which ``commits[-0:]`` would
        otherwise read as keeping every one of them.

    Returns
    -------
    tuple[str, ...]
        The commits kept, oldest first.

    """
    if limit is None:
        return commits
    if limit <= 0:
        return ()
    return commits[-limit:]


def graph_over(*commits: str) -> History:
    """Return a graph answering with ``commits``, oldest first.

    Parameters
    ----------
    *commits : str
        Commits the double holds, oldest first.

    Returns
    -------
    History
        A graph whose history answers with those commits.

    """
    return History(commits=commits)
