"""The deep comparison: the child's commits against the target's newest ones.

``--deep`` puts a question the ladder's own rungs cannot: has the work a child
commit carries already landed on the target, and as which commit? It is
answered by comparing content rather than by reading a record or a forge, and
that is what makes its answer inferred evidence — two commits carrying one tree
say the same content is in both of them, and never which of the two landed
first.

Two comparisons are made, cheapest first. The tree pass indexes the target's
window by whole tree object ID and looks each child commit up in it: a handful
of object reads per commit, and the answer to the clean squash. The
cumulative-patch pass runs only over the child commits the tree pass left
unmatched, comparing the change a child commit accumulates from its fork point
with the change each windowed commit introduces; it is the pass that answers a
squash which had to resolve a conflict, where the two trees differ and the two
changes do not.

The comparison is bounded because a trunk is unbounded. Only the newest
:attr:`~git_donkey.wheresat_records.BoundaryRequest.heuristic_window` commits
of the target are read, and the listing asks for one commit more than the
window holds, so the scan can tell a window that reached the end of the history
from one that did not: a commit older than the window is a commit whose twin
was never looked for, and the run says so. That is a caveat and never a fault.
What the scan would have found is inferred evidence, so a question it could not
answer is one no verdict rested on, and a fault would make ``--deep`` change
the verdict it is documented never to change.
"""

from __future__ import annotations

import dataclasses
import functools
import typing as typ

from git_donkey.wheresat_errors import WheresatGraphError
from git_donkey.wheresat_records import (
    COMMIT_ABBREVIATION,
    BoundaryRequest,
    Candidate,
    EvidenceKind,
    candidate_for,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from git_donkey.wheresat_graph import WheresatGraph


_SOURCE_WORDS: typ.Final[typ.Mapping[EvidenceKind, str]] = {
    EvidenceKind.TREE_IDENTITY: "tree identity",
    EvidenceKind.PATCH_IDENTITY: "patch identity",
}
"""What each comparison is called where a candidate reports its source.

The words are prose because they are read as prose, in a report line beside the
commit the comparison matched rather than only beside the kind it is classed as.
"""

KINDS: typ.Final[tuple[EvidenceKind, ...]] = (
    EvidenceKind.TREE_IDENTITY,
    EvidenceKind.PATCH_IDENTITY,
)
"""The evidence kinds only a ``--deep`` run asks for.

The pipeline reads this to decide which rungs a run without ``--deep`` leaves
out altogether, so a run that did not ask for the comparison asks no question
about one and records no observation for it.
"""


@dataclasses.dataclass(frozen=True, slots=True)
class Twin:
    """One child commit and the target commit whose content matches it.

    ``child`` is the commit a candidate names, because gate 4 asks whether a
    candidate is an ancestor of the child and a target-side commit is not.
    ``target`` is what makes the child commit evidence at all: the window holds
    a commit carrying the same content, which says the child's work is on the
    target already.
    """

    child: str
    target: str


@dataclasses.dataclass(frozen=True, slots=True)
class Scan:
    """What one deep comparison read, and what it found.

    ``tree`` and ``patch`` are the twins each pass found, in the child's own
    order, and a child commit appears in at most one of them: the patch pass
    compares the commits the tree pass left unmatched. ``warnings`` says why the
    scan is not everything it set out to be — a window that cut it short, or a
    question the repository could not answer — and is empty for a scan that read
    the target's whole history and every child commit there was.

    """

    tree: tuple[Twin, ...] = ()
    patch: tuple[Twin, ...] = ()
    warnings: tuple[str, ...] = ()


def scan_for(graph: WheresatGraph, request: BoundaryRequest) -> Scan:
    """Return the comparison the request asked for, and nothing for one that did not.

    A run without ``--deep`` compares nothing: its two rungs are absent from
    the ladder rather than left in place to answer nothing, so taking the scan
    anyway would read the target's history for an answer no one reads. The
    request states the target, the child tip, and the window, which is why the
    comparison is taken from the request rather than from three arguments
    beside it: the window is read from the same place the report reads it, so
    the run cannot state one bound and scan another.

    Parameters
    ----------
    graph : WheresatGraph
        Read-only history questions.
    request : BoundaryRequest
        What the run set out to answer, including whether it asked for this.

    Returns
    -------
    Scan
        The twins the window holds and the caveats the comparison was taken
        under, or an empty scan for a run that did not ask for it.

    """
    if not request.deep:
        return Scan()
    return scan(
        graph,
        target=request.target,
        child_tip=request.child_tip,
        window=request.heuristic_window,
    )


def tree_candidates(scan: Scan) -> tuple[Candidate, ...]:
    """Return one inferred candidate per twin the tree pass found.

    The candidate names the *child* commit rather than the target commit that
    matched it: the twin says the child's work is on the target already, and
    gate 4 asks whether a candidate is an ancestor of the child, which no
    commit on the target's side is.

    Parameters
    ----------
    scan : Scan
        What the comparison read, and what it found.

    Returns
    -------
    tuple[Candidate, ...]
        One candidate per child commit the tree pass matched by whole-tree
        object ID, in the child's own order.

    """
    return _candidates(scan.tree, EvidenceKind.TREE_IDENTITY)


def patch_candidates(scan: Scan) -> tuple[Candidate, ...]:
    """Return one inferred candidate per twin the change pass found.

    This is the pass a tree cannot answer: a squash that had to resolve a
    conflict lands a change whose tree is not the child's, because the target
    had moved on beneath it. Only the child commits the tree pass left
    unmatched are compared, so one commit is never claimed twice.

    Parameters
    ----------
    scan : Scan
        What the comparison read, and what it found.

    Returns
    -------
    tuple[Candidate, ...]
        One candidate per child commit the change pass matched, in the child's
        own order.

    """
    return _candidates(scan.patch, EvidenceKind.PATCH_IDENTITY)


def _candidates(
    twins: cabc.Sequence[Twin], kind: EvidenceKind
) -> tuple[Candidate, ...]:
    """Return one candidate per twin, named by the commit that matched it."""
    return tuple(_candidate(twin, kind) for twin in twins)


def _candidate(twin: Twin, kind: EvidenceKind) -> Candidate:
    """Return the candidate one twin is: the child commit, named by its twin."""
    return candidate_for(twin.child, kind, source=twin_source(twin, kind))


def scan(graph: WheresatGraph, *, target: str, child_tip: str, window: int) -> Scan:
    """Return the twins the target's newest ``window`` commits hold.

    Parameters
    ----------
    graph : WheresatGraph
        Read-only history questions.
    target : str
        Commit whose newest commits are searched for a twin.
    child_tip : str
        Tip of the child branch, whose own commits are the candidates.
    window : int
        How many of the target's newest commits to compare against.

    Returns
    -------
    Scan
        The twins each pass found, and a caveat for every question the scan
        could not put. What the scan cannot answer is a caveat rather than a
        fault, because inferred evidence never establishes a boundary and a
        fault would turn ``--deep`` into the semantics switch it must not be.

    """
    if window < 1:
        caveat = f"the deep scan compared nothing: --heuristic-window is {window}"
        return Scan(warnings=(caveat,))
    try:
        listing = graph.history(target, limit=window + 1)
    except WheresatGraphError as exc:
        return Scan(warnings=(f"the deep scan could not read the target: {exc}",))
    try:
        windowed = listing[-window:]
        children = graph.commits_in_range(target, child_tip)
        by_tree = _index(windowed, graph.tree_of)
        tree, unmatched = _claim(children, by_tree, graph.tree_of)
        patch = _changed(graph, unmatched, windowed, target=target)
    except WheresatGraphError as exc:
        return Scan(warnings=(f"the deep scan could not be taken: {exc}",))
    return Scan(tree=tree, patch=patch, warnings=_caveats(listing, window))


def _changed(
    graph: WheresatGraph,
    unmatched: cabc.Sequence[str],
    windowed: cabc.Sequence[str],
    *,
    target: str,
) -> tuple[Twin, ...]:
    """Return the twins the change pass finds among the unmatched child commits.

    The window is measured only when there is something to measure it against.
    Telling what change each windowed commit introduces costs one diff per
    commit, and a tree pass that left nothing unmatched — the clean squash,
    which is the case the cheap pass exists for — has already answered the
    question the index is built to ask.

    Parameters
    ----------
    graph : WheresatGraph
        Read-only history questions.
    unmatched : collections.abc.Sequence[str]
        Child commits the tree pass claimed no twin for, oldest first.
    windowed : collections.abc.Sequence[str]
        The target's newest commits, oldest first.
    target : str
        Commit whose best common ancestor with a child commit is a base.

    Returns
    -------
    tuple[Twin, ...]
        The twins the change pass found, in the child's own order.

    """
    if not unmatched:
        return ()
    twins, _rest = _claim(
        unmatched,
        _index(windowed, functools.partial(_introduced, graph)),
        functools.partial(_accumulated, graph, target=target),
    )
    return twins


def _index(
    commits: cabc.Sequence[str], key: cabc.Callable[[str], str | None]
) -> dict[str, tuple[str, ...]]:
    """Return commits by the key each of them is looked up by later.

    A key that several commits share keeps all of them, oldest first, because
    which of them answered is decided when a child commit claims one. A commit
    with no key — a patch the diff pipeline could not identify, or a range
    carrying no hunks at all — is left out of the index rather than indexed
    under nothing, so no lookup can find it.

    Parameters
    ----------
    commits : collections.abc.Sequence[str]
        Commits to index, oldest first.
    key : collections.abc.Callable[[str], str | None]
        What one commit is looked up by, or ``None`` when it has no key.

    Returns
    -------
    dict[str, tuple[str, ...]]
        The commits each key names, oldest first.

    """
    found: dict[str, list[str]] = {}
    for commit in commits:
        identifier = key(commit)
        if identifier is not None:
            found.setdefault(identifier, []).append(commit)
    return {identifier: tuple(ones) for identifier, ones in found.items()}


def _claim(
    children: cabc.Sequence[str],
    index: typ.Mapping[str, tuple[str, ...]],
    key: cabc.Callable[[str], str | None],
) -> tuple[tuple[Twin, ...], tuple[str, ...]]:
    """Return the twins the child commits claim, and the ones claiming none.

    The newest child commit claims a twin first, so a child history that met a
    change and then reverted it reports the commit that really landed rather
    than both of them. One target commit answers for one child commit, and the
    twin a lookup takes is the newest unclaimed commit that carries the key, so
    a target that added a change and reverted it answers with the commit that
    has it now.

    Parameters
    ----------
    children : collections.abc.Sequence[str]
        Child commits to compare, oldest first.
    index : typing.Mapping[str, tuple[str, ...]]
        The window's commits, by the key each is looked up by.
    key : collections.abc.Callable[[str], str | None]
        What one child commit is looked up by, or ``None`` when it cannot be
        compared at all.

    Returns
    -------
    tuple[tuple[Twin, ...], tuple[str, ...]]
        The twins, in the child's own order, and the child commits that claimed
        none, in the same order.

    """
    claimed: set[str] = set()
    twins: dict[str, str] = {}
    for child in reversed(children):
        found = key(child)
        twin = _unclaimed(index.get(found, ()), claimed) if found else None
        if twin is None:
            continue
        twins[child] = twin
        claimed.add(twin)
    return (
        tuple(Twin(child, twins[child]) for child in children if child in twins),
        tuple(child for child in children if child not in twins),
    )


def _unclaimed(candidates: cabc.Sequence[str], claimed: cabc.Set[str]) -> str | None:
    """Return the newest candidate no child commit has claimed yet."""
    return next((one for one in reversed(candidates) if one not in claimed), None)


def _accumulated(graph: WheresatGraph, child: str, *, target: str) -> str | None:
    """Return the identifier of the change ``child`` accumulates from its fork point.

    The base is the best common ancestor of the child commit and the target,
    which is where the child's line left the target's: the change measured from
    it is the child's own work, and the whole of it, so a squash of several
    child commits is compared as one cumulative change and never commit by
    commit. A history with more than one best common ancestor takes the first
    Git reports, and an unrelated history has no base and no cumulative change
    to measure.

    Parameters
    ----------
    graph : WheresatGraph
        Read-only history questions.
    child : str
        Child commit whose accumulated change is wanted.
    target : str
        Commit whose best common ancestor with the child is the base.

    Returns
    -------
    str | None
        The patch identifier of the child's accumulated change, or ``None``
        when the range has no base or no patch to identify.

    """
    bases = graph.merge_bases(child, target)
    if not bases:
        return None
    return graph.cumulative_patch_identifier(bases[0], child)


def _introduced(graph: WheresatGraph, commit: str) -> str | None:
    """Return the identifier of the change ``commit`` introduces on its own.

    This is the other side of the patch comparison, and the two sides are
    deliberately not symmetrical: the change a squash commit introduces is
    compared against the change a child commit *accumulates*, because a squash
    is many child commits landing as one. The same side cannot be asked of a
    windowed commit — an accumulated change measured from its fork point with
    the target is the empty diff, every windowed commit being an ancestor of
    the target — so what a windowed commit offers is the change it introduces.

    A commit whose own change cannot be read is indexed under nothing, so no
    lookup finds it. That costs a twin nothing may claim, and it is not a
    fault: the window's commits were read once already, by the tree pass, so a
    commit that fails here is one with no parent to introduce anything over —
    the repository's root commit. A window that reached the root is a window
    that reached the end of the history, which is a complete scan.

    Parameters
    ----------
    graph : WheresatGraph
        Read-only history questions.
    commit : str
        Windowed commit whose own change is wanted.

    Returns
    -------
    str | None
        The patch identifier of the change the commit introduces, or ``None``
        when there is no such change to identify.

    """
    try:
        return graph.cumulative_patch_identifier(f"{commit}^", commit)
    except WheresatGraphError:
        return None


def _caveats(listing: cabc.Sequence[str], window: int) -> tuple[str, ...]:
    """Return what the window left uncompared, when it left anything.

    The listing was asked for one commit more than the window holds, so a
    listing longer than the window proves a commit older than it exists: the
    scan compared the newest commits there are and not all the commits there
    are. A listing no longer than the window is the target's whole history,
    which is complete and has nothing to warn about.

    Parameters
    ----------
    listing : collections.abc.Sequence[str]
        The target's newest commits, oldest first, as the scan asked for them.
    window : int
        How many of the newest commits the scan compared.

    Returns
    -------
    tuple[str, ...]
        The caveat naming the window, or nothing at all when the scan read the
        whole history.

    """
    if len(listing) <= window:
        return ()
    caveat = (
        f"the deep scan compared the target's newest {window} commits, so a "
        f"twin below that window was not looked for"
    )
    return (caveat,)


def twin_source(twin: Twin, kind: EvidenceKind) -> str:
    """Return what one twin's candidate names as the source of its evidence.

    The twin's target commit is part of the answer rather than a detail of the
    scan: a report that named the child commit and nothing else would leave a
    reader unable to tell a copy of the trunk's newest commit from a copy of one
    two hundred commits down.

    Parameters
    ----------
    twin : Twin
        The child commit and the target commit that matched it.
    kind : EvidenceKind
        Which comparison found the twin.

    Returns
    -------
    str
        The comparison and the commit it matched, the commit abbreviated as a
        detail line abbreviates every other commit.

    """
    return f"{_SOURCE_WORDS[kind]} with {twin.target[:COMMIT_ABBREVIATION]}"
