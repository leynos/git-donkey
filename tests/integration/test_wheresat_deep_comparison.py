"""``--deep`` against repositories Git built: the comparison, its bound, its silence.

The comparison's own reasoning is pinned by ``tests/unit/test_wheresat_deep.py``
against a graph that answers what a case tells it. This suite asks the half no
double can: whether two real histories meet the way the comparison assumes they
do. A twin exists when ``git rev-parse <commit>^{tree}`` names one object for
two commits, or when the diff pipeline gives two ranges one patch identifier,
and both are Git's answers rather than a case's.

Two shapes are built, one per pass. In the first the trunk lands a child's
commit whole, so the two commits carry one tree and the cheap pass answers. In
the second the trunk lands two child commits as one squash and on top of work of
its own, so the trees differ and only the accumulated change is shared: that is
the shape the second pass exists for, and the tree pass is checked to have
failed on it rather than assumed to. Both are built with Git's own commands, so
the fixture is what Git made of the commits and not a claim about them.

Every case but one asks the run to replay onto the trunk's tip by object ID, so
the local evidence cannot settle the boundary on its own: the fork-point rung
reads a ref's reflog and an object ID names no ref, which leaves the merge base
alone and one derived candidate cannot establish anything. That state is where
the comparison's answer is visible at all — a run that established a boundary
reports its support and no candidates — so it is the state the twin is asserted
in, and the run that does establish one is asserted separately, where the flag
is required to change nothing.

Three claims about the bound are asserted beside the twins, because the window
is what keeps the comparison from being a walk of the whole trunk. A window that
reached the root of the history compares everything there is and complains about
nothing. A window that cut the scan short says so, and leaves the verdict and
the exit status exactly as they were. And a run without the flag puts no
question about the trunk's history at all, which is measured by handing the run
a graph that writes down every such question rather than by reading the report
for the absence of an answer.

"""

from __future__ import annotations

import dataclasses
import typing as typ

import pytest

from git_donkey import wheresat
from git_donkey.wheresat_graph import GitWheresatGraph
from git_donkey.wheresat_records import EvidenceKind
from tests import git_repo_helpers
from tests.integration.wheresat_helpers import (
    Status,
    WheresatRun,
    in_directory,
    run_wheresat,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    from git import Repo

pytestmark = pytest.mark.timeout(120)

TRUNK: typ.Final = "main"
"""Branch every scenario commits on, which the trunk's tip is also named by."""

CHILD: typ.Final = "child"
"""Branch the run is asked about, whose commits the comparison considers."""

_WORK: typ.Final = "work.txt"
"""File the child's own commits write, which is the work the trunk lands."""

_OTHER: typ.Final = "other.txt"
"""File a trunk commit writes of its own, which only moves the trunk's tree on."""

_WIDE: typ.Final = 200
"""The window a case asks for when the bound is not what the case is about."""


@dataclasses.dataclass(frozen=True, slots=True)
class DeepScenario:
    """A checkout whose child branch carries work the trunk also holds.

    Attributes
    ----------
    root : Path
        Working tree the run is made from, and where the repository was built.
    repo : Repo
        The repository, for the measurements a case computes itself.
    base : str
        Commit the child branch was cut from, which its work is measured from.
    child_tip : str
        Tip of the child branch, whose commits are the candidates.
    trunk_tip : str
        Tip of the trunk, which the run is asked to replay onto.
    landed : str
        Trunk commit the comparison is expected to match a child commit with.
    kind : EvidenceKind
        Which of the two comparisons is expected to find that twin. It is
        named rather than derived from the trees, because the shape a case
        builds is what decides which pass can answer: a case that asserted only
        "some twin was found" would pass on the other pass's answer.
    onto : str
        What ``--onto`` names. The builders name the trunk's tip by object ID,
        and a case that names the branch instead asks the same question about
        the same commit with a reflog for the fork-point rung to read.
    window : int
        ``--heuristic-window`` this case asks for.

    """

    root: Path
    repo: Repo
    base: str
    child_tip: str
    trunk_tip: str
    landed: str
    kind: EvidenceKind
    onto: str
    window: int = _WIDE


def _commit_work(path: Path, repo: Repo, text: str, message: str) -> str:
    """Write ``text`` to ``path``, commit it, and return the new commit.

    The file is committed through Git rather than through the index object,
    because the scenarios check branches out between commits: an index the
    repository has since rewritten on disk would otherwise be the tree the
    commit was made from. The path is staged by its name, which Git resolves
    against the working tree the repository was built in.

    Returns
    -------
    str
        The ID of the commit that wrote the file.

    """
    path.write_text(text)
    repo.git.add(path.name)
    repo.git.commit("-m", message)
    return repo.head.commit.hexsha


def _tree_twin(root: Path) -> DeepScenario:
    """Build a child whose commit the trunk landed whole, then moved past.

    The child writes a file and the trunk writes the same content in a commit of
    its own, so the two commits carry one tree. The trunk then commits something
    else, which is what puts the twin in the middle of the trunk's history
    rather than at its tip: a window narrower than that history can then be
    asked about without the twin being the newest commit inside it, which is
    what the bound's case reads.

    Returns
    -------
    DeepScenario
        The checkout, with the twin the tree pass is expected to report.

    """
    repo = git_repo_helpers.seed_repo(root)
    base = repo.head.commit.hexsha
    repo.git.checkout("-b", CHILD)
    child_tip = _commit_work(root / _WORK, repo, "the child's work", "Child work")
    repo.git.checkout(TRUNK)
    landed = _commit_work(root / _WORK, repo, "the child's work", "Land the work")
    trunk_tip = _commit_work(root / _OTHER, repo, "the trunk's own", "Later trunk work")
    return DeepScenario(
        root=root,
        repo=repo,
        base=base,
        child_tip=child_tip,
        trunk_tip=trunk_tip,
        landed=landed,
        kind=EvidenceKind.TREE_IDENTITY,
        onto=trunk_tip,
    )


def _squashed_work(root: Path) -> DeepScenario:
    """Build a child whose two commits the trunk landed as one, elsewhere.

    The child writes "first" and then "second" to one file, and the trunk commits
    a file of its own before applying the child's whole range to the index and
    committing it as one change. The trunk's commit therefore carries a tree the
    child's history does not — the trunk's file is missing from it — while the
    change it introduces is the change the child accumulated. That difference is
    what the two passes are for, and it is why the second pass is measured on
    this shape.

    Returns
    -------
    DeepScenario
        The checkout, with the twin the change pass is expected to report.

    """
    repo = git_repo_helpers.seed_repo(root)
    base = repo.head.commit.hexsha
    repo.git.checkout("-b", CHILD)
    _commit_work(root / _WORK, repo, "the child's first", "Child one")
    child_tip = _commit_work(root / _WORK, repo, "the child's second", "Child two")
    repo.git.checkout(TRUNK)
    _commit_work(root / _OTHER, repo, "the trunk's own", "Unrelated trunk work")
    repo.git.cherry_pick("--no-commit", f"{base}..{child_tip}")
    repo.git.commit("-m", "Squash the child's work")
    squash = repo.head.commit.hexsha
    return DeepScenario(
        root=root,
        repo=repo,
        base=base,
        child_tip=child_tip,
        trunk_tip=squash,
        landed=squash,
        kind=EvidenceKind.PATCH_IDENTITY,
        onto=squash,
    )


@pytest.fixture(scope="module")
def tree_twin(tmp_path_factory: pytest.TempPathFactory) -> DeepScenario:
    """Return the shape whose twin is the whole tree of one commit."""
    return _tree_twin(tmp_path_factory.mktemp("wheresat-deep-tree"))


@pytest.fixture(scope="module")
def squashed(tmp_path_factory: pytest.TempPathFactory) -> DeepScenario:
    """Return the shape whose twin is the change of one squash."""
    return _squashed_work(tmp_path_factory.mktemp("wheresat-deep-squash"))


@dataclasses.dataclass(frozen=True, slots=True)
class _CountingGraph(GitWheresatGraph):
    """The real port, with the reads of one commit's history written down.

    The comparison and the parent search both read a history through this
    method, and both ask for one commit more than their bound, so counting the
    history reads of a run would not say which of the two made them. The
    revision does: the comparison reads the target's history and the search
    reads the child's, so counting the reads of one commit counts one of them.

    The port is subclassed rather than doubled because the run puts every other
    question to it as well, and a double answering those would be a second
    implementation of the thing under test.

    Attributes
    ----------
    rev : str
        Commit whose history reads are counted.
    reads : list[int | None]
        The ``limit`` of every read of that history, in the order they were
        made.

    """

    rev: str = ""
    reads: list[int | None] = dataclasses.field(default_factory=list)

    @typ.override
    def history(self, rev: str, *, limit: int | None = None) -> tuple[str, ...]:
        """Return the history, remembering every read of the counted commit."""
        if rev == self.rev:
            self.reads.append(limit)
        return super().history(rev, limit=limit)


def _run(
    scenario: DeepScenario,
    capsys: pytest.CaptureFixture[str],
    *,
    deep: bool,
    graph: GitWheresatGraph | None = None,
) -> WheresatRun:
    """Run the command over ``scenario``, with and without the comparison.

    The run is offline, so it consults no forge and fetches nothing: what it
    reports about the child is what the repository itself holds, which is the
    half of the command this suite is about.

    Parameters
    ----------
    scenario : DeepScenario
        Checkout to run in, with the target and window the run asks for.
    capsys : pytest.CaptureFixture[str]
        Capture fixture the run's output is read from.
    deep : bool
        Whether the run is asked for the comparison.
    graph : GitWheresatGraph | None, optional
        Port the run asks its history questions through, or the repository's
        own when the case does not need to see the questions.

    Returns
    -------
    WheresatRun
        The status and both output streams.

    """
    options = wheresat.WheresatOptions(
        branch=CHILD,
        onto=scenario.onto,
        deep=deep,
        offline=True,
        json=True,
        heuristic_window=scenario.window,
    )
    capsys.readouterr()
    with in_directory(scenario.root):
        return run_wheresat(options, capsys, graph=graph)


def _candidates(run: WheresatRun) -> tuple[dict[str, object], ...]:
    """Return the candidates the run's envelope reports."""
    return tuple(typ.cast("list[dict[str, object]]", run.envelope["candidates"]))


def _reported(run: WheresatRun, kind: EvidenceKind) -> tuple[dict[str, object], ...]:
    """Return the candidates the run reported under ``kind``."""
    return tuple(one for one in _candidates(run) if one["kind"] == kind.value)


def _frozen(run: WheresatRun) -> set[tuple[tuple[str, object], ...]]:
    """Return the run's candidates as hashable rows, for comparison by value."""
    return {tuple(sorted(one.items())) for one in _candidates(run)}


def _caveats(run: WheresatRun) -> tuple[str, ...]:
    """Return the warnings the deep comparison itself contributed."""
    warnings = typ.cast("list[str]", run.envelope["warnings"])
    return tuple(one for one in warnings if "deep scan" in one)


def _tree(scenario: DeepScenario, commit: str) -> str:
    """Return the tree object ID of ``commit``, read by this test's own Git."""
    return str(scenario.repo.git.rev_parse(f"{commit}^{{tree}}"))


def _twin(scenario: DeepScenario) -> tuple[dict[str, object], ...]:
    """Return the one candidate the case's comparison is expected to report."""
    return (
        {
            "commit": scenario.child_tip,
            "kind": scenario.kind.value,
            "tier": "inferred",
            "source": f"{scenario.kind.value.replace('-', ' ')} "
            f"with {scenario.landed[:7]}",
        },
    )


def test_a_child_commit_the_trunk_landed_whole_is_a_tree_twin(
    tree_twin: DeepScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The cheap pass answers the clean landing, and names the commit it matched."""
    child_tree = _tree(tree_twin, tree_twin.child_tip)
    landed_tree = _tree(tree_twin, tree_twin.landed)

    assert child_tree == landed_tree, (
        "the fixture must give the two commits one tree, or the tree pass is "
        "being measured on a shape it could not answer"
    )
    assert tree_twin.child_tip != tree_twin.landed, (
        "and they must be two commits, or the twin would be the commit itself"
    )

    run = _run(tree_twin, capsys, deep=True)

    assert run.exit_code == Status.REFUSED, (
        "the evidence that could establish this boundary is a reflog, and the "
        "target was named by object ID, so the comparison's answer is what the "
        "run has to report"
    )
    assert _reported(run, EvidenceKind.TREE_IDENTITY) == _twin(tree_twin), (
        "the child commit is named as the candidate, and the trunk commit as "
        "the twin it was matched with"
    )
    assert not _caveats(run), (
        "a window wider than the trunk's whole history compared all of it"
    )


def test_a_child_commit_no_tree_matched_is_matched_by_its_change(
    squashed: DeepScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The expensive pass answers the squash, on a shape the cheap one could not."""
    assert _tree(squashed, squashed.child_tip) != _tree(squashed, squashed.landed), (
        "the squash must carry a tree of its own, or the tree pass would answer "
        "this shape and the change pass would never be asked"
    )
    assert squashed.repo.git.diff("--stat", squashed.base, squashed.child_tip), (
        "and the child must have work of its own, or there is no change to compare"
    )

    run = _run(squashed, capsys, deep=True)

    assert not _reported(run, EvidenceKind.TREE_IDENTITY), (
        "no trunk commit carries the child's tree, so the tree pass found nothing"
    )
    assert _reported(run, EvidenceKind.PATCH_IDENTITY) == _twin(squashed), (
        "the child's own tip is the commit whose accumulated change landed, and "
        "the trunk's commit is the one it landed as"
    )
    assert not _caveats(run), "the squash is inside a window wider than the history"


def test_a_window_that_reached_the_root_compares_it_without_complaint(
    squashed: DeepScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The window is measured over the root, which has no parent to be measured from.

    The trunk's whole history includes its root commit, and the change pass asks
    every windowed commit what change it introduces — a question the root cannot
    be asked, having no parent. The comparison swallows that refusal because the
    tree pass has already read the commit, and this case is what pins the
    swallowing: an unswallowed refusal would abandon the whole scan, and the
    twin would be reported nowhere with a caveat in its place.
    """
    whole = squashed.repo.git.rev_list("--end-of-options", squashed.trunk_tip).split()
    assert len(whole) > 1, "the trunk must have a commit above its root"

    run = _run(dataclasses.replace(squashed, window=len(whole)), capsys, deep=True)

    assert _reported(run, EvidenceKind.PATCH_IDENTITY) == _twin(squashed), (
        "the whole history is compared, root and all, and the twin is still found"
    )
    assert not _caveats(run), (
        "there is nothing below a window that reached the beginning of the history"
    )


def test_a_window_that_cut_the_scan_short_says_so_and_changes_nothing(
    tree_twin: DeepScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A twin below the window is out of the comparison, and the run reports that."""
    landed_tree = _tree(tree_twin, tree_twin.landed)
    tip_tree = _tree(tree_twin, tree_twin.trunk_tip)

    assert landed_tree != tip_tree, (
        "the newest trunk commit must carry a tree of its own, or the twin would "
        "be inside a window of one"
    )
    narrow = dataclasses.replace(tree_twin, window=1)

    run = _run(narrow, capsys, deep=True)
    whole = _run(tree_twin, capsys, deep=True)

    assert _reported(whole, EvidenceKind.TREE_IDENTITY) == _twin(tree_twin), (
        "the twin is inside the wider window, which is what the narrow one missed"
    )
    assert not _reported(run, EvidenceKind.TREE_IDENTITY), (
        "the only commit the scan compared is the trunk's newest, which the "
        "child's content does not match"
    )
    assert len(_caveats(run)) == 1, "the window that cut the scan short is stated once"
    assert "1" in _caveats(run)[0], "the caveat names the window the run asked for"
    assert not _caveats(whole), "the wider window had nothing to report"
    assert run.exit_code == whole.exit_code, (
        "a comparison that read less may not change the verdict: what it would "
        "have found is inferred evidence, and inferred evidence never establishes"
    )


def test_a_run_without_the_flag_asks_the_trunk_nothing(
    tree_twin: DeepScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The comparison is not read and not reported when the flag was not given."""
    local_graph = _CountingGraph(tree_twin.repo, rev=tree_twin.trunk_tip)
    deep_graph = _CountingGraph(tree_twin.repo, rev=tree_twin.trunk_tip)
    local = _run(tree_twin, capsys, deep=False, graph=local_graph)
    deep = _run(tree_twin, capsys, deep=True, graph=deep_graph)

    assert not local_graph.reads, (
        "a run without --deep puts no question about the trunk's history: the "
        "comparison is the only reader of it, and the flag is what asks for it"
    )
    assert deep_graph.reads == [tree_twin.window + 1], (
        "the deep run reads that history exactly once, asking for one commit "
        "more than the window, which is how a window that was cut is told from "
        "one that was not"
    )
    assert not _reported(local, EvidenceKind.TREE_IDENTITY), (
        "the local run reports none of what the comparison would have found"
    )
    assert _reported(deep, EvidenceKind.TREE_IDENTITY) == _twin(tree_twin), (
        "and the deep run reports it"
    )
    assert local.exit_code == deep.exit_code == Status.REFUSED, (
        "the two runs agree about the boundary"
    )


def test_the_two_runs_differ_in_nothing_a_verdict_rests_on(
    squashed: DeepScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """One repository, two runs, and only the candidates between their envelopes."""
    local = _run(squashed, capsys, deep=False)
    deep = _run(squashed, capsys, deep=True)

    assert local.exit_code == deep.exit_code == Status.REFUSED, (
        "the flag is a cost control and an extra rung, never a second opinion "
        "about the boundary"
    )
    differing = {
        key for key in deep.envelope if deep.envelope[key] != local.envelope[key]
    }
    assert differing == {"candidates", "gates", "reasons"}, (
        "the comparison reaches the report's evidence sections and nothing "
        "else: the verdict, the partition, the boundary it names, and the "
        "warnings are the local run's, and those are what a verdict rests on; "
        f"the runs differ in {sorted(differing)}"
    )
    assert _frozen(local) < _frozen(deep), (
        "the local run's own candidate is carried over unchanged"
    )
    assert _frozen(deep) - _frozen(local) == {
        tuple(sorted(_twin(squashed)[0].items()))
    }, "and the one the comparison added is the twin, and nothing else"


def test_an_established_run_reports_the_same_boundary_with_and_without_the_flag(
    squashed: DeepScenario,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The comparison has nothing to add to a boundary the local evidence settled.

    Naming the target by branch rather than by object ID gives the fork-point
    rung a reflog to read, which is the second derived source the merge base
    needs, so this run establishes a boundary. An established result reports its
    support and no candidates, which is why the flag changes nothing here: the
    comparison answers what the local evidence could not, and there is nothing
    here it could not answer.
    """
    named = dataclasses.replace(squashed, onto=TRUNK)

    local = _run(named, capsys, deep=False)
    deep = _run(named, capsys, deep=True)

    assert local.exit_code == deep.exit_code == Status.ESTABLISHED, (
        "the fixture must establish a boundary, or this case is the other one"
    )
    assert deep.envelope == local.envelope, (
        "with the boundary settled, the two runs' accounts are the same account"
    )
