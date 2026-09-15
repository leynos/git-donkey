"""The deep comparison: which child commits the target's window already holds.

The comparison is a lookup in two passes, so this suite is mostly a table of
small histories: one row per way a child commit can meet a windowed one, and
one row per way the comparison can fail to cover what it set out to. Every row
states the two histories, the trees, and the patch identifiers, and asks what
the scan made of them, because what the scan returns is the whole of what the
rungs above it can say — a candidate it does not name is a boundary the run
does not propose.

Two properties are what the rows are chosen for. The comparison never claims
more than it read: a window that cut the scan short, an empty window, and a
history the repository would not answer all end in a caveat rather than in a
twin, and a caveat never reaches the verdict. And the two passes are not the
same comparison asked twice: the tree pass compares content that is equal, the
patch pass compares the change a child commit accumulates with the change a
windowed commit introduces, which is the asymmetry a squash depends on.

The command's own wiring — which rungs a run without ``--deep`` reads, and
where the caveats are rendered — is pinned by the command-line suites in
``tests/integration``, because that is where a run's options are.

"""

from __future__ import annotations

import dataclasses
import typing as typ

from git_donkey import wheresat_deep
from git_donkey.wheresat_errors import ShallowHistoryError, WheresatGraphError
from git_donkey.wheresat_records import EvidenceKind

if typ.TYPE_CHECKING:
    from git_donkey.wheresat_graph import WheresatGraph

# The target the run is replaying onto, the newest three commits of its
# history, and the two child commits the cases compare against them. Every
# commit is a repeated digit so a failing example names the one it meant at a
# glance.
_TARGET = "7" * 40
_WINDOW_OLDEST = "3" * 40
_WINDOW_MIDDLE = "4" * 40
_WINDOW_NEWEST = "5" * 40
_CHILD_BELOW = "2" * 40
_CHILD_TIP = "1" * 40

# Two trees and two patch identifiers that differ, so a case can make two
# commits agree or disagree about either one without saying anything else.
_TREE_ONE = "a" * 40
_TREE_TWO = "b" * 40
_PATCH_ONE = "c" * 40
_PATCH_TWO = "d" * 40

# The window the cases scan with, which is wider than any history they build: a
# case that is not about the bound is a case in which the bound did not bite.
_WIDE = 200


@dataclasses.dataclass(frozen=True, slots=True)
class _Graph:
    """A graph whose history answers the case supplies, recording its questions.

    Only the questions the comparison puts are implemented — a history listing,
    a range, a tree, a merge base, and a cumulative patch identifier — because
    the double is cast to the port rather than completed: a comparison that
    reached for another question would fail against it rather than pass
    quietly.

    Attributes
    ----------
    commits : tuple[str, ...]
        The target's history, oldest first, as the real reader returns it.
    children : tuple[str, ...]
        The child branch's own commits, oldest first, as the range returns them.
    trees : collections.abc.Mapping[str, str]
        Tree object ID per commit. A commit the case left out has no tree, and
        asking for one is a question the repository could not answer.
    bases : collections.abc.Mapping[str, tuple[str, ...]]
        Best common ancestors per child commit, or nothing at all for a child
        whose history is unrelated to the target's.
    patches : collections.abc.Mapping[str, str | None]
        Patch identifier per commit, as the diff pipeline would report it. A
        commit the case left out has no patch to identify.
    refusals : collections.abc.Mapping[str, Exception]
        Failure to raise per question, named by the question: ``history``,
        ``range``, ``tree``, ``bases``, or ``patch``.
    limits : list[int | None]
        The ``limit`` of every history read, in the order the reads were made.
    ranges : list[tuple[str, str]]
        The ``(exclude, include)`` of every range read.
    merges : list[tuple[str, str]]
        The ``(left, right)`` of every merge base asked for.
    diffs : list[tuple[str, str]]
        The ``(base, tip)`` of every cumulative patch asked for.

    """

    commits: tuple[str, ...] = ()
    children: tuple[str, ...] = ()
    trees: typ.Mapping[str, str] = dataclasses.field(default_factory=dict)
    bases: typ.Mapping[str, tuple[str, ...]] = dataclasses.field(default_factory=dict)
    patches: typ.Mapping[str, str | None] = dataclasses.field(default_factory=dict)
    refusals: typ.Mapping[str, Exception] = dataclasses.field(default_factory=dict)
    limits: list[int | None] = dataclasses.field(default_factory=list)
    ranges: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    merges: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    diffs: list[tuple[str, str]] = dataclasses.field(default_factory=list)

    def __post_init__(self) -> None:
        """Give every commit the case named no tree for one no other commit has.

        A case about the change pass should not have to describe trees at all,
        and two commits carry one tree exactly when the case says they do: the
        generated tree comes from the commit's position in the two histories,
        so it is a tree of its own for every commit and the same one whenever
        the case is built again.
        """
        generated = {
            commit: f"{index:040x}"
            for index, commit in enumerate((*self.commits, *self.children), start=1)
        }
        object.__setattr__(self, "trees", generated | dict(self.trees))

    def _refuse(self, question: str) -> None:
        """Raise the failure the case named for ``question``, if it named one."""
        refusal = self.refusals.get(question)
        if refusal is not None:
            raise refusal

    def history(self, rev: str, *, limit: int | None = None) -> tuple[str, ...]:
        """Return the newest commits of the history this double holds."""
        self._refuse("history")
        self.limits.append(limit)
        if limit is None:
            return self.commits
        return self.commits[-limit:]

    def commits_in_range(
        self,
        exclude: str,
        include: str,
        *,
        not_reachable_from: str | None = None,
    ) -> tuple[str, ...]:
        """Return the child's own commits, which the case supplies."""
        self._refuse("range")
        self.ranges.append((exclude, include))
        return self.children

    def tree_of(self, rev: str) -> str:
        """Return the tree the case gave ``rev``, or refuse the question."""
        self._refuse("tree")
        try:
            return self.trees[rev]
        except KeyError as exc:
            msg = f"cannot read the tree of {rev}"
            raise WheresatGraphError(msg) from exc

    def merge_bases(self, left: str, right: str) -> tuple[str, ...]:
        """Return the best common ancestors the case gave ``left``."""
        self._refuse("bases")
        self.merges.append((left, right))
        return self.bases.get(left, ())

    def cumulative_patch_identifier(self, base: str, tip: str) -> str | None:
        """Return the patch identifier the case gave ``tip``."""
        self._refuse("patch")
        self.diffs.append((base, tip))
        return self.patches.get(tip)


def _scan(graph: _Graph, *, window: int = _WIDE) -> wheresat_deep.Scan:
    """Return what the comparison makes of the double's two histories."""
    return wheresat_deep.scan(
        typ.cast("WheresatGraph", graph),
        target=_TARGET,
        child_tip=_CHILD_TIP,
        window=window,
    )


def _twin(child: str, target: str) -> wheresat_deep.Twin:
    """Return the twin a case expects the comparison to report."""
    return wheresat_deep.Twin(child=child, target=target)


def test_a_shared_tree_names_the_child_commit_and_the_commit_it_matched() -> None:
    """A child commit whose whole tree the window holds is a twin of it."""
    graph = _Graph(
        commits=(_WINDOW_OLDEST, _WINDOW_NEWEST),
        children=(_CHILD_TIP,),
        trees={_CHILD_TIP: _TREE_ONE, _WINDOW_NEWEST: _TREE_ONE},
    )

    found = _scan(graph)

    assert found.tree == (_twin(_CHILD_TIP, _WINDOW_NEWEST),), (
        "the candidate is the child commit whose content the target holds"
    )
    assert not found.patch, "a commit the tree pass matched is not compared again"
    assert not graph.diffs, (
        "the change pass is not even begun: the tree pass left nothing to ask it"
    )
    assert not found.warnings, "a history this short is a complete comparison"


def test_the_newest_child_commit_claims_a_shared_twin() -> None:
    """Two child commits carrying one tree leave the older one unclaimed."""
    graph = _Graph(
        commits=(_WINDOW_NEWEST,),
        children=(_CHILD_BELOW, _CHILD_TIP),
        trees={
            _CHILD_BELOW: _TREE_ONE,
            _CHILD_TIP: _TREE_ONE,
            _WINDOW_NEWEST: _TREE_ONE,
        },
    )

    found = _scan(graph)

    assert found.tree == (_twin(_CHILD_TIP, _WINDOW_NEWEST),), (
        "the newest child commit is the one that claims the twin, so a child "
        "history that met a change and reverted it reports the landing commit"
    )


def test_one_window_commit_answers_for_one_child_commit() -> None:
    """Two child commits claiming one change take the two newest twins."""
    graph = _Graph(
        commits=(_WINDOW_MIDDLE, _WINDOW_NEWEST),
        children=(_CHILD_BELOW, _CHILD_TIP),
        trees={
            _CHILD_BELOW: _TREE_ONE,
            _CHILD_TIP: _TREE_ONE,
            _WINDOW_MIDDLE: _TREE_ONE,
            _WINDOW_NEWEST: _TREE_ONE,
        },
    )

    found = _scan(graph)

    assert found.tree == (
        _twin(_CHILD_BELOW, _WINDOW_MIDDLE),
        _twin(_CHILD_TIP, _WINDOW_NEWEST),
    ), (
        "the newest child takes the newest twin, and the two are not the same "
        "one; the twins are then reported in the child's own order, because "
        "that is the order every other rung reports its candidates in"
    )


def test_an_unmatched_child_commit_is_compared_by_its_own_change() -> None:
    """A child commit no tree matched is looked for by its cumulative change."""
    graph = _Graph(
        commits=(_WINDOW_NEWEST,),
        children=(_CHILD_TIP,),
        patches={_CHILD_TIP: _PATCH_ONE, _WINDOW_NEWEST: _PATCH_ONE},
        bases={_CHILD_TIP: (_WINDOW_OLDEST,)},
    )

    found = _scan(graph)

    assert not found.tree, "the two trees differ, so no tree names this twin"
    assert found.patch == (_twin(_CHILD_TIP, _WINDOW_NEWEST),), (
        "the change the child accumulates is the change the window commit "
        "introduces, so the two are twins however far apart their trees are"
    )


def test_the_two_sides_of_the_patch_comparison_are_different_diffs() -> None:
    """The two sides of the comparison measure their change from different commits.

    The child's change is measured from the child commit's fork point with the
    target, and the windowed commit's from its own parent.
    """
    graph = _Graph(
        commits=(_WINDOW_NEWEST,),
        children=(_CHILD_TIP,),
        patches={_CHILD_TIP: _PATCH_ONE},
        bases={_CHILD_TIP: (_WINDOW_OLDEST,)},
    )

    _scan(graph)

    assert graph.diffs == [
        (f"{_WINDOW_NEWEST}^", _WINDOW_NEWEST),
        (_WINDOW_OLDEST, _CHILD_TIP),
    ], (
        "an accumulated change measured for a windowed commit would be the "
        "empty diff, because every windowed commit is an ancestor of the target"
    )


def test_a_child_commit_the_tree_pass_matched_is_not_compared_by_change() -> None:
    """The expensive pass reads only what the cheap one could not settle."""
    graph = _Graph(
        commits=(_WINDOW_NEWEST,),
        children=(_CHILD_TIP,),
        trees={_CHILD_TIP: _TREE_ONE, _WINDOW_NEWEST: _TREE_ONE},
        patches={_CHILD_TIP: _PATCH_TWO, _WINDOW_NEWEST: _PATCH_TWO},
        bases={_CHILD_TIP: (_WINDOW_OLDEST,)},
    )

    found = _scan(graph)

    assert not found.patch, (
        "the child commit is already claimed, so its change is not looked for "
        "a second time"
    )
    assert not graph.diffs, (
        "a matched child commit costs not even the window's own measures: the "
        "change pass is only begun for a commit the tree pass left unmatched"
    )


def test_an_unrelated_history_has_no_change_to_compare() -> None:
    """A child with no best common ancestor is compared by tree and no further."""
    graph = _Graph(
        commits=(_WINDOW_NEWEST,),
        children=(_CHILD_TIP,),
        patches={_CHILD_TIP: _PATCH_ONE, _WINDOW_NEWEST: _PATCH_ONE},
    )

    found = _scan(graph)

    assert not found.tree, "the histories share no tree, so the tree pass names no twin"
    assert not found.patch, (
        "and no change to measure one from, because neither has a base"
    )
    assert (f"{_WINDOW_NEWEST}^", _WINDOW_NEWEST) in graph.diffs, (
        "the window's own change is still read, because the window is readable"
    )
    assert (_TARGET, _CHILD_TIP) not in graph.diffs, (
        "no cumulative diff is asked for over a range with no base"
    )
    assert not found.warnings, "a base that does not exist is not a failure to read"


def test_the_target_is_read_once_and_asks_for_one_commit_more_than_the_window() -> None:
    """The window is read in one listing, and the listing can tell it was cut."""
    graph = _Graph(
        commits=(_WINDOW_OLDEST, _WINDOW_MIDDLE, _WINDOW_NEWEST),
        children=(_CHILD_TIP,),
    )

    _scan(graph, window=2)

    assert graph.limits == [3], (
        "one commit more than the window is asked for, which is how a window "
        "shorter than the history is told from one that reached the end"
    )
    assert graph.ranges == [(_TARGET, _CHILD_TIP)], (
        "the child's own commits are the ones reachable from the child and not "
        "from the target"
    )


def test_a_window_that_cut_the_scan_short_is_a_caveat() -> None:
    """A twin below the window was not looked for, and the run says so."""
    graph = _Graph(
        commits=(_WINDOW_OLDEST, _WINDOW_MIDDLE, _WINDOW_NEWEST),
        children=(_CHILD_TIP,),
        trees={_CHILD_TIP: _TREE_ONE, _WINDOW_OLDEST: _TREE_ONE},
    )

    found = _scan(graph, window=2)

    assert not found.tree, "the commit the child matches is below the window"
    assert len(found.warnings) == 1, "the window is stated once"
    assert "2" in found.warnings[0], "the caveat names the window that was scanned"


def test_a_window_that_reached_the_end_of_the_history_is_no_caveat() -> None:
    """A comparison that read the whole history is complete, not cut short."""
    graph = _Graph(
        commits=(_WINDOW_OLDEST, _WINDOW_MIDDLE, _WINDOW_NEWEST),
        children=(_CHILD_TIP,),
        trees={_CHILD_TIP: _TREE_ONE, _WINDOW_OLDEST: _TREE_ONE},
    )

    found = _scan(graph, window=3)

    assert found.tree == (_twin(_CHILD_TIP, _WINDOW_OLDEST),), (
        "the whole history is compared, so a twin anywhere in it is found"
    )
    assert not found.warnings, "there is nothing below a window that reached the end"


def test_a_window_of_nothing_compares_nothing_and_reads_nothing() -> None:
    """A window no commit fits in is a caveat, and the history is not read."""
    graph = _Graph(commits=(_WINDOW_NEWEST,), children=(_CHILD_TIP,))

    found = _scan(graph, window=0)

    assert not found.tree, "nothing was compared, so no twin was found"
    assert not found.patch, "neither pass found anything to report"
    assert not graph.limits, (
        "not one question is put to the repository for a comparison with no "
        "window, so the target's history is not even listed"
    )
    assert not graph.ranges, "and the child's own commits are not listed either"
    assert len(found.warnings) == 1, "the empty window is stated once"
    assert "0" in found.warnings[0], "the caveat names the window that was scanned"


def test_a_target_the_repository_would_not_list_is_a_caveat() -> None:
    """A history the repository refuses is a caveat, never a fault."""
    graph = _Graph(
        commits=(_WINDOW_NEWEST,),
        children=(_CHILD_TIP,),
        refusals={"history": ShallowHistoryError("graft: deepen the clone and retry")},
    )

    found = _scan(graph)

    assert not found.tree, "the list the repository refused names no twin"
    assert not found.patch, "and there is no window to compare a change against"
    assert len(found.warnings) == 1, "the refusal is carried once"
    assert "deepen the clone" in found.warnings[0], (
        "what the repository said is the whole of what the operator has to act on"
    )


def test_a_child_the_repository_would_not_list_is_a_caveat() -> None:
    """A child history the repository refuses is a caveat as well."""
    graph = _Graph(
        commits=(_WINDOW_NEWEST,),
        children=(_CHILD_TIP,),
        refusals={"range": WheresatGraphError("cannot list the child's commits")},
    )

    found = _scan(graph)

    assert not found.tree, "the child history the repository refused names no twin"
    assert not found.patch, "and no change pass is reached without one"
    assert len(found.warnings) == 1, "the refusal is carried once"
    assert "cannot list" in found.warnings[0], (
        "the caveat quotes what the repository said, as every fault does"
    )


def test_a_twin_names_the_comparison_that_found_it_and_the_commit_it_matched() -> None:
    """A candidate's source says which comparison, and which commit, it was."""
    twin = _twin(_CHILD_TIP, _WINDOW_NEWEST)

    tree = wheresat_deep.twin_source(twin, EvidenceKind.TREE_IDENTITY)
    patch = wheresat_deep.twin_source(twin, EvidenceKind.PATCH_IDENTITY)

    assert tree == f"tree identity with {_WINDOW_NEWEST[:7]}", (
        "the source names the commit the child's content matched, abbreviated "
        "as every other detail line abbreviates one"
    )
    assert patch == f"patch identity with {_WINDOW_NEWEST[:7]}", (
        "the two comparisons are told apart by the word in front of the commit"
    )
