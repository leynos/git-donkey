"""INV-6's real half: six repositories whose histories a boundary must split.

The generated half of INV-6 ranges over synthetic graph data; this half asks
the read-only port the same question about repositories Git built, because a
graph the test constructed cannot exhibit a graft, a criss-cross, or a parent
whose history was rewritten. Each shape is built once, and every claim is then
computed independently of the port: the test runs ``git rev-list`` itself, in
its own invocation, over the same commits.

What INV-6 asks for is a partition, and it is asserted as one.
``OLD_BASE..child_tip`` holds every commit reachable from the child's tip and
not from the boundary, holds nothing reachable from the boundary, and never
holds the boundary itself; the two halves together are the child's whole
history, so nothing is lost between them. A mutant that listed an inclusive
range would put the boundary in the listing and fail the first of those.

The second of the module's questions is ``not_reachable_from``, which
subtracts a third commit's history from the listing. Two shapes pass one: the
advanced-parent shape, where the commit asked about reaches nothing the
boundary had not already excluded, and the criss-cross, where it reaches into
the middle of the range. Both are asserted against ``git rev-list`` computed
here, so the parameter's effect is pinned rather than described.

The module ends with the shallow case, which is where a range listing is not
an answer at all. Depth-1 fetches leave a repository in which
``git merge-base --is-ancestor`` reports *no* for a commit that is an ancestor
and ``git rev-list`` lists a range that stops at the graft as though it were
the whole range, so the ancestry question returns ``UNKNOWN`` rather than the
false negative, and the questions with no value for "could not tell" refuse.
The measurements behind that rule are recorded in
``docs/execplans/git-wheresat-sub-command.md``.

Marked with a longer timeout than the suite default because the six shapes
build real repositories, and because the generated ``Repo`` objects of a
module-scoped fixture are shared by every test that reads them.
"""

from __future__ import annotations

import dataclasses
import typing as typ

import pytest
from git import GitCommandError, Repo

from git_donkey.wheresat_errors import WheresatGraphError
from git_donkey.wheresat_graph import GitWheresatGraph
from git_donkey.wheresat_records import Ancestry
from tests import git_repo_helpers

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

pytestmark = pytest.mark.timeout(120)


@dataclasses.dataclass(frozen=True, slots=True)
class CrossBases:
    """The four commits a criss-cross is built from and the two it produces.

    Both merge commits have the same two parents, which is what makes two
    commits the best common ancestors instead of one, and neither merge is an
    ancestor of the other, so neither can be the answer to the question the
    pair asks.
    """

    left_tip: str
    right_tip: str
    left_merge: str
    right_merge: str


@dataclasses.dataclass(frozen=True, slots=True)
class Shape:
    """One repository and the range question asked about it.

    ``third`` is the commit handed to the port's ``not_reachable_from``
    parameter, and ``third_overlaps`` records whether that commit's history
    reaches into the range at all: a commit the range never held must leave the
    listing alone, and one the range does hold must remove exactly its history.
    The flag is a claim about the fixture, so the test checks it against the
    intersection it computes rather than trusting it.
    """

    label: str
    repo: Repo
    boundary: str
    child_tip: str
    third: str | None = None
    third_overlaps: bool = False
    cross: CrossBases | None = None


def _history(repo: Repo, rev: str) -> frozenset[str]:
    """Return every commit reachable from ``rev``, read by this test's own Git.

    Parameters
    ----------
    repo : Repo
        Repository to read.
    rev : str
        Revision to list the ancestry of.

    Returns
    -------
    frozenset[str]
        The commit ``rev`` names and every ancestor of it.

    """
    return frozenset(repo.git.rev_list("--end-of-options", rev).split())


def _linear(root: Path) -> Shape:
    """Build three commits on one branch and ask about the oldest of them."""
    repo = git_repo_helpers.seed_repo(root)
    boundary = repo.head.commit.hexsha
    git_repo_helpers.advance(repo, message="Second commit")
    child_tip = git_repo_helpers.advance(repo, message="Third commit")
    return Shape("linear", repo, boundary, child_tip)


def _forked_then_linear(root: Path) -> Shape:
    """Fork a child from a base the trunk then left behind in a straight line.

    The boundary is a fork point rather than a commit of the child's own line,
    and the trunk's later commit is in neither the range nor the child's
    history at all.

    Returns
    -------
    Shape
        The shape whose boundary is the fork point the trunk left behind.

    """
    repo = git_repo_helpers.seed_repo(root)
    fork_point = repo.head.commit.hexsha
    repo.git.checkout("-b", "child")
    child_tip = git_repo_helpers.advance(repo, message="Child work")
    repo.git.checkout("main")
    git_repo_helpers.advance(repo, message="Trunk work")
    return Shape("forked-then-linear", repo, fork_point, child_tip)


def _advanced_parent(root: Path) -> Shape:
    """Ask about the head a child inherited from a parent that moved on.

    The boundary is the commit the child was cut from, which is no longer the
    parent's head. The parent's head is passed as the third commit to subtract,
    and reaches nothing the boundary had not already excluded.

    Returns
    -------
    Shape
        The shape whose boundary is the commit the child inherited.

    """
    fixture = git_repo_helpers.advanced_parent_stack(root)
    return Shape(
        "advanced-parent",
        fixture.repo,
        fixture.inherited_head,
        fixture.child_tip,
        third=fixture.parent_head,
    )


def _rewritten_parent(root: Path) -> Shape:
    """Ask about a boundary whose parent branch was rebuilt with new IDs.

    The child still reaches the head it inherited, so the boundary is a fact
    about the repository, while the parent's head shares nothing with the child
    above the trunk. That is why this shape's boundary is the one the command
    has to refuse, and it is why the range must still partition cleanly.

    Returns
    -------
    Shape
        The shape whose parent branch was rebuilt with new object IDs.

    """
    fixture = git_repo_helpers.rewritten_parent_stack(root)
    return Shape(
        "rewritten-parent",
        fixture.repo,
        fixture.inherited_head,
        fixture.child_tip,
        third=fixture.parent_head,
    )


def _empty_replay_range(root: Path) -> Shape:
    """Ask about a child with nothing above its boundary.

    The boundary and the child's tip are one commit, so the range is empty and
    the child's whole history is the excluded half. An inclusive listing would
    report the boundary itself here and nowhere be more visibly wrong.

    Returns
    -------
    Shape
        The shape whose boundary and child tip are the same commit.

    """
    repo = git_repo_helpers.seed_repo(root)
    tip = git_repo_helpers.advance(repo, message="The child's only work")
    repo.git.branch("child", tip)
    return Shape("empty-replay-range", repo, tip, tip)


def _criss_cross(root: Path) -> Shape:
    """Cross two branches' merges so the pair has two best common ancestors.

    Both merge commits take the two branch tips as parents, one after the
    other, so each tip is a common ancestor of the pair and neither is an
    ancestor of the other. The right tip is passed as the third commit, and
    reaches into the middle of the range rather than merely to its edge.

    Returns
    -------
    Shape
        The shape whose crossed merges leave two best common ancestors.

    """
    repo = git_repo_helpers.seed_repo(root)
    base = repo.head.commit.hexsha
    repo.git.checkout("-b", "left", base)
    left_tip = git_repo_helpers.advance(repo, message="Left work")
    repo.git.checkout("-b", "right", base)
    right_tip = git_repo_helpers.advance(repo, message="Right work")
    repo.git.checkout("left")
    repo.git.merge("--no-ff", "-m", "Merge right into left", "right")
    left_merge = repo.head.commit.hexsha
    repo.git.checkout("right")
    repo.git.merge("--no-ff", "-m", "Merge left into right", left_tip)
    right_merge = repo.head.commit.hexsha
    return Shape(
        "criss-cross",
        repo,
        base,
        right_merge,
        third=right_tip,
        third_overlaps=True,
        cross=CrossBases(left_tip, right_tip, left_merge, right_merge),
    )


_BUILDERS: typ.Final[cabc.Mapping[str, typ.Callable[[Path], Shape]]] = {
    "linear": _linear,
    "forked-then-linear": _forked_then_linear,
    "advanced-parent": _advanced_parent,
    "rewritten-parent": _rewritten_parent,
    "empty-replay-range": _empty_replay_range,
    "criss-cross": _criss_cross,
}

_SHAPE_NAMES: typ.Final[tuple[str, ...]] = tuple(_BUILDERS)
"""The six shapes INV-6 names, built once each and asked about by every test."""


@pytest.fixture(scope="module")
def shapes(tmp_path_factory: pytest.TempPathFactory) -> cabc.Mapping[str, Shape]:
    """Build each shape once for the whole module."""
    root = tmp_path_factory.mktemp("wheresat-ranges")
    return {name: _BUILDERS[name](root / name) for name in _SHAPE_NAMES}


@pytest.fixture(scope="module")
def graph(shapes: cabc.Mapping[str, Shape]) -> cabc.Mapping[str, GitWheresatGraph]:
    """Return the read-only port over each shape's repository."""
    return {name: GitWheresatGraph(shape.repo) for name, shape in shapes.items()}


@pytest.mark.parametrize("name", _SHAPE_NAMES)
def test_the_range_partitions_the_childs_history(
    shapes: cabc.Mapping[str, Shape],
    graph: cabc.Mapping[str, GitWheresatGraph],
    name: str,
) -> None:
    """INV-6: the boundary divides the child's history and loses nothing."""
    shape = shapes[name]
    child_history = _history(shape.repo, shape.child_tip)
    boundary_history = _history(shape.repo, shape.boundary)
    listed = set(graph[name].commits_in_range(shape.boundary, shape.child_tip))
    excluded = child_history & boundary_history

    assert listed == child_history - boundary_history, (
        "the range holds exactly the child's history that the boundary's "
        "history does not"
    )
    assert listed == set(
        shape.repo.git.rev_list(
            "--end-of-options", f"{shape.boundary}..{shape.child_tip}"
        ).split()
    ), "and Git's own listing of the same range agrees, computed here"
    assert shape.boundary not in listed, "the boundary is excluded, not included"
    assert listed & excluded == set(), (
        "a commit cannot be on both sides of the boundary"
    )
    assert listed | excluded == child_history, (
        "the two halves are the child's whole history, with nothing lost between them"
    )


@pytest.mark.parametrize("name", _SHAPE_NAMES)
def test_the_range_is_listed_oldest_first(
    shapes: cabc.Mapping[str, Shape],
    graph: cabc.Mapping[str, GitWheresatGraph],
    name: str,
) -> None:
    """The listing runs in the order a replay would apply it."""
    shape = shapes[name]
    listed = graph[name].commits_in_range(shape.boundary, shape.child_tip)
    assert list(listed) == list(
        shape.repo.git.rev_list(
            "--reverse", "--end-of-options", f"{shape.boundary}..{shape.child_tip}"
        ).split()
    ), "the range is the one Git lists, oldest first"


@pytest.mark.parametrize("name", _SHAPE_NAMES)
def test_the_history_limit_keeps_the_newest_commits(
    shapes: cabc.Mapping[str, Shape],
    graph: cabc.Mapping[str, GitWheresatGraph],
    name: str,
) -> None:
    """A bounded history is the tip's own commits, and Git's grammar is held to.

    The bound reaches Git as an option and the revision as the argument after
    ``--end-of-options``, so an option passed on the wrong side of that
    separator is refused by Git rather than quietly obeyed. That fault is
    invisible to a fake graph, which answers whatever it is asked; this case is
    what puts the question to a command line at all, and it asserts the listing
    against ``git rev-list`` run here rather than against the port's own words.
    """
    shape = shapes[name]
    whole = graph[name].history(shape.child_tip)
    assert (
        list(whole)
        == shape.repo.git.rev_list(
            "--reverse", "--end-of-options", shape.child_tip
        ).split()
    ), "an unbounded history is the whole listing, oldest first"
    limit = len(whole) - 1
    assert limit > 0, (
        "the shape must have a history longer than the bound, or a bound that "
        "was ignored would pass for one that was applied"
    )
    bounded = graph[name].history(shape.child_tip, limit=limit)

    assert (
        list(bounded)
        == shape.repo.git.rev_list(
            "--reverse", f"--max-count={limit}", "--end-of-options", shape.child_tip
        ).split()
    ), "the bounded listing is the one Git computes for the same bound"
    assert list(bounded) == list(whole[-limit:]), (
        "the bound keeps the commits nearest the tip, not the oldest ones"
    )
    assert whole[0] not in bounded, "and the oldest commit is what it drops"


@pytest.mark.parametrize("name", _SHAPE_NAMES)
def test_subtracting_a_third_commit_removes_exactly_its_history(
    shapes: cabc.Mapping[str, Shape],
    graph: cabc.Mapping[str, GitWheresatGraph],
    name: str,
) -> None:
    """``not_reachable_from`` subtracts a third commit, and nothing else."""
    shape = shapes[name]
    listed = set(graph[name].commits_in_range(shape.boundary, shape.child_tip))
    overlap = listed & _history(shape.repo, shape.third) if shape.third else set()
    assert bool(overlap) == shape.third_overlaps, (
        "the fixture's claim about whether the third commit reaches into the "
        "range must be the one the repository shows"
    )
    if shape.third is None:
        return
    narrowed = set(
        graph[name].commits_in_range(
            shape.boundary, shape.child_tip, not_reachable_from=shape.third
        )
    )
    assert narrowed == listed - _history(shape.repo, shape.third), (
        "the narrowed range is the original minus the third commit's history"
    )


def test_the_corpus_holds_the_shapes_the_invariant_needs(
    shapes: cabc.Mapping[str, Shape],
) -> None:
    """Non-vacuity: the corpus contains what INV-6 says it must."""
    assert set(shapes) == set(_SHAPE_NAMES), (
        "the fixture built every shape the corpus names"
    )
    exclusions = {
        name: _history(shape.repo, shape.child_tip)
        & _history(shape.repo, shape.boundary)
        for name, shape in shapes.items()
    }
    assert any(exclusions.values()), (
        "at least one shape must exclude something, or the partition's empty "
        "half would pass for the invariant"
    )
    assert any(shape.third_overlaps for shape in shapes.values()), (
        "at least one shape must have a third commit that reaches into the "
        "range, or subtracting one would only ever be a no-op"
    )
    assert any(shape.third is not None for shape in shapes.values()), (
        "and at least one shape must pass a third commit at all"
    )


def test_a_criss_cross_has_two_best_common_ancestors(
    shapes: cabc.Mapping[str, Shape],
    graph: cabc.Mapping[str, GitWheresatGraph],
) -> None:
    """Both bases are reported, so a port that kept the first would fail here.

    The two commits are the ones the builder made rather than the ones Git
    reports, and the pair of branch tips is checked as well: those share the
    single base the two merges were built on, which is what distinguishes a
    history that crossed from one that did not.
    """
    cross = shapes["criss-cross"].cross
    assert cross is not None, "the criss-cross shape records its own commits"
    port = graph["criss-cross"]
    assert set(port.merge_bases(cross.left_merge, cross.right_merge)) == {
        cross.left_tip,
        cross.right_tip,
    }, "a crossed history has two best common ancestors, not one"
    assert set(port.merge_bases(cross.left_tip, cross.right_tip)) == {
        shapes["criss-cross"].boundary
    }, "and the tips that crossed share the single base they were cut from"


def _shallow_clone(root: Path) -> tuple[Repo, str, str, str]:
    """Clone a three-commit line at depth one from each of two refs.

    The clone holds the tip and the root and not the commit between them,
    which is the repository shape the false negative was measured in: the
    graft makes the tip look parentless, so a question about the root's
    ancestry is answered by a traversal that stops before reaching it.

    Returns
    -------
    tuple[Repo, str, str, str]
        The clone, the root, the missing middle commit, and the tip.

    """
    source = git_repo_helpers.seed_repo(root / "source")
    root_commit = source.head.commit.hexsha
    source.git.branch("old", root_commit)
    middle = git_repo_helpers.advance(source, message="The missing middle")
    tip = git_repo_helpers.advance(source, message="The tip")

    clone = Repo.init(root / "clone")
    git_repo_helpers.configure_repo(clone)
    clone.git.remote("add", "origin", (root / "source").as_posix())
    clone.git.fetch("--depth=1", "origin", "main")
    clone.git.fetch("--depth=1", "origin", "refs/heads/old:refs/remotes/origin/old")
    return clone, root_commit, middle, tip


def test_a_shallow_clone_cannot_answer_the_ancestry_question(tmp_path: Path) -> None:
    """A graft turns a *yes* into a *no*; the port must not pass that on.

    The false negative is asserted first, through Git directly, so the test
    fails if the fixture ever stops reproducing the shape that motivates the
    rule. ``UNKNOWN`` is then asserted against both of the answers a caller
    could mistake it for, and the affirmative answer is checked to survive the
    graft, since Git can only find a path that is really there.
    """
    clone, root_commit, middle, tip = _shallow_clone(tmp_path)
    assert clone.git.rev_parse("--is-shallow-repository").strip() == "true", (
        "the clone must be shallow, or nothing below is testing a graft"
    )
    assert not clone.git.cat_file("-e", f"{root_commit}^{{commit}}"), (
        "the root is present"
    )
    assert not clone.git.cat_file("-e", f"{tip}^{{commit}}"), "the tip is present"
    with pytest.raises(GitCommandError):
        clone.git.cat_file("-e", f"{middle}^{{commit}}")
    assert (
        clone.git.merge_base(
            "--is-ancestor",
            "--end-of-options",
            root_commit,
            tip,
            with_extended_output=True,
            with_exceptions=False,
        )[0]
        == 1
    ), "Git reports the root as no ancestor of the tip, although it is one"

    port = GitWheresatGraph(clone)
    assert port.is_ancestor(root_commit, tip) is Ancestry.UNKNOWN, (
        "the negative answer a graft manufactures is reported as unknown"
    )
    assert port.is_ancestor(root_commit, tip) is not Ancestry.NOT_ANCESTOR, (
        "the graft's false negative must never be passed on as an answer"
    )
    assert port.is_ancestor(tip, tip) is Ancestry.ANCESTOR, (
        "an answer Git could find is reported as it stands"
    )


def test_a_shallow_clone_refuses_the_questions_it_cannot_answer(
    tmp_path: Path,
) -> None:
    """A boundary read from a partial history would be read from the wrong cut.

    Neither of these questions has a value for "could not tell": an empty
    tuple from ``merge_bases`` reads as unrelated histories and an empty
    listing reads as a range with no work in it, and a run that believed
    either would report a boundary it has no basis for.
    """
    clone, root_commit, _, tip = _shallow_clone(tmp_path)
    port = GitWheresatGraph(clone)
    for label, question in (
        ("merge bases", lambda: port.merge_bases(root_commit, tip)),
        ("fork point", lambda: port.fork_point("refs/remotes/origin/old", tip)),
        ("range", lambda: port.commits_in_range(root_commit, tip)),
    ):
        with pytest.raises(WheresatGraphError) as refusal:
            question()
        assert "shallow" in str(refusal.value), (
            f"{label} refused for another reason: {refusal.value}"
        )


def test_a_deep_clone_answers_the_same_question_positively(tmp_path: Path) -> None:
    """The control: the downgrade is the graft's doing, not the port's."""
    source = git_repo_helpers.seed_repo(tmp_path / "source")
    root_commit = source.head.commit.hexsha
    middle = git_repo_helpers.advance(source, message="The middle")
    tip = git_repo_helpers.advance(source, message="The tip")
    port = GitWheresatGraph(source)
    assert port.is_ancestor(root_commit, tip) is Ancestry.ANCESTOR, (
        "with a full history the same commit is an ancestor"
    )
    assert port.commits_in_range(root_commit, tip) == (middle, tip), (
        "and the range is the commits between the two, oldest first"
    )
