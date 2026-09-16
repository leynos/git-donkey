"""The ladder's harness: the reads a walk is handed, and the call that puts it.

A case states one rung of the ladder by supplying all three of its readers as
data, and this module puts them together: ``Run`` holds the graph, the record
reader and the opener, and exposes them as the ports the ladder asks for, while
``ask`` puts the run's question with them. The doubles those reads are made of
are in :mod:`tests.unit.wheresat_parents_history`,
:mod:`tests.unit.wheresat_parents_record` and
:mod:`tests.unit.wheresat_parents_forge`, and the values they answer with are in
:mod:`tests.unit.wheresat_parents_corpus`.

Nothing here opens a repository or a socket. Nothing here is collected by
pytest either, so a check of its own raises rather than asserting: the suite
states what the ladder does, and these modules state only what a test asked for.
"""

from __future__ import annotations

import dataclasses
import typing as typ

from git_donkey import wheresat_parents as parents
from git_donkey.wheresat_records import BoundaryRequest
from tests.unit.wheresat_helpers import CHILD_TIP, DEFAULT_WINDOW, TARGET
from tests.unit.wheresat_parents_corpus import ASSOCIATION_REPOSITORY, CHILD_BRANCH
from tests.unit.wheresat_parents_history import History
from tests.unit.wheresat_parents_record import Records

if typ.TYPE_CHECKING:
    from git_donkey import stack_records, stack_store, wheresat_graph
    from tests.unit.wheresat_parents_forge import Opener


def boundary_request(
    *,
    parent: stack_records.PullRequestIdentity | None = None,
    offline: bool = False,
) -> BoundaryRequest:
    """Return the run's question, naming a parent and an offline flag.

    Parameters
    ----------
    parent : stack_records.PullRequestIdentity | None, optional
        The parent pull request the run was told to consult, or ``None`` for a
        run that names none and reads only what its own branch says.
    offline : bool, optional
        Whether the run may ask the network at all. A case sets it to keep a
        walk from reaching a forge whose opener it supplied for another reason.

    Returns
    -------
    BoundaryRequest
        The question, put to the branch and child tip every case here shares.

    """
    return BoundaryRequest(
        branch=CHILD_BRANCH,
        child_tip=CHILD_TIP,
        target=TARGET,
        parent=parent,
        deep=False,
        heuristic_window=DEFAULT_WINDOW,
        offline=offline,
    )


def search_bounds(
    *, limit: int = 20, repository: str | None = ASSOCIATION_REPOSITORY
) -> parents.SearchBounds:
    """Return how far the search may walk, and in which repository.

    Parameters
    ----------
    limit : int, optional
        Commits the search may examine, as ``--limit`` bounds it.
    repository : str | None, optional
        ``OWNER/REPOSITORY`` slug of the repository to ask about those commits,
        or ``None`` for the checkout that names no GitHub repository.

    Returns
    -------
    parents.SearchBounds
        The bounds the association search is put to the ladder with.

    """
    return parents.SearchBounds(limit=limit, repository=repository)


@dataclasses.dataclass(frozen=True, slots=True)
class Run:
    """Everything one walk of the ladder reads through, as a test supplies it.

    Attributes
    ----------
    history : History
        The graph the search's window is read from.
    records : Records
        The child's own record, which may name a parent.
    opener : Opener | None
        What opens the forge, or ``None`` for a caller that offers none.

    """

    history: History = dataclasses.field(default_factory=History)
    records: Records = dataclasses.field(default_factory=Records)
    opener: Opener | None = None

    @property
    def reads(self) -> parents.LadderReads:
        """The three reads this walk is handed: history, record, and forge.

        Returns
        -------
        parents.LadderReads
            The reads, cast to the ports the ladder asks: the graph its window
            is listed from, the reader its record comes from, and the opener
            its forge is reached through when the case supplies one.

        """
        return parents.LadderReads(
            graph=typ.cast("wheresat_graph.WheresatGraph", self.history),
            records=typ.cast("stack_store.StackRecordReader", self.records),
            opener=self.opener,
        )


def ask(
    request: BoundaryRequest,
    bounds: parents.SearchBounds,
    *,
    run: Run | None = None,
) -> parents.ParentIdentification:
    """Put the run's parent question to the ladder.

    Parameters
    ----------
    request : BoundaryRequest
        What the run was asked, including the window and the offline flag.
    bounds : parents.SearchBounds
        How far the search may walk, and in which repository.
    run : Run | None, optional
        The reads the walk is handed, or ``None`` for a walk handed an empty
        history, an absent record, and no forge to open.

    Returns
    -------
    parents.ParentIdentification
        The identification the ladder reached.

    """
    return parents.identify_parent(request, bounds, reads=(run or Run()).reads)
